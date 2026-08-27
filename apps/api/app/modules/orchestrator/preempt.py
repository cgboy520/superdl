"""竞价抢占:按量/包周期请求容量不足时,回收最晚创建的竞价实例腾位置。

三条硬规矩,每一条都直接对应用户在知情同意里看到的那句话:

1. **只在同池同型号内选。** 回收一台 RTX4090 腾不出 A100 的位置,跨池更是连调度域都不同。
2. **按 `created_at DESC`(最晚创建先回收)。** 这是唯一的排序规则,写在知情同意里,
   用户据它判断自己的实例有多安全。任何「按用量」「按价格」的聪明排序都会让这句承诺变假。
3. **凑不够就一台都不动。** 半途回收既杀了竞价用户、又没救成请求方 —— 两头落空是最坏结果。

抢占与请求方的建实例在**同一个事务**里:请求方后续任何一步失败(余额不足、幂等撞车、
配额超限)都会把回收一起回滚,不会出现「杀了人但单没开成」。

宽限窗靠 outbox 的延迟投递实现:状态机立刻迁到 stopping(用户马上看见「即将回收」),
`instance.stop` 却推迟 `spot_grace_seconds` 才执行,期间 Pod 还在、SSH 还能登。
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

    「一台实例腾出 gpu_count 张卡」是近似口径:共享档的实例只占一张卡的一部分,
    回收它未必真空出整张卡。整个软准入模型本来就建立在「一张卡要么空要么满」的
    近似上(见 `_sku_free_capacity`),这里沿用同一套近似而不是另造一套更精确的 ——
    两套口径并存,才是真正说不清的那种 bug。近似偏乐观的后果是抢占后仍调度不上,
    那条路径已经有兜底:creating 超时转 failed、全额不出账。
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
    """回收选中的竞价实例。**不 commit** —— 必须与请求方的建实例同事务。

    迁 stopping 会触发计费边监听器出尾账,按到此刻为止的实际运行秒数结算:
    宽限窗那 60 秒不计费(平台单方面决定回收,不该让用户为等待期买单),
    而结算引擎完全不知道「抢占」这回事,`edge_listener` 一行没改。
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
