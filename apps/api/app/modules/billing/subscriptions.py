"""包周期(预付)订阅:下单预扣、续费、到期巡检。

与小时结算彻底分离:`bills_hourly` 的结构、幂等键、水位线、缺口机制一行不动,包周期
实例只在结算候选里被跳过(orchestrator/queries.billing_candidates 一处)。

预付语义的三个后果:中途释放不退款(订阅转 cancelled,确需退款走人工 `refund_requests`);
到期不自动转按量,到期即停机;余额为零不停机 —— 停机判据、燃烧率、在途预留、冻结链
四处都要把它排除,漏一处就是「包月用户被欠费巡检误停机」。
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError, ErrorCode
from app.core.idempotency import IDEMPOTENCY_WINDOW, find_replay
from app.core.locks import LockKey, try_advisory_lock
from app.core.logging import get_logger
from app.core.metrics import PATROL_FAILED_TOTAL
from app.core.policies import get_effective_policies
from app.core.pricing import SubscriptionQuote, period_delta, quote_subscription
from app.core.timeutil import ensure_utc, now_utc
from app.modules.billing import wallet
from app.modules.billing.models import Subscription
from app.modules.notify import service as notify_service

if TYPE_CHECKING:
    from app.modules.orchestrator.models import Instance

logger = get_logger(__name__)

STATUS_ACTIVE = "active"
STATUS_EXPIRED = "expired"
STATUS_CANCELLED = "cancelled"

# 到期未续费的处置理由(instance_events.reason)。与欠费链路的 arrears_* 分开命名:
# 时间线上「包周期到期」和「欠费」对用户是两件不同的事
REASON_EXPIRED_STOP = "subscription_expired"
REASON_EXPIRED_FREEZE = "subscription_freeze"

_PERIOD_LABELS = {"day": "日", "week": "周", "month": "月", "year": "年"}


def period_label(period: str) -> str:
    return _PERIOD_LABELS.get(period, period)


async def quote(
    session: AsyncSession,
    *,
    base_hourly: Decimal,
    gpu_count: int,
    period: str,
    period_count: int,
) -> SubscriptionQuote:
    """报价(不落库)。市场页、创建预估、续费 modal 都经这里,不各算各的。"""
    policies = await get_effective_policies(session)
    return quote_subscription(
        base_hourly,
        gpu_count=gpu_count,
        period=period,
        period_count=period_count,
        policies=policies,
    )


async def charge_new(
    session: AsyncSession,
    *,
    user_id: int,
    instance_id: int,
    instance_name: str,
    sku_id: int,
    base_hourly: Decimal,
    gpu_count: int,
    period: str,
    period_count: int,
    idempotency_key: str | None,
) -> tuple[Subscription, SubscriptionQuote]:
    """下单预扣:写 subscriptions 行 + 扣款 + 流水。**不 commit**,由调用方并入建实例事务。

    扣款用 allow_negative=False —— 包周期是「先付后用」,不允许透支买断一个月。
    余额不够时抛的就是 debit 自己的 INSUFFICIENT_BALANCE,文案直指余额不足;
    「买得起、但买完就付不起在途按量实例」由调用方随后的 assert_can_afford 分开报。
    """
    quoted = await quote(
        session,
        base_hourly=base_hourly,
        gpu_count=gpu_count,
        period=period,
        period_count=period_count,
    )
    started = now_utc()
    row = Subscription(
        user_id=user_id,
        instance_id=instance_id,
        sku_id=sku_id,
        period=period,
        period_count=period_count,
        unit_price=quoted.base_hourly,
        amount_paid=quoted.amount,
        started_at=started,
        expires_at=started + period_delta(period, period_count),
        status=STATUS_ACTIVE,
        idempotency_key=idempotency_key,
    )
    session.add(row)
    await session.flush()
    await wallet.debit(
        session,
        user_id,
        quoted.amount,
        type_="consume",
        ref_type="subscription",
        ref_id=str(row.id),
        remark=f"{instance_name} 包{period_label(period)}×{period_count}",
        allow_negative=False,
    )
    return row, quoted


async def find_replay_row(session: AsyncSession, *, user_id: int, key: str) -> Subscription | None:
    """幂等窗口内同 (user_id, key) 的订阅行。转换/续费的**第一步**就要问它。

    转换尤其不能晚问:market 一旦翻成 subscription,重放请求会先撞上「只有按量实例
    可以转」这条守卫拿到 400;守卫若排在结算之后,重放还会用折后价再补一次转换前
    那个小时的账。
    """
    return await find_replay(
        session,
        Subscription,
        owner_col=Subscription.user_id,
        owner_id=user_id,
        key=key,
        window=IDEMPOTENCY_WINDOW,
    )


async def quote_of_row(
    session: AsyncSession, row: Subscription, gpu_count: int
) -> SubscriptionQuote:
    """按已落库的订阅行反算报价(幂等重放的响应体要和首次一致)。"""
    return await _quote_of(session, row, gpu_count)


async def convert(
    session: AsyncSession,
    *,
    instance: "Instance",
    period: str,
    period_count: int,
    idempotency_key: str | None,
) -> tuple[Subscription, SubscriptionQuote, bool]:
    """按量实例转包周期:开出这台实例的第一张订阅单。**不 commit**。

    与 `renew` 的差别只在起点:续费从老周期到期时刻接上,转换从**现在**起算 —— 转换前那段
    按量时间由调用方先结清(orchestrator.subscribe_instance → settle_on_demand_up_to),
    两段各按各的口径收费,既不重复也不留缝。

    报价基准是 `instance.price_hourly`(按量实例上它就是建实例时的 SKU 原价快照),
    不是 SKU 现价:与「变更 SKU 仅影响新实例」同一条口径,用户锁定的价格延续到包周期。
    """
    current = await current_for_instance(session, instance.id)
    if current is not None and current.status == STATUS_ACTIVE:
        # 已经在保还来转,多半是重复提交没带幂等键。放行会开出第二张单、扣两份钱
        raise AppError(
            ErrorCode.SUBSCRIPTION_NOT_RENEWABLE, key="billing.subscriptionAlreadyActive"
        )
    row, quoted = await charge_new(
        session,
        user_id=instance.user_id,
        instance_id=instance.id,
        instance_name=instance.name,
        sku_id=instance.sku_id,
        base_hourly=instance.price_hourly,
        gpu_count=instance.gpu_count,
        period=period,
        period_count=period_count,
        idempotency_key=idempotency_key,
    )
    logger.info(
        "subscription_converted",
        subscription_id=row.id,
        instance_id=instance.id,
        period=period,
        count=period_count,
        amount=str(quoted.amount),
    )
    return row, quoted, True


async def renew(
    session: AsyncSession,
    *,
    instance: "Instance",
    period: str,
    period_count: int,
    idempotency_key: str | None,
    actor: str = "user",
) -> tuple[Subscription, SubscriptionQuote, bool]:
    """续费:老行转 expired,新开一行并串 renewed_from_id。**不 commit**。

    返回 (新订阅, 报价, created);created=False = 幂等重放。

    新周期从**老周期的到期时刻**起算,不是从「现在」(否则提前续费会丢掉手上剩余天数)。
    只有老周期已过(到期后才来续)才从现在起算,否则会续出一个开局就少几天的周期。

    重新定价的基准是 `subscriptions.unit_price`(下单时的 SKU **原价**快照),不是 SKU 现价:
    与「变更 SKU 仅影响新实例」同一条口径,涨价不追已购用户。
    """
    if idempotency_key:
        existing = await find_replay(
            session,
            Subscription,
            owner_col=Subscription.user_id,
            owner_id=instance.user_id,
            key=idempotency_key,
            window=IDEMPOTENCY_WINDOW,
        )
        if existing is not None:
            return existing, await _quote_of(session, existing, instance.gpu_count), False

    current = await current_for_instance(session, instance.id)
    if current is None:
        raise AppError(ErrorCode.SUBSCRIPTION_NOT_RENEWABLE, key="billing.subscriptionMissing")
    if current.status == STATUS_CANCELLED:
        # 与「压根没买过」分开报:作废只可能是实例被释放过,续费不会复活这台机器
        raise AppError(ErrorCode.SUBSCRIPTION_NOT_RENEWABLE, key="billing.subscriptionCancelled")
    quoted = await quote(
        session,
        base_hourly=current.unit_price,
        gpu_count=instance.gpu_count,
        period=period,
        period_count=period_count,
    )
    started = max(ensure_utc(current.expires_at), now_utc())
    row = Subscription(
        user_id=instance.user_id,
        instance_id=instance.id,
        sku_id=current.sku_id,
        period=period,
        period_count=period_count,
        unit_price=current.unit_price,
        amount_paid=quoted.amount,
        started_at=started,
        expires_at=started + period_delta(period, period_count),
        status=STATUS_ACTIVE,
        auto_renew=current.auto_renew,
        renewed_from_id=current.id,
        idempotency_key=idempotency_key,
    )
    current.status = STATUS_EXPIRED
    session.add(row)
    if idempotency_key:
        try:
            await session.flush()
        except IntegrityError:
            # 并发同幂等键:UNIQUE(user_id, idempotency_key) 兜住,回查胜出方按重放返回。
            # rollback 是必须的(事务已 rollback-only),它同时撤掉上面对 current 的改动
            await session.rollback()
            raced = await find_replay(
                session,
                Subscription,
                owner_col=Subscription.user_id,
                owner_id=instance.user_id,
                key=idempotency_key,
            )
            if raced is None:
                raise
            return raced, await _quote_of(session, raced, instance.gpu_count), False
    else:
        await session.flush()
    await wallet.debit(
        session,
        instance.user_id,
        quoted.amount,
        type_="consume",
        ref_type="subscription",
        ref_id=str(row.id),
        remark=f"{instance.name} 续费 包{period_label(period)}×{period_count}",
        allow_negative=False,
    )
    logger.info(
        "subscription_renewed",
        subscription_id=row.id,
        instance_id=instance.id,
        period=period,
        count=period_count,
        amount=str(quoted.amount),
        actor=actor,
    )
    return row, quoted, True


async def _quote_of(session: AsyncSession, row: Subscription, gpu_count: int) -> SubscriptionQuote:
    """按已落库的订阅行反算报价(幂等重放的响应体要和首次一致)。"""
    return await quote(
        session,
        base_hourly=row.unit_price,
        gpu_count=gpu_count,
        period=row.period,
        period_count=row.period_count,
    )


# ---------- 查询 ----------


async def current_for_instance(session: AsyncSession, instance_id: int) -> Subscription | None:
    """该实例当前生效(或最后一期)的订阅行:取 id 最大的一行。

    续费链上永远只有一行 active,但到期未续时全链都是 expired —— 取最后一行才能回答
    「什么时候到的期」,那正是到期横幅和续费 modal 要显示的东西。
    """
    return (
        await session.execute(
            select(Subscription)
            .where(Subscription.instance_id == instance_id)
            .order_by(Subscription.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def latest_by_instance(
    session: AsyncSession, instance_ids: list[int]
) -> dict[int, Subscription]:
    """批量版 current_for_instance(列表页一次查完,不逐行打接口)。"""
    if not instance_ids:
        return {}
    rows = (
        (
            await session.execute(
                select(Subscription)
                .where(Subscription.instance_id.in_(instance_ids))
                .order_by(Subscription.id)
            )
        )
        .scalars()
        .all()
    )
    # 按 id 升序遍历,后写的覆盖先写的 → 每个实例留下 id 最大的那行
    return {row.instance_id: row for row in rows}


async def reserved_instance_ids(
    session: AsyncSession, instance_ids: list[int] | None = None
) -> set[int]:
    """仍在保(active 且未到期)的包周期实例 id;给了 instance_ids 就只在其中筛。

    软准入据此把「已停机但周期未满」的实例仍计为占用:平台承诺了整个周期。
    创建路径每次都要问一遍,故支持先按候选集收窄,不整表扫。
    """
    if instance_ids is not None and not instance_ids:
        return set()
    stmt = select(Subscription.instance_id).where(
        Subscription.status == STATUS_ACTIVE, Subscription.expires_at > now_utc()
    )
    if instance_ids is not None:
        stmt = stmt.where(Subscription.instance_id.in_(instance_ids))
    return set((await session.execute(stmt)).scalars().all())


async def expired_instance_ids(session: AsyncSession) -> set[int]:
    """最后一期已到期(且未被释放)的包周期实例 id —— 到期冻结链路的候选。"""
    expired = set(
        (
            await session.execute(
                select(Subscription.instance_id).where(
                    Subscription.status == STATUS_EXPIRED,
                    Subscription.expires_at <= now_utc(),
                )
            )
        )
        .scalars()
        .all()
    )
    # 减去在保集合:续过费的实例在链上既有 expired 的老行、也有 active 的新行,
    # 只看 expired 会把刚续过费的实例也送进冻结候选
    return expired - await reserved_instance_ids(session)


async def assert_active(session: AsyncSession, instance_id: int) -> Subscription:
    """包周期实例的开机门禁:周期内才让开机。

    行缺失也判过期(fail-closed):market='subscription' 却查不到订阅行是数据不一致,
    放行等于白送一台机器。
    """
    row = await current_for_instance(session, instance_id)
    if row is None or row.status != STATUS_ACTIVE or ensure_utc(row.expires_at) <= now_utc():
        raise AppError(
            ErrorCode.SUBSCRIPTION_EXPIRED,
            key="billing.subscriptionExpired",
            http_status=409,
        )
    return row


async def set_auto_renew(
    session: AsyncSession, *, user_id: int, instance_id: int, enabled: bool
) -> Subscription:
    """开关自动续费。**不 commit**。"""
    row = await current_for_instance(session, instance_id)
    if row is None or row.user_id != user_id:
        raise AppError(ErrorCode.SUBSCRIPTION_NOT_RENEWABLE, key="billing.subscriptionMissing")
    if row.status == STATUS_CANCELLED:
        raise AppError(ErrorCode.SUBSCRIPTION_NOT_RENEWABLE, key="billing.subscriptionCancelled")
    row.auto_renew = enabled
    return row


async def cancel_for_instance(session: AsyncSession, instance_id: int) -> None:
    """实例进入 releasing 时作废订阅(预付不退款)。**不 commit**。

    只动 active 行:已 expired 的历史行是账期凭证,把它改成 cancelled 会让财务口径
    多出一类「被追溯改写的收入」。
    """
    for row in (
        (
            await session.execute(
                select(Subscription).where(
                    Subscription.instance_id == instance_id,
                    Subscription.status == STATUS_ACTIVE,
                )
            )
        )
        .scalars()
        .all()
    ):
        row.status = STATUS_CANCELLED


# ---------- 到期巡检 ----------


async def subscription_patrol(sm: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """包周期到期链路(每 30 分钟):预警 → 自动续费 → 到期停机 → 冻结。

    **回收那一步刻意不在这里** —— frozen 到期回收由 balance_patrol 既有的
    `_patrol_frozen_and_arrears_stopped` 统一做,状态机与回收逻辑仍只有一处实现。
    这里只负责把实例送进 frozen 并写好 frozen_deadline。
    """
    counts = {"warned": 0, "renewed": 0, "renew_failed": 0, "stopped": 0, "frozen": 0}
    async with (
        sm() as lock_session,
        try_advisory_lock(lock_session, LockKey.SUBSCRIPTION_PATROL) as got,
    ):
        if not got:
            return counts
        await _patrol_due(sm, counts)
        await _patrol_freeze_expired(sm, counts)
    logger.info("subscription_patrol_done", **counts)
    return counts


async def _patrol_due(sm: async_sessionmaker[AsyncSession], counts: dict[str, int]) -> None:
    """临期预警 + 到期处置。逐条独立事务:一条炸了不拖累其它条。"""
    async with sm() as session:
        policies = await get_effective_policies(session)
        horizon = now_utc() + timedelta(days=policies.period_expire_warn_days)
        due_ids = list(
            (
                await session.execute(
                    select(Subscription.id).where(
                        Subscription.status == STATUS_ACTIVE,
                        Subscription.expires_at <= horizon,
                    )
                )
            )
            .scalars()
            .all()
        )
    for subscription_id in due_ids:
        try:
            async with sm() as session:
                await _handle_due(session, subscription_id, counts)
                await session.commit()
        except Exception:
            PATROL_FAILED_TOTAL.labels(stage="subscription_due").inc()
            logger.exception("subscription_patrol_failed", subscription_id=subscription_id)


async def _handle_due(session: AsyncSession, subscription_id: int, counts: dict[str, int]) -> None:
    from app.modules.orchestrator import service as orchestrator_service

    row = (
        await session.execute(select(Subscription).where(Subscription.id == subscription_id))
    ).scalar_one_or_none()
    # 取快照到现在之间,用户可能已经自己续费或释放了
    if row is None or row.status != STATUS_ACTIVE:
        return
    expires = ensure_utc(row.expires_at)
    now = now_utc()
    if expires > now:
        if await _warn_expiring(session, row, expires, now):
            counts["warned"] += 1
        return

    instance = await orchestrator_service.instance_by_id(session, row.instance_id)
    if row.auto_renew and await _try_auto_renew(session, row, instance, counts):
        return
    row.status = STATUS_EXPIRED
    await _expire_instance(session, instance, counts)


async def _warn_expiring(
    session: AsyncSession, row: Subscription, expires: datetime, now: datetime
) -> bool:
    """到期预警。warned_for_expiry 存「已预警到哪个到期时刻」而不是布尔:
    续费后 expires_at 变了,新周期自然重新可预警,不需要额外清位。"""
    if row.warned_for_expiry is not None and ensure_utc(row.warned_for_expiry) == expires:
        return False
    row.warned_for_expiry = expires
    days = max(0, round((expires - now).total_seconds() / 86400))
    await notify_service.send_subscription_notice(
        session,
        row.user_id,
        action="expiring",
        detail=(
            f"包{period_label(row.period)}将于 {expires:%Y-%m-%d %H:%M} UTC 到期"
            f"(剩 {days} 天),到期后自动停机。请及时续费。"
        ),
        dedup_suffix=str(row.id),
    )
    return True


async def _try_auto_renew(
    session: AsyncSession, row: Subscription, instance: "Instance", counts: dict[str, int]
) -> bool:
    """自动续费。余额不够就返回 False 走到期停机链路,绝不透支。

    先算价再比余额,而不是让 `renew` 的 debit 抛 INSUFFICIENT_BALANCE 兜底:
    debit 抛错时 `renew` 已经把老订阅行改成了 expired,ORM 里那个改动还在,
    捕获异常继续用同一个 session 就会把它一起提交(老周期凭空作废)。
    """
    quoted = await quote(
        session,
        base_hourly=row.unit_price,
        gpu_count=instance.gpu_count,
        period=row.period,
        period_count=row.period_count,
    )
    if await wallet.get_balance(session, row.user_id) < quoted.amount:
        counts["renew_failed"] += 1
        await notify_service.send_subscription_notice(
            session,
            row.user_id,
            action="renew_failed",
            detail="余额不足,自动续费失败,实例将停机。充值后可手动续费。",
            dedup_suffix=str(row.id),
        )
        return False
    await renew(
        session,
        instance=instance,
        period=row.period,
        period_count=row.period_count,
        idempotency_key=None,
        actor="system",
    )
    counts["renewed"] += 1
    await notify_service.send_subscription_notice(
        session,
        row.user_id,
        action="renewed",
        detail=(
            f"已自动续费 包{period_label(row.period)}×{row.period_count},扣款 ¥{quoted.amount}。"
        ),
        dedup_suffix=str(row.id),
    )
    return True


async def _expire_instance(
    session: AsyncSession, instance: "Instance", counts: dict[str, int]
) -> None:
    """到期处置:running → 停机(停稳后由 _patrol_freeze_expired 接着冻结);
    stopped → 直接冻结起回收倒计时;其余状态本轮不动,下轮再来。"""
    from app.modules.orchestrator import service as orchestrator_service

    if instance.status == "running":
        await orchestrator_service.system_stop(session, instance, reason=REASON_EXPIRED_STOP)
        counts["stopped"] += 1
    elif instance.status == "stopped":
        await _freeze(session, instance)
        counts["frozen"] += 1
    else:
        # creating/starting/stopping/frozen/releasing:本轮动不了(状态机不允许),
        # 收敛到终态后由下一轮接手。订阅行已置 expired,不会重复计费
        return
    await notify_service.send_subscription_notice(
        session,
        instance.user_id,
        action="expired",
        detail="包周期已到期,实例已停机;72 小时内未续费将回收实例盘(数据盘不受影响)。",
        dedup_suffix=str(instance.id),
    )


async def _freeze(session: AsyncSession, instance: "Instance") -> None:
    """冻结窗口复用 `freeze_grace_hours`(欠费同款):两条链路对用户是同一句承诺
    ——「停机后 72 小时内还能救回来」。"""
    from app.modules.orchestrator import service as orchestrator_service

    policies = await get_effective_policies(session)
    await orchestrator_service.freeze_instance(
        session,
        instance,
        now_utc() + timedelta(hours=policies.freeze_grace_hours),
        reason=REASON_EXPIRED_FREEZE,
    )


async def _patrol_freeze_expired(
    sm: async_sessionmaker[AsyncSession], counts: dict[str, int]
) -> None:
    """已到期且已停稳的包周期实例 → 冻结(起 72h 回收倒计时)。

    单独一趟而不是接在停机后面:停机是异步的(outbox 删 Pod → reconciler 确认),
    到期那一刻实例还在 stopping,当场冻不了。欠费链路的同一步在 balance_patrol 里,
    但那一步的进入条件是「余额 ≤ 0」—— 包周期用户余额可能很充足,套不上。
    """
    from app.modules.orchestrator import service as orchestrator_service

    async with sm() as session:
        expired = await expired_instance_ids(session)
        if not expired:
            return
        candidates = [
            inst
            for inst in await orchestrator_service.list_instances_by_status(session, "stopped")
            if inst.id in expired
        ]
    for inst in candidates:
        try:
            async with sm() as session:
                fresh = await orchestrator_service.instance_by_id(session, inst.id)
                if fresh.status != "stopped":
                    continue
                await _freeze(session, fresh)
                await session.commit()
                counts["frozen"] += 1
        except Exception:
            PATROL_FAILED_TOTAL.labels(stage="subscription_freeze").inc()
            logger.exception("subscription_freeze_failed", instance_id=inst.id)
