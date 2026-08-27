"""计费边监听器:实例离开 running 的瞬间,与状态迁移同事务出尾账。

由 wire_modules() 注册到 orchestrator 的 transition 监听器,尾账与状态迁移原子提交。
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.pricing import MARKET_SUBSCRIPTION
from app.core.timeutil import ensure_utc, hour_floor
from app.modules.billing import subscriptions
from app.modules.billing.settlement import settle_instance_window

if TYPE_CHECKING:
    from app.modules.orchestrator.models import Instance, InstanceEvent

logger = get_logger(__name__)

RUNNING = "running"
RELEASING = "releasing"

# 平台责任失联(节点失联/Pod 丢失):计费截断到 Pod 首次 not-ready 的时刻,
# 判定前的宽限观察期不向用户计费。pod_unready(节点正常,负载自身问题)不在此列。
_TRUNCATE_REASONS = ("node_lost", "pod_lost")


async def on_instance_transition(
    session: AsyncSession, instance: "Instance", event: "InstanceEvent"
) -> None:
    if event.to_status == RELEASING and instance.market == MARKET_SUBSCRIPTION:
        # 中途释放不退款(预付语义),但订阅必须作废:留着 active 会让软准入
        # 继续替一台已经不存在的实例预留容量,也会让到期巡检去停一台已释放的机器。
        # 挂在迁移监听器上而不是 release_instance 里,是为了把用户释放、欠费回收、
        # 到期回收、管理端强制回收四条路径一次覆盖 —— 它们最终都经过这条边
        await subscriptions.cancel_for_instance(session, instance.id)
    if event.from_status != RUNNING:
        return
    if instance.market == MARKET_SUBSCRIPTION:
        # 包周期离开 running 不出尾账:整段周期的钱在下单时已经收过了。
        # 三处配套过滤之一(另两处:wallet.assert_can_afford 的在途燃烧率、
        # billing.patrol 的停机判据),漏一处就是对预付用户二次收费
        return
    at = ensure_utc(event.created_at)
    detail_extra = None
    meta = event.event_metadata or {}
    if event.reason in _TRUNCATE_REASONS and meta.get("unready_since"):
        # 与 settlement._billing_view 同口径:截断到 Pod 首次 not-ready 时刻。窗口末必须
        # 在此自行取截断时刻——本次退出边(created_at == 窗口末)不在 billing_events_before
        # (严格 < 窗口末)的返回里,重建层看不到它的 metadata;整点/追平结算能看到,
        # 因此不会把宽限期秒数再补回来。unready_since 只在当前 running 段内由 reconciler
        # 写入(每条进入 running 的路径先清零),不存在上次失联残留的可能
        unready_at = ensure_utc(datetime.fromisoformat(str(meta["unready_since"])))
        if unready_at < at:
            at = unready_at
            detail_extra = {"truncated_at": meta["unready_since"], "truncate_reason": event.reason}
    charged = await settle_instance_window(
        session,
        instance_id=instance.id,
        user_id=instance.user_id,
        unit_price=instance.price_hourly,
        gpu_count=instance.gpu_count,
        window_start=hour_floor(at),
        window_end=at,
        source="tail",
        detail_extra=detail_extra,
    )
    if charged > 0:
        logger.info(
            "tail_bill_charged",
            instance_id=instance.id,
            amount=str(charged),
            reason=event.reason,
        )


_registered = False


def register_billing_edge_listener() -> None:
    global _registered
    if _registered:
        return
    from app.modules.orchestrator.service import register_transition_listener

    register_transition_listener(on_instance_transition)
    _registered = True
