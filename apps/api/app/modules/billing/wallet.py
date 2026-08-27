"""钱包原语:所有余额变动的唯一入口。

更新必须 `SELECT ... FOR UPDATE`,同事务写 balance_ledger(balance_after 快照)。
本文件函数不 commit —— 由调用方把余额变动放进业务事务。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.money import as_amount, disk_daily_charge, hourly_cost
from app.core.pagination import Page, RawPage, clamp_limit, decode_cursor_int, slice_page
from app.core.policies import get_effective_policies
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


async def _wallet_row(session: AsyncSession, user_id: int, *, lock: bool) -> Wallet:
    """钱包行,不存在则首建。首建用 INSERT ... ON CONFLICT DO NOTHING:并发首建撞
    user_id 唯一约束既不抛错也不污染外层事务,随后重查即得胜出方的行。

    lock=True 时 FOR UPDATE,且 populate_existing 必须带:拿到行锁但读到 identity map
    里的旧副本 = 锁内校验(余额复检/燃烧率)对着陈旧值放行,等同 TOCTOU
    (实测:锁拿到、balance 是旧的)。
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
    """FOR UPDATE 锁定钱包行(不存在则先创建,并发首建不炸外层事务)。"""
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
) -> BalanceLedger:
    """扣款。allow_negative 为必填关键字,每个调用点显式表态。

    - 结算扣款(小时账单、盘日费)与管理员调账扣减:允许透支;
    - 「先付后用」的同步消费:不允许(开机/建盘走 assert_can_afford 预校验)。

    返回刚写入的流水行(已 flush,id 可用)——退款闭环需要回写 wallet_entry_id;
    其余调用方忽略返回值即可。
    """
    amount = as_amount(amount)
    if amount <= 0:
        raise ValueError("debit amount must be positive")
    wallet = await lock_wallet(session, user_id)
    new_balance = as_amount(wallet.balance - amount)
    if not allow_negative and new_balance < 0:
        raise AppError(ErrorCode.INSUFFICIENT_BALANCE, key="billing.insufficientBalance")
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


async def assert_can_afford(
    session: AsyncSession,
    user_id: int,
    *,
    additional_hourly: Decimal = Decimal("0.00"),
    additional_daily_disk: Decimal = Decimal("0.00"),
) -> None:
    """燃烧率感知的开户前校验:余额须覆盖「在途 + 新增」资源的一个预留期消耗。

    函数契约(调用方必须满足,否则护栏失效):

    - 必须在调用方事务内调用,且调用方须在**同一事务**内完成资源创建/开机并 commit。
      本函数先 FOR UPDATE 锁钱包行再统计在途燃烧率,锁持有到事务提交:
      并发开户请求因此串行,后到的请求统计时能看到先到请求新建的资源,被在途项挡住。
    - `additional_hourly`:本次新增实例的小时费(单价 × 卡数,2 位小数)。
      开新机/开机传该机费用;实例启动前不算「在途」,必须由调用方显式传入。
    - `additional_daily_disk`:本次新增数据盘的日均费(disk_daily_charge 均摊口径)。
    - 预留期:实例 afford_cover_hours 小时(默认 1),数据盘 disk_grace_days 天
      (暴露上限即「日费 × 宽限天数」)。均为 policies 在线可调。
    - 校验口径:余额 ≥ (在途实例时费 + additional_hourly) × afford_cover_hours
      + (在途盘日费 + additional_daily_disk) × disk_grace_days。
      「在途」= running 实例 + active 数据盘(grace 宽限盘已停计费,不计入)。
    - 不足抛 INSUFFICIENT_BALANCE(billing.insufficientForInFlight),params 含
      balance / required / inflight(在途部分的预留额),文案写明在途消耗原因。
    - 只校验不扣款:这是护栏不是精确预占,实际消耗由结算扣款(允许透支)兜底。

    替代已退役的 require_balance_at_least(只查余额 ≥ 单笔预估,不看在途,
    串行开户可绕过);本函数把在途燃烧率计入门槛。

    性能注记:在途统计走 orchestrator 现有的全量只读接口(全表 running 实例 +
    计费态盘,Python 侧按 user_id 过滤),单次调用两次全表扫;开户/开机都是
    低频写路径,可接受。若将来成为热点,由 orchestrator 侧加按用户过滤的只读
    接口再换实现,本函数契约不变。
    """
    # 延迟 import 防循环:orchestrator.service → billing.service → wallet
    from app.modules.orchestrator import service as orchestrator_service

    locked = await lock_wallet(session, user_id)  # 先锁再统计:并发新增才能互相看见
    policies = await get_effective_policies(session)

    running = (await orchestrator_service.list_running_instances_by_user(session)).get(user_id, [])
    inflight_hourly = sum(
        # 包周期实例不进燃烧率:它已经付过整段周期的钱,再算作「在途消耗」会让
        # 一个把余额全买成包月的用户**开不出任何新机**(护栏把他自己已付的钱又扣了一遍)
        (
            hourly_cost(i.price_hourly, i.gpu_count)
            for i in running
            if i.market != MARKET_SUBSCRIPTION
        ),
        Decimal("0.00"),
    )
    inflight_daily = sum(
        (
            disk_daily_charge(d.price_gb_month, d.size_gb)
            for d in await orchestrator_service.billable_disks(session)
            if d.user_id == user_id
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
    if locked.balance < required:
        raise AppError(
            ErrorCode.INSUFFICIENT_BALANCE,
            key="billing.insufficientForInFlight",
            params={
                "balance": format(locked.balance, "f"),
                "required": format(required, "f"),
                "inflight": format(inflight, "f"),
            },
        )


async def ledger_page(
    session: AsyncSession, user_id: int, *, cursor: str | None = None, limit: int | None = None
):
    """资金流水游标分页(用户端与管理端下钻共用同一实现)。"""
    lim = clamp_limit(limit)
    stmt = (
        select(BalanceLedger)
        .where(BalanceLedger.user_id == user_id)
        .order_by(BalanceLedger.id.desc())
        .limit(lim + 1)
    )
    last_id = decode_cursor_int(cursor)
    if last_id is not None:
        stmt = stmt.where(BalanceLedger.id < last_id)
    rows = list((await session.execute(stmt)).scalars())
    page_items, next_cursor = slice_page(rows, lim, key=lambda r: r.id)
    return Page[LedgerEntryOut](
        items=[LedgerEntryOut.model_validate(r) for r in page_items], next_cursor=next_cursor
    )


async def hourly_bills_page(
    session: AsyncSession,
    user_id: int,
    *,
    instance_id: int | None = None,
    month_range: tuple | None = None,
    cursor: str | None = None,
    limit: int | None = None,
):
    """小时账单游标分页(用户端与管理端下钻共用同一实现)。"""
    lim = clamp_limit(limit)
    stmt = (
        select(BillHourly)
        .where(BillHourly.user_id == user_id)
        .order_by(BillHourly.id.desc())
        .limit(lim + 1)
    )
    if instance_id is not None:
        stmt = stmt.where(BillHourly.instance_id == instance_id)
    if month_range is not None:
        stmt = stmt.where(
            BillHourly.hour_start >= month_range[0], BillHourly.hour_start < month_range[1]
        )
    last_id = decode_cursor_int(cursor)
    if last_id is not None:
        stmt = stmt.where(BillHourly.id < last_id)
    rows = list((await session.execute(stmt)).scalars())
    page_items, next_cursor = slice_page(rows, lim, key=lambda r: r.id)
    # 补实例名供账单页展示(纯数字 id 对运营/用户都不可读;实例行释放后仍保留,可查)
    from app.modules.orchestrator import service as orchestrator_service

    names = await orchestrator_service.instance_names(session, [r.instance_id for r in page_items])
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

    月度汇总与当日消费两个端点共用同一口径(窗口边界由端点按本地日/月界折算);
    items 补实例名(释放后行保留,改名跟当前名)供消费概览环图按名展示。
    """
    from app.modules.orchestrator import service as orchestrator_service

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
    names = await orchestrator_service.instance_names(session, [iid for iid, _a, _s in gpu_rows])
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
    """各用户余额。user_ids 给定则只聚合这些用户(列表页按本页用户过滤,避免全表)。"""
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

    口径:计量出账按账单**归属期**切窗(bills_hourly.hour_start / bills_daily_disk.day),
    而非扣款入账时间(ledger.created_at)——小时结算在次小时 :02 才扣款,按入账时间
    归属会把 23 点的消费错记到次日;按归属期才与用户账单页、日终核对同口径。

    **包周期预付另按收款当日切窗**(subscriptions.created_at):它不产生任何账单行,
    归属期就是收款那一刻,没有延迟入账的问题。`*_revenue` 是两者之和 —— 少加这一段,
    包周期上线后运营看到的「今日收入」会把全部预付漏掉;再单独给一个 `*_prepaid`,
    是因为一笔包年会在当天造成一个尖峰,看环比时必须能把它拆出来。
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
        "today_revenue": format(await _billed_since(day_start) + today_prepaid, "f"),
        "yesterday_revenue": format(
            await _billed_since(prev_day_start, day_start)
            + await _prepaid_since(prev_day_start, day_start),
            "f",
        ),
        "month_revenue": format(await _billed_since(month_start) + month_prepaid, "f"),
        "today_prepaid": format(today_prepaid, "f"),
        "month_prepaid": format(month_prepaid, "f"),
    }


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
    """充值订单列表(游标分页,降序)。order_no 精确匹配(unique 索引);day_range 按 created_at 过滤。"""
    lim = clamp_limit(limit)
    stmt = select(Order).order_by(Order.id.desc()).limit(lim + 1)
    if status:
        stmt = stmt.where(Order.status == status)
    if order_no:
        stmt = stmt.where(Order.order_no == order_no.strip())
    if user_id:
        stmt = stmt.where(Order.user_id == user_id)
    if day_range is not None:
        stmt = stmt.where(Order.created_at >= day_range[0], Order.created_at < day_range[1])
    last_id = decode_cursor_int(cursor)
    if last_id is not None:
        stmt = stmt.where(Order.id < last_id)
    rows = list((await session.execute(stmt)).scalars())
    page_items, next_cursor = slice_page(rows, lim, key=lambda r: r.id)
    return RawPage(items=page_items, next_cursor=next_cursor)
