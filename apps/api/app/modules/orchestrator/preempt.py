"""竞价抢占:按量/包周期请求容量不足时,回收最晚创建的竞价实例腾位置。

三条硬规矩,每条都对应知情同意里的一句承诺:只在同池同型号内选;按 `created_at DESC`
(最晚创建先回收,不得改成按用量或价格排序);凑不够就一台都不动。

抢占必须与请求方的建实例同事务:请求方后续任何一步失败都会把回收一起回滚。
宽限窗靠 outbox 延迟投递实现:状态机立刻迁 stopping,`instance.stop` 推迟
`spot_grace_seconds` 才执行,期间 Pod 还在、SSH 还能登。
"""

import math
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.metrics import SPOT_PREEMPTED_TOTAL
from app.core.outbox import enqueue
from app.core.pricing import MARKET_SPOT
from app.modules.orchestrator import statemachine as sm_def
from app.modules.orchestrator.models import Instance

if TYPE_CHECKING:
    from app.modules.catalog.models import Sku

logger = get_logger(__name__)

REASON_PREEMPTED = "preempted"


async def pick_victims(
    session: AsyncSession,
    *,
    pool_label: str,
    gpu_model_selector: str | None,
    need_cards: int,
) -> list[Instance]:
    """选出够腾 `need_cards` 张卡的竞价实例(最晚创建的先选)。凑不够返回空列表。

    「一台实例腾出 gpu_count 张卡」是近似口径(与 `_sku_free_capacity` 同源):共享档实例
    只占一张卡的一部分,近似偏乐观的后果由 creating 超时转 failed、全额不出账兜底。
    """
    if need_cards <= 0:
        return []
    rows = list(
        (
            await session.execute(
                select(Instance)
                .where(
                    Instance.status == sm_def.RUNNING,
                    Instance.market == MARKET_SPOT,
                    Instance.spec["pool_label"].astext == pool_label,
                    Instance.spec["gpu_model_selector"].astext.is_not_distinct_from(
                        gpu_model_selector
                    ),
                )
                .order_by(Instance.created_at.desc(), Instance.id.desc())
            )
        ).scalars()
    )
    picked: list[Instance] = []
    freed = 0
    for inst in rows:
        picked.append(inst)
        freed += max(1, inst.gpu_count)
        if freed >= need_cards:
            return picked
    return []  # 凑不够:一台都不动


def cards_needed(*, deficit_slots: int, slots_per_card: int) -> int:
    """把「还差几个本 SKU 的槽位」换算成「还差几张卡」。槽位口径见 catalog.sellable_per_gpu。"""
    if deficit_slots <= 0:
        return 0
    return math.ceil(deficit_slots / max(1, slots_per_card))


async def preempt(
    session: AsyncSession,
    victims: list[Instance],
    *,
    grace_seconds: int,
    requested_by: int,
    admin_reason: str | None = None,
) -> None:
    """回收选中的竞价实例。不 commit —— 必须与请求方的建实例同事务。

    迁 stopping 触发计费边监听器出尾账,按到此刻为止的实际运行秒数结算,宽限窗不计费;
    结算引擎对「抢占」无感知,走的仍是 `edge_listener` 的通用尾账路径。
    """
    # 延迟 import 防循环:service → preempt → service
    from app.modules.notify import service as notify_service
    from app.modules.orchestrator.service import transition

    for inst in victims:
        await transition(
            session,
            inst,
            sm_def.STOPPING,
            reason=REASON_PREEMPTED,
            actor="system",
            metadata={
                "grace_seconds": grace_seconds,
                "requested_by": requested_by,
                **({"admin_reason": admin_reason} if admin_reason else {}),
            },
        )
        enqueue(
            session,
            "instance.stop",
            {"instance_id": inst.id},
            delay_seconds=grace_seconds,
        )
        await notify_service.send_preemption_notice(
            session,
            inst.user_id,
            instance_name=inst.name,
            grace_seconds=grace_seconds,
            instance_id=inst.id,
            instance_uuid=inst.uuid,
        )
        SPOT_PREEMPTED_TOTAL.inc()
        logger.warning(
            "spot_instance_preempted",
            instance_id=inst.id,
            user_id=inst.user_id,
            gpu_count=inst.gpu_count,
            grace_seconds=grace_seconds,
            requested_by=requested_by,
        )


async def try_free_capacity(
    session: AsyncSession,
    *,
    sku: "Sku",
    deficit_slots: int,
    slots_per_card: int,
    grace_seconds: int,
    requested_by: int,
) -> bool:
    """尝试靠抢占补齐缺口。腾得出返回 True(已在本事务内下发回收),否则 False。"""
    from app.core.gpu_models import canonical_gpu_model

    need = cards_needed(deficit_slots=deficit_slots, slots_per_card=slots_per_card)
    victims = await pick_victims(
        session,
        pool_label=sku.pool_label,
        gpu_model_selector=canonical_gpu_model(sku.gpu_model),
        need_cards=need,
    )
    if not victims:
        return False
    await preempt(session, victims, grace_seconds=grace_seconds, requested_by=requested_by)
    return True
