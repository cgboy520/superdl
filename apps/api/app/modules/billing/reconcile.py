"""资金账实核对(只报不改;资金侧只写自己的核对游标 reconcile_checkpoints)。

核对三个不变式:
1. 每个用户 wallets.balance == balance_ledger 逐笔链式累计(增量扫描,断链可定位);
2. 窗口内 bills_* 出账合计 == ledger consume 合计(两侧都按账单归属期切窗);
3. consume 流水的 ref_id 必须能回连到账单行(悬挂消费 = 有扣款无出账)。

只报不改:发现差异打 error 日志 + 指标 + 管理端告警,不自动纠正账。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import String, cast, func, select, true
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.locks import LockKey, advisory_lock
from app.core.logging import get_logger
from app.core.metrics import FUND_RECONCILE_MISMATCH_TOTAL
from app.core.timeutil import billing_day_floor, now_utc
from app.modules.billing.models import (
    BalanceLedger,
    BillDailyDisk,
    BillHourly,
    ReconcileCheckpoint,
    Subscription,
    Wallet,
)

logger = get_logger(__name__)


@dataclass
class WalletMismatch:
    """钱包核对差异。kind: chain_break(逐笔断链) / balance_drift(末端快照≠余额)。"""

    user_id: int
    kind: str
    wallet_balance: Decimal
    expected: Decimal
    detail: str


async def _scan_user_chain(
    session: AsyncSession, wallet_row: Wallet, checkpoint: ReconcileCheckpoint | None
) -> tuple[WalletMismatch | None, int, Decimal]:
    """扫一个用户 checkpoint 之后的增量流水,逐笔验链。

    链式不变式:按 id 序,e.balance_after == 前一笔 balance_after + e.amount
    (无 checkpoint 时以 0.00 起算,等价于「首笔必须自洽」)。
    返回 (差异|None, 新的游标 last_ledger_id, 新的游标 balance_after)。
    """
    last_id = checkpoint.last_ledger_id if checkpoint else 0
    prev = checkpoint.balance_after if checkpoint else Decimal("0.00")
    entries = list(
        (
            await session.execute(
                select(BalanceLedger)
                .where(BalanceLedger.user_id == wallet_row.user_id, BalanceLedger.id > last_id)
                .order_by(BalanceLedger.id)
            )
        ).scalars()
    )
    for e in entries:
        want = prev + e.amount
        if e.balance_after != want:
            return (
                WalletMismatch(
                    user_id=wallet_row.user_id,
                    kind="chain_break",
                    wallet_balance=wallet_row.balance,
                    expected=want,
                    detail=(
                        f"断链于 ledger id={e.id}:balance_after={e.balance_after},"
                        f"应为 {want}(上一笔快照 {prev} + 本笔 {e.amount})"
                    ),
                ),
                last_id,
                prev,
            )
        prev = e.balance_after
    # 末端快照必须等于钱包余额(钱包被直接改/流水被删都会在这里现形)
    if prev != wallet_row.balance:
        return (
            WalletMismatch(
                user_id=wallet_row.user_id,
                kind="balance_drift",
                wallet_balance=wallet_row.balance,
                expected=prev,
                detail=(
                    f"钱包余额 {wallet_row.balance} ≠ 流水链末端快照 {prev}"
                    f"(ledger 至 id={entries[-1].id if entries else last_id})"
                ),
            ),
            last_id,
            prev,
        )
    return None, (entries[-1].id if entries else last_id), prev


async def _verify_user_once(
    sm: async_sessionmaker[AsyncSession], user_id: int
) -> WalletMismatch | None:
    """验一次;自洽则推进游标(同事务),有差异不动游标(下一轮重验,直至人工处理)。"""
    async with sm() as session:
        # t0 取库时钟、先于一切读取:之后提交的变动 wallet.updated_at > t0,下轮必被重新选中
        t0 = (await session.execute(select(func.now()))).scalar_one()
        # 候选集出自 wallets 表且行从不删,按 user_id 必命中
        wallet_row = (
            await session.execute(select(Wallet).where(Wallet.user_id == user_id))
        ).scalar_one()
        checkpoint = await session.get(ReconcileCheckpoint, user_id)
        if checkpoint is not None:
            # 游标边界行复核:被删/被改则链无法续接,直接报差(候选集的尾部探测已选中)
            boundary = await session.get(BalanceLedger, checkpoint.last_ledger_id)
            if (
                boundary is None
                or boundary.user_id != user_id
                or boundary.balance_after != checkpoint.balance_after
            ):
                return WalletMismatch(
                    user_id=user_id,
                    kind="chain_break",
                    wallet_balance=wallet_row.balance,
                    expected=checkpoint.balance_after,
                    detail=(
                        f"游标行 ledger id={checkpoint.last_ledger_id} 缺失或被改,链式校验无法续接"
                    ),
                )
        mismatch, new_last_id, new_prev = await _scan_user_chain(session, wallet_row, checkpoint)
        if mismatch is not None:
            return mismatch
        await session.execute(
            pg_insert(ReconcileCheckpoint)
            .values(
                user_id=user_id,
                last_ledger_id=new_last_id,
                balance_after=new_prev,
                updated_at=t0,
            )
            .on_conflict_do_update(
                index_elements=["user_id"],
                set_={
                    "last_ledger_id": new_last_id,
                    "balance_after": new_prev,
                    "updated_at": t0,
                },
            )
        )
        await session.commit()
        return None


async def _candidate_user_ids(session: AsyncSession) -> list[int]:
    """本轮需要核实的用户:从未核过的钱包,或游标之后钱包/流水尾部有变动的用户。

    流水尾部用 LATERAL 取各用户 id 最大一行(user_id 索引回扫,不聚合全表):
    尾部 id 大于游标 = 有新流水;小于/缺失 = 尾部行被删;balance_after 不符 = 尾行被改。
    只改流水不碰钱包的篡改因此同样会被选中核实。
    """
    tail = (
        select(
            BalanceLedger.id.label("tail_id"),
            BalanceLedger.balance_after.label("tail_after"),
        )
        .where(BalanceLedger.user_id == ReconcileCheckpoint.user_id)
        .order_by(BalanceLedger.id.desc())
        .limit(1)
        .lateral()
    )
    fresh = (
        select(Wallet.user_id)
        .outerjoin(ReconcileCheckpoint, ReconcileCheckpoint.user_id == Wallet.user_id)
        .where(ReconcileCheckpoint.user_id.is_(None))
    )
    stale = (
        select(ReconcileCheckpoint.user_id)
        .join(Wallet, Wallet.user_id == ReconcileCheckpoint.user_id)
        .outerjoin(tail, true())
        .where(
            (Wallet.updated_at > ReconcileCheckpoint.updated_at)
            | (tail.c.tail_id.is_(None))
            | (tail.c.tail_id != ReconcileCheckpoint.last_ledger_id)
            | (tail.c.tail_after != ReconcileCheckpoint.balance_after)
        )
    )
    return list((await session.execute(fresh.union(stale))).scalars())


async def wallet_ledger_chain_check(sm: async_sessionmaker[AsyncSession]) -> list[WalletMismatch]:
    """增量链式核对:只扫候选用户游标之后的新增流水,逐笔验 balance_after 链。

    在飞事务可能造成快照错位(读钱包与读流水之间有新扣款提交):首轮报差的用户
    换会话复核一次,两轮都差才上报 —— 正常扣款不会在两轮间给出同一个差异。
    """
    async with sm() as session:
        candidates = await _candidate_user_ids(session)
    mismatches: list[WalletMismatch] = []
    for user_id in candidates:
        mismatch = await _verify_user_once(sm, user_id)
        if mismatch is None:
            continue
        mismatch = await _verify_user_once(sm, user_id)  # 复核:排除在飞事务的瞬态错位
        if mismatch is not None:
            mismatches.append(mismatch)
    return mismatches


async def bills_vs_consume(
    session: AsyncSession, since: datetime, until: datetime
) -> tuple[Decimal, Decimal]:
    """窗口内 (出账合计, 消费流水合计的绝对值)。两者必须相等。

    两侧都按账单**归属期**切窗:bills 用 hour_start/day,ledger 经 ref_id 回连账单取
    归属期 —— 与入账时间(created_at)解耦。BillHourly.amount 会被补差价原地更新、
    追平补账的 created_at 落在后来某天,按 created_at 切窗在跨日/追平场景必误报。

    包周期预付是第三条腿:它不产生账单行,「出账」侧取 `subscriptions.amount_paid`,切窗用
    `subscriptions.created_at`(下单与扣款同一事务)。漏了它,`ref_type='subscription'` 的
    consume 流水就成了全无对账的一段钱。
    """
    billed_hourly = (
        await session.execute(
            select(func.coalesce(func.sum(BillHourly.amount), 0)).where(
                BillHourly.hour_start >= since, BillHourly.hour_start < until
            )
        )
    ).scalar_one()
    billed_daily = (
        await session.execute(
            select(func.coalesce(func.sum(BillDailyDisk.amount), 0)).where(
                BillDailyDisk.day >= since, BillDailyDisk.day < until
            )
        )
    ).scalar_one()
    consumed_hourly = (
        await session.execute(
            select(func.coalesce(func.sum(BalanceLedger.amount), 0))
            .where(BalanceLedger.type == "consume", BalanceLedger.ref_type == "bill_hourly")
            .join(BillHourly, BalanceLedger.ref_id == cast(BillHourly.id, String))
            .where(BillHourly.hour_start >= since, BillHourly.hour_start < until)
        )
    ).scalar_one()
    consumed_daily = (
        await session.execute(
            select(func.coalesce(func.sum(BalanceLedger.amount), 0))
            .where(BalanceLedger.type == "consume", BalanceLedger.ref_type == "bill_daily_disk")
            .join(BillDailyDisk, BalanceLedger.ref_id == cast(BillDailyDisk.id, String))
            .where(BillDailyDisk.day >= since, BillDailyDisk.day < until)
        )
    ).scalar_one()
    billed_subscription = (
        await session.execute(
            select(func.coalesce(func.sum(Subscription.amount_paid), 0)).where(
                Subscription.created_at >= since, Subscription.created_at < until
            )
        )
    ).scalar_one()
    consumed_subscription = (
        await session.execute(
            select(func.coalesce(func.sum(BalanceLedger.amount), 0))
            .where(BalanceLedger.type == "consume", BalanceLedger.ref_type == "subscription")
            .join(Subscription, BalanceLedger.ref_id == cast(Subscription.id, String))
            .where(Subscription.created_at >= since, Subscription.created_at < until)
        )
    ).scalar_one()
    billed = Decimal(billed_hourly) + Decimal(billed_daily) + Decimal(billed_subscription)
    consumed = -(
        Decimal(consumed_hourly) + Decimal(consumed_daily) + Decimal(consumed_subscription)
    )
    return billed, consumed


async def dangling_consume_refs(session: AsyncSession) -> int:
    """ref_id 回连不到账单的 consume 流水数(有扣款无出账;理论为零,手工改账除外)。"""
    hourly_dangling = (
        await session.execute(
            select(func.count())
            .select_from(BalanceLedger)
            .where(
                BalanceLedger.type == "consume",
                BalanceLedger.ref_type == "bill_hourly",
                ~select(BillHourly.id)
                .where(cast(BillHourly.id, String) == BalanceLedger.ref_id)
                .exists(),
            )
        )
    ).scalar_one()
    daily_dangling = (
        await session.execute(
            select(func.count())
            .select_from(BalanceLedger)
            .where(
                BalanceLedger.type == "consume",
                BalanceLedger.ref_type == "bill_daily_disk",
                ~select(BillDailyDisk.id)
                .where(cast(BillDailyDisk.id, String) == BalanceLedger.ref_id)
                .exists(),
            )
        )
    ).scalar_one()
    subscription_dangling = (
        await session.execute(
            select(func.count())
            .select_from(BalanceLedger)
            .where(
                BalanceLedger.type == "consume",
                BalanceLedger.ref_type == "subscription",
                ~select(Subscription.id)
                .where(cast(Subscription.id, String) == BalanceLedger.ref_id)
                .exists(),
            )
        )
    ).scalar_one()
    return int(hourly_dangling) + int(daily_dangling) + int(subscription_dangling)


async def reconcile_funds(
    sm: async_sessionmaker[AsyncSession], *, at: datetime | None = None
) -> dict[str, int]:
    """每日资金账实核对。返回 {"wallet_mismatch": n, "bill_mismatch": 0/1}。"""
    counts = {"wallet_mismatch": 0, "bill_mismatch": 0}
    async with advisory_lock(sm, LockKey.FUND_RECONCILE) as got:
        if not got:
            return counts
        until = billing_day_floor(at or now_utc())  # 与盘费日界同口径(北京日)
        since = until - timedelta(days=1)
        mismatches = await wallet_ledger_chain_check(sm)
        async with sm() as session:
            billed, consumed = await bills_vs_consume(session, since, until)
            dangling = await dangling_consume_refs(session)

        for m in mismatches:
            counts["wallet_mismatch"] += 1
            FUND_RECONCILE_MISMATCH_TOTAL.labels(kind="wallet_ledger").inc()
            logger.error(
                "fund_reconcile_wallet_mismatch",
                user_id=m.user_id,
                kind=m.kind,
                wallet_balance=str(m.wallet_balance),
                expected=str(m.expected),
                detail=m.detail,
            )
        if billed != consumed or dangling:
            counts["bill_mismatch"] = 1
            FUND_RECONCILE_MISMATCH_TOTAL.labels(kind="bill_consume").inc()
            logger.error(
                "fund_reconcile_bill_mismatch",
                day=since.isoformat(),
                billed=str(billed),
                consumed=str(consumed),
                diff=str(billed - consumed),
                dangling_consume_refs=dangling,
            )

        if counts["wallet_mismatch"] or counts["bill_mismatch"]:
            await _raise_admin_alert(sm, counts, since, billed, consumed)
        else:
            logger.info("fund_reconcile_ok", day=since.isoformat(), billed=str(billed))
    return counts


async def _raise_admin_alert(
    sm: async_sessionmaker[AsyncSession],
    counts: dict[str, int],
    day: datetime,
    billed: Decimal,
    consumed: Decimal,
) -> None:
    from app.modules.notify import service as notify_service

    parts = []
    if counts["wallet_mismatch"]:
        parts.append(f"{counts['wallet_mismatch']} 个账号的余额与流水累计不符")
    if counts["bill_mismatch"]:
        parts.append(f"当日出账 {billed} 与消费流水 {consumed} 不符")
    async with sm() as session:
        await notify_service.notify(
            session,
            None,  # 平台告警流
            type_="admin_alert",
            title="资金账实核对发现差异",
            content=";".join(parts) + "。请勿自行改账,先按 balance_ledger 追溯来源。",
            severity="critical",
            dedup_key=f"fund_reconcile:{day.date()}",
        )
        await session.commit()
