"""资金账实核对(只读探针)。

平台的预防控制是扎实的:`wallet.py` 是唯一的钱包写入口,credit/debit 都是
`lock_wallet` → 改 balance → 同事务 add(_ledger),而 `_ledger` 在赋值**之后**才快照
balance_after,所以「余额变动」与「流水行」在构造上就是原子配对的。

但预防控制背后没有任何检测控制:一旦某次改动破坏了这个不变式(改错代码、手工 SQL、
半提交的事务),没有任何东西会发现 —— 没有任务、没有指标、没有管理端视图、没有 DB 约束。
`balance_ledger` 在方案文档里被定义为「对账基准」,而这份基准从来没有被对过。
损失会一直复利到有人恰好去看为止,这在一个准备收公众钱的平台上是典型的致命形态。

本模块只报不改:发现差异就打 error 日志 + 指标 + 管理端告警,绝不自动「纠正」——
自动改账会把一个可查的差异变成一个不可查的差异。
"""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.locks import LockKey, try_advisory_lock
from app.core.logging import get_logger
from app.core.metrics import FUND_RECONCILE_MISMATCH_TOTAL
from app.core.timeutil import day_floor, now_utc
from app.modules.billing.models import BalanceLedger, BillDailyDisk, BillHourly, Wallet

logger = get_logger(__name__)

# 出账与流水的比对窗口:向前多看一天,覆盖跨日结算与尾账的边界
CONSUME_REF_TYPES = ("bill_hourly", "bill_daily_disk")


async def wallet_ledger_mismatches(
    session: AsyncSession,
) -> list[tuple[int, Decimal, Decimal]]:
    """余额 ≠ 流水累计的用户。返回 (user_id, wallet_balance, ledger_sum)。

    一条分组 SQL,不按用户循环 —— 这是每日全量核对,不能是 N+1。
    """
    ledger = (
        select(
            BalanceLedger.user_id.label("user_id"),
            func.coalesce(func.sum(BalanceLedger.amount), 0).label("total"),
        )
        .group_by(BalanceLedger.user_id)
        .subquery()
    )
    rows = (
        (
            await session.execute(
                select(Wallet.user_id, Wallet.balance, func.coalesce(ledger.c.total, 0))
                .outerjoin(ledger, ledger.c.user_id == Wallet.user_id)
                .where(Wallet.balance != func.coalesce(ledger.c.total, 0))
            )
        )
        .tuples()
        .all()
    )
    return [(uid, Decimal(bal), Decimal(total)) for uid, bal, total in rows]


async def bills_vs_consume(
    session: AsyncSession, since: datetime, until: datetime
) -> tuple[Decimal, Decimal]:
    """窗口内 (出账合计, 消费流水合计的绝对值)。两者必须相等。

    出账走的是 bills_*,扣款走的是 ledger,中间隔着 wallet.debit —— 任何一侧写成功而另一侧
    没写(或写了两次),这个等式就会破。
    """
    hourly = (
        await session.execute(
            select(func.coalesce(func.sum(BillHourly.amount), 0)).where(
                BillHourly.created_at >= since, BillHourly.created_at < until
            )
        )
    ).scalar_one()
    daily = (
        await session.execute(
            select(func.coalesce(func.sum(BillDailyDisk.amount), 0)).where(
                BillDailyDisk.created_at >= since, BillDailyDisk.created_at < until
            )
        )
    ).scalar_one()
    consumed = (
        await session.execute(
            select(func.coalesce(func.sum(BalanceLedger.amount), 0)).where(
                BalanceLedger.type == "consume",
                BalanceLedger.ref_type.in_(CONSUME_REF_TYPES),
                BalanceLedger.created_at >= since,
                BalanceLedger.created_at < until,
            )
        )
    ).scalar_one()
    return Decimal(hourly) + Decimal(daily), -Decimal(consumed)


async def reconcile_funds(
    sm: async_sessionmaker[AsyncSession], *, at: datetime | None = None
) -> dict[str, int]:
    """每日资金账实核对。返回 {"wallet_mismatch": n, "bill_mismatch": 0/1}。"""
    counts = {"wallet_mismatch": 0, "bill_mismatch": 0}
    async with (
        sm() as lock_session,
        try_advisory_lock(lock_session, LockKey.FUND_RECONCILE) as got,
    ):
        if not got:
            return counts
        until = day_floor(at or now_utc())
        since = until - timedelta(days=1)
        async with sm() as session:
            mismatches = await wallet_ledger_mismatches(session)
            billed, consumed = await bills_vs_consume(session, since, until)

        for user_id, balance, total in mismatches:
            counts["wallet_mismatch"] += 1
            FUND_RECONCILE_MISMATCH_TOTAL.labels(kind="wallet_ledger").inc()
            logger.error(
                "fund_reconcile_wallet_mismatch",
                user_id=user_id,
                wallet_balance=str(balance),
                ledger_sum=str(total),
                diff=str(balance - total),
            )
        if billed != consumed:
            counts["bill_mismatch"] = 1
            FUND_RECONCILE_MISMATCH_TOTAL.labels(kind="bill_consume").inc()
            logger.error(
                "fund_reconcile_bill_mismatch",
                day=since.isoformat(),
                billed=str(billed),
                consumed=str(consumed),
                diff=str(billed - consumed),
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
