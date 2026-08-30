"""充值订单与回调入账。回调幂等三重保障:channel_txn_id 唯一、订单状态检查、行锁。"""

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError, ErrorCode, not_found
from app.core.idempotency import find_replay, request_fingerprint
from app.core.logging import get_logger
from app.core.metrics import (
    PAYMENT_CALLBACK_MISMATCH_TOTAL,
    PAYMENT_CHANNEL_REVERSED_TOTAL,
    PAYMENT_CLOSED_ORDER_RESCUED_TOTAL,
    PAYMENT_RECOVER_FAILED_TOTAL,
)
from app.core.money import as_amount
from app.core.platform_config import get_effective_platform_config
from app.core.timeutil import now_utc
from app.modules.billing import wallet
from app.modules.billing.models import Order
from app.modules.billing.payment_channels import (
    CallbackResult,
    PaymentChannel,
    QueryResult,
    get_channel,
)

logger = get_logger(__name__)

# 渠道查单显式超时:两个真实渠道 SDK 都走 asyncio.to_thread 且无自带超时,
# 挂死的查单会拖垮收敛轮/占住管理端请求(线程本身杀不掉,等结果必须有界)
CHANNEL_QUERY_TIMEOUT_SECONDS = 15.0


async def _query_with_timeout(channel: PaymentChannel, order: Order) -> QueryResult:
    """带显式超时的渠道查单。超时抛 TimeoutError,由调用方按「渠道不可达」处理。"""
    return await asyncio.wait_for(channel.query_order(order), timeout=CHANNEL_QUERY_TIMEOUT_SECONDS)


def _gen_order_no() -> str:
    return f"R{now_utc():%Y%m%d%H%M%S}{secrets.token_hex(4)}"


async def create_recharge(
    session: AsyncSession,
    user_id: int,
    amount: Decimal,
    channel_name: str,
    idempotency_key: str | None,
) -> tuple[Order, bool]:
    """创建充值单。返回 (订单, created):created=False = 幂等重放(含上次未拿到码的补拉),
    路由据此回 200 + X-Idempotent-Replay 而非 201。"""
    from app.core.config import get_settings

    amount = as_amount(amount)  # 上下限由 RechargeCreate 契约层校验
    cfg = await get_effective_platform_config(session)
    if channel_name in ("wechat", "alipay") and cfg[f"payment_{channel_name}_enabled"] != "true":
        raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, key="billing.channelNotEnabled")
    channel = await get_channel(channel_name, session)

    # 异参检测指纹:同键改了金额/渠道 → 409,而非静默返回上一单
    fingerprint = request_fingerprint(user_id, amount, channel_name)
    if idempotency_key:
        existing = await find_replay(
            session,
            Order,
            owner_col=Order.user_id,
            owner_id=user_id,
            key=idempotency_key,
            fingerprint=fingerprint,
        )
        if existing is not None:
            if existing.status == "pending" and not existing.qr_url:
                return await _attach_payment(session, existing, channel), False  # 补拉支付码
            return existing, False

    order = Order(
        order_no=_gen_order_no(),
        user_id=user_id,
        amount=amount,
        channel=channel.name,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        expires_at=now_utc() + timedelta(seconds=get_settings().recharge_order_ttl_seconds),
    )
    session.add(order)
    try:
        await session.commit()  # 先落单再调渠道
    except IntegrityError as exc:
        # 同 (user_id, idempotency_key) 并发首单(双击/超时重试):先 SELECT 后 INSERT
        # 的竞态由唯一约束兜底,回查胜出方的订单按幂等重放返回,不能 500
        await session.rollback()
        if idempotency_key is None:
            raise  # 无幂等键不会撞 (user_id, idempotency_key) 约束,原样上抛
        existing = await find_replay(
            session,
            Order,
            owner_col=Order.user_id,
            owner_id=user_id,
            key=idempotency_key,
            fingerprint=fingerprint,
        )
        if existing is None:
            raise exc  # 撞的是别的唯一约束(理论不到达),原样上抛
        if existing.status == "pending" and not existing.qr_url:
            return await _attach_payment(session, existing, channel), False  # 胜出方补拉
        return existing, False
    logger.info("recharge_order_created", order_no=order.order_no, user_id=user_id)
    return await _attach_payment(session, order, channel), True


async def _attach_payment(session: AsyncSession, order: Order, channel: PaymentChannel) -> Order:
    """向渠道下单并回填二维码。不在事务里调渠道(连接池会被渠道抖动占满)。

    失败的订单让出幂等键,用户按原键重试即可开新单。
    """
    try:
        qr_url = await channel.create_payment(order)
    except Exception:
        order.status = "failed"
        order.idempotency_key = None
        await session.commit()
        raise
    order.qr_url = qr_url
    await session.commit()
    return order


async def get_order(session: AsyncSession, user_id: int, order_no: str) -> Order:
    order = (
        await session.execute(
            select(Order).where(Order.order_no == order_no, Order.user_id == user_id)
        )
    ).scalar_one_or_none()
    if order is None:
        raise AppError(ErrorCode.ORDER_NOT_FOUND, key="billing.orderNotFound", http_status=404)
    return order


async def _credit_paid_order(
    session: AsyncSession, order: Order, *, channel_txn_id: str, remark: str
) -> None:
    """订单置 paid + 钱包入账(同事务,不 commit;回调与人工补单共用)。

    先 flush 再入账:订单行上的唯一约束(channel_txn_id 同一渠道流水只入账一次、
    backfill_idempotency_key)冲突在此显式抛 IntegrityError,而不是等 wallet.credit 内的
    SELECT 触发 autoflush 时漏成 500。
    """
    order.status = "paid"
    order.channel_txn_id = channel_txn_id
    order.paid_at = now_utc()
    await session.flush()
    await wallet.credit(
        session,
        order.user_id,
        order.amount,
        type_="recharge",
        ref_type="order",
        ref_id=order.order_no,
        remark=remark,
    )


async def handle_callback(session: AsyncSession, channel_name: str, result: CallbackResult) -> str:
    """处理支付回调。返回 'ok'(含重放)或抛错。重放回调不重复入账。"""
    order = (
        await session.execute(
            select(Order).where(Order.order_no == result.order_no).with_for_update()
        )
    ).scalar_one_or_none()
    if order is None:
        raise not_found("订单不存在")
    if order.status == "paid":
        if not result.success and order.channel_reversed_at is None:
            # 渠道侧对已入账订单的关单/退款通知(商户后台退款等):不自动冲账(渠道
            # 通知可能是中间态噪音),但等额冻结钱包阻断继续消费——冲正到人工核销之间
            # 余额不得继续流出。解冻/扣回走管理端 /finance/reversals/{order_no}/resolve。
            # 幂等:渠道会重推,只对首次置标的那一次冻结。
            order.channel_reversed_at = now_utc()
            await wallet.freeze(
                session,
                order.user_id,
                order.amount,
                ref_id=order.order_no,
                remark="渠道冲正冻结",
            )
            await session.commit()
            logger.error(
                "channel_reversed_on_paid_order",
                order_no=order.order_no,
                channel=channel_name,
                channel_txn_id=result.channel_txn_id,
                frozen=str(order.amount),
            )
            PAYMENT_CHANNEL_REVERSED_TOTAL.inc()
        return "ok"  # 重放:已入账,直接确认
    # 关单/失败单后到达的有效成功回调(验签已过):按人工补单同等校验(渠道一致+金额一致)自动入账。
    # failed 订单多为渠道中间态(TRADE_CLOSED/WAIT_BUYER_PAY)误迁移,渠道侧可能稍后转成功。
    rescued = order.status in ("closed", "failed") and result.success
    if order.status != "pending" and not rescued:
        logger.warning("callback_on_closed_order", order_no=order.order_no, status=order.status)
        return "ok"
    if order.channel != channel_name:
        raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, key="billing.callbackChannelMismatch")
    if as_amount(result.amount) != order.amount:
        logger.error(
            "callback_amount_mismatch",
            order_no=order.order_no,
            expected=str(order.amount),
            got=str(result.amount),
        )
        PAYMENT_CALLBACK_MISMATCH_TOTAL.inc()
        raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, key="billing.callbackAmountMismatch")
    if not result.success:
        order.status = "failed"
        await session.commit()
        return "ok"

    await _credit_paid_order(
        session, order, channel_txn_id=result.channel_txn_id, remark=f"{channel_name} 充值"
    )
    await session.commit()
    if rescued:
        logger.info("closed_order_auto_credited", order_no=order.order_no)
        PAYMENT_CLOSED_ORDER_RESCUED_TOTAL.inc()
    logger.info("recharge_paid", order_no=order.order_no, amount=str(order.amount))
    return "ok"


async def reconcile_pending_orders(sm: async_sessionmaker[AsyncSession]) -> int:
    """查单收敛(定时任务,每 2 分钟):对 pending 超 60s、近 48h failed 及近 48h closed 的
    订单主动向渠道查单,渠道侧已支付则按回调同路径入账。

    failed/closed 订单纳入扫描:渠道中间态(TRADE_CLOSED 等)会把订单打成 failed;
    「用户在关单前最后一刻支付成功但回调丢失」则留下 closed 单——两种情况下
    渠道查单都是唯一事实源,扫进来才能自动救回,不必等人工补单。
    48h 窗口限定避免无限重扫下单即失败的旧单(渠道侧查无此单,会被跳过);
    closed 单由 expires_at 界定同一只窗口(关单时刻 = expires_at)。

    advisory lock 防多副本重复;单轮 cap 50;渠道不可达跳过该单,下轮再试。
    """
    from app.core.locks import LockKey, try_advisory_lock

    credited = 0
    async with (
        sm() as lock_session,
        try_advisory_lock(lock_session, LockKey.PAYMENT_RECONCILE) as got,
    ):
        if not got:
            return 0
        async with sm() as session:
            orders = list(
                (
                    await session.execute(
                        select(Order)
                        .where(
                            or_(
                                Order.status.in_(("pending", "failed")),
                                # closed 单按关单时刻(expires_at)限定近 48h:
                                # 与 created_at 双界交集 = 关单不久的新单
                                Order.status == "closed",
                            ),
                            Order.created_at < now_utc() - timedelta(seconds=60),
                            Order.created_at > now_utc() - timedelta(hours=48),
                            or_(
                                Order.status != "closed",
                                Order.expires_at > now_utc() - timedelta(hours=48),
                            ),
                        )
                        .order_by(Order.id)
                        .limit(50)
                    )
                ).scalars()
            )
        for order in orders:
            try:
                async with sm() as cfg_session:
                    channel = await get_channel(order.channel, cfg_session)
                result = await _query_with_timeout(channel, order)
            except Exception as exc:
                # 渠道不可达/查单超时:跳过该单,下轮再试
                logger.warning("order_query_failed", order_no=order.order_no, error=str(exc))
                continue
            if not (
                result.status == "paid" and result.channel_txn_id and result.amount is not None
            ):
                continue
            try:
                async with sm() as session:
                    await handle_callback(
                        session,
                        order.channel,
                        CallbackResult(order.order_no, result.channel_txn_id, result.amount, True),
                    )
            except Exception as exc:
                # 单笔入账失败(金额/渠道不符、channel_txn_id 撞唯一约束等)记日志+指标,
                # 不中断整轮
                logger.exception("order_recover_failed", order_no=order.order_no)
                PAYMENT_RECOVER_FAILED_TOTAL.labels(error=type(exc).__name__).inc()
                continue
            credited += 1
            logger.info("lost_callback_recovered", order_no=order.order_no)
    return credited


async def verify_order(session: AsyncSession, order_no: str) -> dict:
    """管理端:向渠道核验订单(补单前置)。渠道结果是唯一事实源。"""
    order = (
        await session.execute(select(Order).where(Order.order_no == order_no))
    ).scalar_one_or_none()
    if order is None:
        raise AppError(ErrorCode.ORDER_NOT_FOUND, key="billing.orderNotFound", http_status=404)
    channel = await get_channel(order.channel, session)
    result = await _query_with_timeout(channel, order)
    matches = (
        result.status == "paid"
        and result.amount is not None
        and as_amount(result.amount) == order.amount
    )
    return {
        "order_no": order.order_no,
        "order_status": order.status,
        "order_amount": str(order.amount),
        "channel_status": result.status,
        "channel_txn_id": result.channel_txn_id,
        "channel_amount": str(result.amount) if result.amount is not None else None,
        "matches": matches,
    }


def _is_backfill_replay(order: Order, idempotency_key: str | None) -> bool:
    """补单状态检查(锁外预检与锁内复核同一段):已入账且同键 → True(幂等重放);
    已入账异键 → 409;不可补的状态 → 409;可补 → False。"""
    if order.status == "paid":
        if idempotency_key is not None and order.backfill_idempotency_key == idempotency_key:
            return True
        raise AppError(ErrorCode.CONFLICT, key="billing.orderAlreadyPaid")
    if order.status not in ("pending", "closed", "failed"):
        raise AppError(
            ErrorCode.CONFLICT,
            key="billing.orderStateNotBackfillable",
            params={"status": order.status},
        )
    return False


async def backfill_order(
    session: AsyncSession,
    order_no: str,
    idempotency_key: str | None = None,
    audit_writer: Callable[[AsyncSession], Awaitable[None]] | None = None,
) -> tuple[Order, bool]:
    """管理端人工补单:仅当渠道侧核验为已支付且金额一致才入账,closed/failed 订单同样可补。

    failed 订单(下单失败/失败回调)不是死胡同:渠道是唯一事实源,查单确认已支付即可救回。
    幂等由 channel_txn_id 唯一约束 + 行锁 + 状态检查 + backfill_idempotency_key 唯一约束四重保障。
    渠道查单在无锁状态下进行(带显式超时):行锁持有期间不做网络调用,
    查单结果落账前在锁内复核订单状态,查单期间被回调/并发补单入账的订单在此被拦下。
    带 Idempotency-Key 调用时,同键重放且订单已入账则直接回当前状态(不 409)。
    返回 (订单, replayed):replayed=True 表示同键重放,路由据此回 X-Idempotent-Replay 头。
    """
    order = (
        await session.execute(select(Order).where(Order.order_no == order_no))
    ).scalar_one_or_none()
    if order is None:
        raise AppError(ErrorCode.ORDER_NOT_FOUND, key="billing.orderNotFound", http_status=404)
    if _is_backfill_replay(order, idempotency_key):
        return order, True  # 同键重放:本单已由本次补单入账,按当前状态返回
    channel = await get_channel(order.channel, session)
    result = await _query_with_timeout(channel, order)
    if result.status != "paid" or not result.channel_txn_id or result.amount is None:
        raise AppError(
            ErrorCode.PAYMENT_CHANNEL_ERROR,
            key="billing.channelStateNotBackfillable",
            params={"status": result.status},
        )
    if as_amount(result.amount) != order.amount:
        raise AppError(
            ErrorCode.PAYMENT_CHANNEL_ERROR,
            key="billing.amountMismatchAdjust",
            params={"channel": str(result.amount), "order": str(order.amount)},
        )
    # 锁内复核:查渠道期间订单可能已被回调/并发补单入账
    order = (
        await session.execute(select(Order).where(Order.order_no == order_no).with_for_update())
    ).scalar_one()
    if _is_backfill_replay(order, idempotency_key):
        return order, True  # 并发同键补单已胜出:按幂等重放返回
    order.backfill_idempotency_key = idempotency_key
    try:
        await _credit_paid_order(
            session,
            order,
            channel_txn_id=result.channel_txn_id,
            remark=f"{order.channel} 充值(人工补单)",
        )
    except IntegrityError as exc:
        # backfill_idempotency_key 唯一约束兜底:同键被并发用到另一笔订单,回查持键方后按 409 表态
        await session.rollback()
        if idempotency_key is not None:
            holder = (
                await session.execute(
                    select(Order).where(Order.backfill_idempotency_key == idempotency_key)
                )
            ).scalar_one_or_none()
            if holder is not None and holder.order_no != order_no:
                raise AppError(
                    ErrorCode.CONFLICT,
                    key="billing.backfillKeyInUse",
                    params={"order_no": holder.order_no},
                    http_status=409,
                ) from exc
        raise
    if audit_writer is not None:
        await audit_writer(session)  # 同步审计:与入账同事务,写失败即回滚
    await session.commit()
    logger.info("order_backfilled", order_no=order.order_no, amount=str(order.amount))
    return order, False


async def list_payment_anomalies(session: AsyncSession) -> list[dict]:
    """异常清单:疑似丢回调(pending 超 10 分钟)、近 48h 被关单、近 48h 失败单、负余额钱包。"""
    from app.modules.billing.models import Wallet

    now = now_utc()
    items: list[dict] = []
    stale = (
        await session.execute(
            select(Order)
            .where(Order.status == "pending", Order.created_at < now - timedelta(minutes=10))
            .order_by(Order.id.desc())
            .limit(100)
        )
    ).scalars()
    for o in stale:
        items.append(
            {
                "kind": "lost_callback",
                "order_no": o.order_no,
                "user_id": o.user_id,
                "amount": str(o.amount),
                "detail": f"{o.channel} 渠道 pending 超 10 分钟,疑似回调丢失",
                "created_at": o.created_at,
            }
        )
    closed_recent = (
        await session.execute(
            select(Order)
            .where(Order.status == "closed", Order.expires_at > now - timedelta(hours=48))
            .order_by(Order.id.desc())
            .limit(100)
        )
    ).scalars()
    for o in closed_recent:
        items.append(
            {
                "kind": "closed_order",
                "order_no": o.order_no,
                "user_id": o.user_id,
                "amount": str(o.amount),
                "detail": "订单超时关闭;若用户声称已付,先核验渠道再补单",
                "created_at": o.created_at,
            }
        )
    failed_recent = (
        await session.execute(
            select(Order)
            .where(Order.status == "failed", Order.created_at > now - timedelta(hours=48))
            .order_by(Order.id.desc())
            .limit(100)
        )
    ).scalars()
    for o in failed_recent:
        items.append(
            {
                "kind": "failed_order",
                "order_no": o.order_no,
                "user_id": o.user_id,
                "amount": str(o.amount),
                "detail": "渠道中间态/失败回调置 failed;查单收敛会自动救回已支付单,亦可人工补单",
                "created_at": o.created_at,
            }
        )
    reversed_recent = (
        await session.execute(
            select(Order)
            .where(Order.channel_reversed_at > now - timedelta(hours=48))
            .order_by(Order.id.desc())
            .limit(100)
        )
    ).scalars()
    for o in reversed_recent:
        items.append(
            {
                "kind": "channel_reversed",
                "order_no": o.order_no,
                "user_id": o.user_id,
                "amount": str(o.amount),
                "detail": "已入账订单收到渠道关单/退款通知:钱包已等额冻结阻断消费;"
                "核实后经 /finance/reversals/{order_no}/resolve 解冻(噪音单)或扣回(确认反转)",
                "created_at": o.channel_reversed_at,
            }
        )
    negative = (
        await session.execute(select(Wallet).where(Wallet.balance < 0).limit(100))
    ).scalars()
    for w in negative:
        items.append(
            {
                "kind": "negative_balance",
                "order_no": None,
                "user_id": w.user_id,
                "amount": str(w.balance),
                "detail": "钱包负余额(欠费回收后残留),可调账核销",
                "created_at": w.updated_at,
            }
        )
    return items


async def close_expired_orders(sm: async_sessionmaker[AsyncSession]) -> int:
    """关闭超时未支付订单(定时任务,每 10 分钟)。"""
    async with sm() as session:
        result = await session.execute(
            update(Order)
            .where(Order.status == "pending", Order.expires_at < now_utc())
            .values(status="closed")
        )
        await session.commit()
        count = result.rowcount  # type: ignore[attr-defined]
        if count:
            logger.info("expired_orders_closed", count=count)
        return count
