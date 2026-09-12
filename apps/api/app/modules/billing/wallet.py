"""钱包原语:所有余额变动的唯一入口。

更新必须 `SELECT ... FOR UPDATE`,同事务写 balance_ledger(balance_after 快照)。本文件函数不 commit。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import Select, case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.logging import get_logger
from app.core.money import as_amount, disk_daily_charge, hourly_cost, money_str
from app.core.pagination import Page, RawPage, paginate_by_id
from app.core.platform_config import get_runtime_config
from app.core.pricing import MARKET_SUBSCRIPTION
from app.core.timeutil import now_utc
from app.modules.billing.models import (
    BalanceLedger,
    BillDailyDisk,
    BillHourly,
    Order,
    Subscription,
    Wallet,
)
from app.modules.billing.schemas import BillHourlyOut, BillSummaryItem, LedgerEntryOut
from app.modules.orchestrator import queries as orchestrator_queries

logger = get_logger(__name__)


async def _wallet_row(session: AsyncSession, user_id: int, *, lock: bool) -> Wallet:
    """钱包行,不存在则首建(INSERT ... ON CONFLICT DO NOTHING,随后重查)。
    lock=True 时 FOR UPDATE,且带 populate_existing(强制重读加锁后的值)。
    """
    stmt = select(Wallet).where(Wallet.user_id == user_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    wallet = (await session.execute(stmt)).scalar_one_or_none()
    if wallet is None:
        await session.execute(
            pg_insert(Wallet)
            .values(user_id=user_id)
            .on_conflict_do_nothing(index_elements=["user_id"])
        )
        wallet = (await session.execute(stmt)).scalar_one()
    return wallet


async def get_or_create_wallet(session: AsyncSession, user_id: int) -> Wallet:
    return await _wallet_row(session, user_id, lock=False)


async def lock_wallet(session: AsyncSession, user_id: int) -> Wallet:
    """FOR UPDATE 锁定钱包行(不存在则先创建)。"""
    return await _wallet_row(session, user_id, lock=True)


def _ledger(
    wallet: Wallet,
    type_: str,
    amount: Decimal,
    ref_type: str | None,
    ref_id: str | None,
    remark: str | None,
) -> BalanceLedger:
    return BalanceLedger(
        user_id=wallet.user_id,
        type=type_,
        amount=amount,
        balance_after=wallet.balance,
        ref_type=ref_type,
        ref_id=ref_id,
        remark=remark,
    )


async def credit(
    session: AsyncSession,
    user_id: int,
    amount: Decimal,
    *,
    type_: str,  # recharge / refund / adjust
    ref_type: str | None = None,
    ref_id: str | None = None,
    remark: str | None = None,
) -> Wallet:
    amount = as_amount(amount)
    if amount <= 0:
        raise ValueError("credit amount must be positive")
    wallet = await lock_wallet(session, user_id)
    wallet.balance = as_amount(wallet.balance + amount)
    session.add(_ledger(wallet, type_, amount, ref_type, ref_id, remark))
    return wallet


async def debit(
    session: AsyncSession,
    user_id: int,
    amount: Decimal,
    *,
    type_: str = "consume",
    ref_type: str | None = None,
    ref_id: str | None = None,
    remark: str | None = None,
    allow_negative: bool,
    allow_frozen: bool = False,
) -> BalanceLedger:
    """扣款。allow_negative 为必填关键字,每个调用点显式表态:
    结算扣款(小时账单、盘日费)与管理员调账扣减允许透支;「先付后用」的同步消费不允许。

    allow_frozen(默认 False):扣款后余额不得击穿 frozen;唯一合法 True 的场景是对已发生消费的
    事后收款(小时结算/盘日费,搭配 allow_negative=True)。
    返回刚写入的流水行(已 flush,id 可用)。
    """
    amount = as_amount(amount)
    if amount <= 0:
        raise ValueError("debit amount must be positive")
    wallet = await lock_wallet(session, user_id)
    new_balance = as_amount(wallet.balance - amount)
    if not allow_negative and new_balance < 0:
        raise AppError(ErrorCode.INSUFFICIENT_BALANCE, key="billing.insufficientBalance")
    if not allow_frozen and new_balance < wallet.frozen:
        # 击穿冻结额:文案与裸余额不足区分开
        raise AppError(
            ErrorCode.INSUFFICIENT_BALANCE,
            key="billing.insufficientAvailableFrozen",
            params={"frozen": money_str(wallet.frozen)},
        )
    wallet.balance = new_balance
    entry = _ledger(wallet, type_, -amount, ref_type, ref_id, remark)
    session.add(entry)
    await session.flush()
    return entry


async def get_balance(session: AsyncSession, user_id: int) -> Decimal:
    wallet = (
        await session.execute(select(Wallet).where(Wallet.user_id == user_id))
    ).scalar_one_or_none()
    return wallet.balance if wallet else Decimal("0.00")


def available_of(wallet: Wallet) -> Decimal:
    """可用余额 = balance - frozen。"""
    return as_amount(wallet.balance - wallet.frozen)


async def get_available_balance(session: AsyncSession, user_id: int) -> Decimal:
    wallet = (
        await session.execute(select(Wallet).where(Wallet.user_id == user_id))
    ).scalar_one_or_none()
    return available_of(wallet) if wallet else Decimal("0.00")


async def refundable_capacity(session: AsyncSession, user_id: int) -> Decimal:
    """可退余额:Σ充值 − Σ消费 − Σ已退 − Σ负向调账,下限 0。正向 adjust 不进可退额。
    退款申请与打款两处都按此封顶。
    """
    total = (
        await session.execute(
            select(
                func.coalesce(
                    # adjust 只计负向
                    func.sum(
                        case(
                            (BalanceLedger.type == "adjust", func.least(BalanceLedger.amount, 0)),
                            else_=BalanceLedger.amount,
                        )
                    ),
                    0,
                )
            ).where(BalanceLedger.user_id == user_id)
        )
    ).scalar_one()
    return max(Decimal("0.00"), as_amount(Decimal(total)))


async def freeze(
    session: AsyncSession, user_id: int, amount: Decimal, *, ref_id: str, remark: str
) -> Wallet:
    """等额冻结(渠道冲正):不动 balance、不记 ledger;frozen 可超过 balance。
    幂等由调用方(order 行标记)保证。"""
    amount = as_amount(amount)
    if amount <= 0:
        raise ValueError("freeze amount must be positive")
    wallet = await lock_wallet(session, user_id)
    wallet.frozen = as_amount(wallet.frozen + amount)
    logger.error("wallet_frozen", user_id=user_id, amount=str(amount), ref_id=ref_id, remark=remark)
    return wallet


async def release_freeze(session: AsyncSession, user_id: int, amount: Decimal) -> Wallet:
    """解冻(核销 release)。floor 0。"""
    amount = as_amount(amount)
    if amount <= 0:
        raise ValueError("release amount must be positive")
    wallet = await lock_wallet(session, user_id)
    wallet.frozen = as_amount(max(Decimal("0.00"), wallet.frozen - amount))
    return wallet


async def assert_can_afford(
    session: AsyncSession,
    user_id: int,
    *,
    additional_hourly: Decimal = Decimal("0.00"),
    additional_daily_disk: Decimal = Decimal("0.00"),
) -> None:
    """燃烧率感知的开户前校验:余额 ≥ (在途实例时费 + 待燃时费 + additional_hourly)
    × afford_cover_hours + (在途盘日费 + additional_daily_disk) × disk_grace_days。

    必须在调用方事务内调用,调用方同一事务内完成资源创建/开机并 commit
    (本函数先 FOR UPDATE 锁钱包行)。「在途」= running 实例 + active 数据盘;
    「待燃」= creating/starting 实例(orchestrator.pending_hourly,内部并入)。
    不足抛 INSUFFICIENT_BALANCE,params 含 balance / required / inflight。只校验不扣款。
    """
    # 必须延迟 import:orchestrator.service 与本模块循环依赖
    locked = await lock_wallet(session, user_id)  # 先锁再统计
    policies = await get_runtime_config(session)

    # 锁内只查本用户
    running = await orchestrator_queries.running_instances_of_user(session, user_id)
    pending = await orchestrator_queries.pending_hourly(session, user_id)
    inflight_hourly = (
        sum(
            # 包周期实例不进燃烧率
            (
                hourly_cost(i.price_hourly, i.gpu_count)
                for i in running
                if i.market != MARKET_SUBSCRIPTION
            ),
            Decimal("0.00"),
        )
        + pending
    )
    inflight_daily = sum(
        (
            disk_daily_charge(d.price_gb_month, d.size_gb)
            for d in await orchestrator_queries.billable_disks_of_user(session, user_id)
        ),
        Decimal("0.00"),
    )

    inflight = as_amount(inflight_hourly * policies.afford_cover_hours) + as_amount(
        inflight_daily * policies.disk_grace_days
    )
    required = (
        inflight
        + as_amount(as_amount(additional_hourly) * policies.afford_cover_hours)
        + as_amount(as_amount(additional_daily_disk) * policies.disk_grace_days)
    )
    if available_of(locked) < required:
        raise AppError(
            ErrorCode.INSUFFICIENT_BALANCE,
            key="billing.insufficientForInFlight",
            params={
                "balance": money_str(available_of(locked)),
                "required": money_str(required),
                "inflight": money_str(inflight),
            },
        )


async def ledger_page(
    session: AsyncSession, user_id: int, *, cursor: str | None = None, limit: int | None = None
):
    """资金流水游标分页(用户端与管理端共用)。"""
    stmt = (
        select(BalanceLedger)
        .where(BalanceLedger.user_id == user_id)
        .order_by(BalanceLedger.id.desc())
    )
    page_items, next_cursor = await paginate_by_id(
        session, stmt, id_col=BalanceLedger.id, cursor=cursor, limit=limit
    )
    return Page[LedgerEntryOut](
        items=[LedgerEntryOut.model_validate(r) for r in page_items], next_cursor=next_cursor
    )


async def hourly_bills_page(
    session: AsyncSession,
    user_id: int,
    *,
    instance_id: int | None = None,
    instance_ids: Sequence[int] | None = None,
    month_range: tuple | None = None,
    cursor: str | None = None,
    limit: int | None = None,
):
    """小时账单游标分页(用户端与管理端共用)。instance_ids = 一组实例的并集;空列表即无账。"""
    stmt = select(BillHourly).where(BillHourly.user_id == user_id).order_by(BillHourly.id.desc())
    if instance_id is not None:
        stmt = stmt.where(BillHourly.instance_id == instance_id)
    if instance_ids is not None:
        if not instance_ids:
            return Page[BillHourlyOut](items=[], next_cursor=None)
        stmt = stmt.where(BillHourly.instance_id.in_(list(instance_ids)))
    if month_range is not None:
        stmt = stmt.where(
            BillHourly.hour_start >= month_range[0], BillHourly.hour_start < month_range[1]
        )
    page_items, next_cursor = await paginate_by_id(
        session, stmt, id_col=BillHourly.id, cursor=cursor, limit=limit
    )
    # 补实例名供账单页展示(实例行释放后仍保留)
    names = await orchestrator_queries.instance_names(session, [r.instance_id for r in page_items])
    items = []
    for r in page_items:
        out = BillHourlyOut.model_validate(r)
        out.instance_name = names.get(r.instance_id)
        items.append(out)
    return Page[BillHourlyOut](items=items, next_cursor=next_cursor)


@dataclass(frozen=True)
class ConsumptionSummary:
    gpu_total: Decimal
    disk_total: Decimal
    items: list[BillSummaryItem]


async def consumption_summary(
    session: AsyncSession, user_id: int, start: datetime, end: datetime
) -> ConsumptionSummary:
    """[start, end) 窗口内的消费汇总:GPU 时费按实例归因 + 数据盘日费合计。
    月度汇总与当日消费共用同一口径;items 补实例名。
    """
    gpu_rows = (
        (
            await session.execute(
                select(
                    BillHourly.instance_id,
                    func.sum(BillHourly.amount),
                    func.sum(BillHourly.seconds_used),
                )
                .where(
                    BillHourly.user_id == user_id,
                    BillHourly.hour_start >= start,
                    BillHourly.hour_start < end,
                )
                .group_by(BillHourly.instance_id)
            )
        )
        .tuples()
        .all()
    )
    disk_total = (
        await session.execute(
            select(func.coalesce(func.sum(BillDailyDisk.amount), 0)).where(
                BillDailyDisk.user_id == user_id,
                BillDailyDisk.day >= start,
                BillDailyDisk.day < end,
            )
        )
    ).scalar_one()
    names = await orchestrator_queries.instance_names(session, [iid for iid, _a, _s in gpu_rows])
    items = [
        BillSummaryItem(
            instance_id=iid,
            instance_name=names.get(iid),
            total_amount=as_amount(Decimal(amount or 0)),
            total_seconds=int(secs or 0),
        )
        for iid, amount, secs in gpu_rows
    ]
    return ConsumptionSummary(
        gpu_total=sum((i.total_amount for i in items), Decimal("0.00")),
        disk_total=as_amount(Decimal(disk_total)),
        items=items,
    )


async def billed_by_instance(session: AsyncSession, start, end) -> dict[int, Decimal]:
    """对账用:窗口内各实例的事件计费合计(bills_hourly)。"""
    rows = (
        (
            await session.execute(
                select(BillHourly.instance_id, func.sum(BillHourly.amount))
                .where(BillHourly.hour_start >= start, BillHourly.hour_start < end)
                .group_by(BillHourly.instance_id)
            )
        )
        .tuples()
        .all()
    )
    return dict(rows)


async def balances_by_user(
    session: AsyncSession, user_ids: list[int] | None = None
) -> dict[int, Decimal]:
    """各用户余额。user_ids 给定则只聚合这些用户。"""
    stmt = select(Wallet.user_id, Wallet.balance)
    if user_ids is not None:
        stmt = stmt.where(Wallet.user_id.in_(user_ids))
    rows = (await session.execute(stmt)).tuples().all()
    return dict(rows)


async def consumed_by_user(
    session: AsyncSession, user_ids: list[int] | None = None
) -> dict[int, Decimal]:
    """累计消费(ledger consume 合计的绝对值)。user_ids 给定则只聚合这些用户。"""
    stmt = (
        select(BalanceLedger.user_id, func.coalesce(-func.sum(BalanceLedger.amount), 0))
        .where(BalanceLedger.type == "consume")
        .group_by(BalanceLedger.user_id)
    )
    if user_ids is not None:
        stmt = stmt.where(BalanceLedger.user_id.in_(user_ids))
    rows = (await session.execute(stmt)).tuples().all()
    return dict(rows)


async def revenue_summary(session: AsyncSession, *, tz_offset_minutes: int = 0) -> dict:
    """今日/本月消费额与环比基数。本地日界按 tz_offset 折算。

    计量出账按账单归属期切窗(bills_hourly.hour_start / bills_daily_disk.day);
    包周期预付按收款当日切窗(subscriptions.created_at)。`*_revenue` 是两者之和,`*_prepaid` 单列。
    """
    offset = timedelta(minutes=tz_offset_minutes)
    local_now = now_utc() + offset
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0) - offset
    month_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - offset
    prev_day_start = day_start - timedelta(days=1)

    async def _billed_since(start, end=None) -> Decimal:
        hourly_stmt = select(func.coalesce(func.sum(BillHourly.amount), 0)).where(
            BillHourly.hour_start >= start
        )
        daily_stmt = select(func.coalesce(func.sum(BillDailyDisk.amount), 0)).where(
            BillDailyDisk.day >= start
        )
        if end is not None:
            hourly_stmt = hourly_stmt.where(BillHourly.hour_start < end)
            daily_stmt = daily_stmt.where(BillDailyDisk.day < end)
        hourly = (await session.execute(hourly_stmt)).scalar_one()
        daily = (await session.execute(daily_stmt)).scalar_one()
        return Decimal(hourly) + Decimal(daily)

    async def _prepaid_since(start, end=None) -> Decimal:
        stmt = select(func.coalesce(func.sum(Subscription.amount_paid), 0)).where(
            Subscription.created_at >= start
        )
        if end is not None:
            stmt = stmt.where(Subscription.created_at < end)
        return Decimal((await session.execute(stmt)).scalar_one())

    today_prepaid = await _prepaid_since(day_start)
    month_prepaid = await _prepaid_since(month_start)
    return {
        "today_revenue": money_str(await _billed_since(day_start) + today_prepaid),
        "yesterday_revenue": money_str(
            await _billed_since(prev_day_start, day_start)
            + await _prepaid_since(prev_day_start, day_start)
        ),
        "month_revenue": money_str(await _billed_since(month_start) + month_prepaid),
        "today_prepaid": money_str(today_prepaid),
        "month_prepaid": money_str(month_prepaid),
    }


def admin_orders_query(
    *,
    status: str | None = None,
    order_no: str | None = None,
    user_id: int | None = None,
    day_range: tuple[datetime, datetime] | None = None,
) -> Select[tuple[Order]]:
    """管理端充值订单的筛选口径(列表与 CSV 共用):status 精确、order_no 精确、user_id、
    day_range 为 [start, end) 的 created_at 窗口。"""
    stmt = select(Order)
    if status:
        stmt = stmt.where(Order.status == status)
    if order_no:
        stmt = stmt.where(Order.order_no == order_no.strip())
    if user_id:
        stmt = stmt.where(Order.user_id == user_id)
    if day_range is not None:
        stmt = stmt.where(Order.created_at >= day_range[0], Order.created_at < day_range[1])
    return stmt


async def admin_list_orders(
    session: AsyncSession,
    status: str | None = None,
    *,
    order_no: str | None = None,
    user_id: int | None = None,
    day_range: tuple[datetime, datetime] | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> RawPage[Order]:
    """充值订单列表(游标分页,降序)。order_no 精确匹配;day_range 按 created_at 过滤。"""
    stmt = admin_orders_query(
        status=status, order_no=order_no, user_id=user_id, day_range=day_range
    ).order_by(Order.id.desc())
    page_items, next_cursor = await paginate_by_id(
        session, stmt, id_col=Order.id, cursor=cursor, limit=limit
    )
    return RawPage(items=page_items, next_cursor=next_cursor)
