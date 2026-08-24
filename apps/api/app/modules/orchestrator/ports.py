"""SSH 端口池(从 service.py 拆出,门面再导出):

端口池 30000–32767 与 K8s NodePort 同段,集群其它对象会硬占其中某些端口,两道防护:
`ssh_port_excluded` 预先跳过已知占用;`blocked` 由 handle_create 在运行期撞占后标记。
"""

from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.logging import get_logger
from app.modules.orchestrator import statemachine as sm_def
from app.modules.orchestrator.models import Instance, PortAllocation

if TYPE_CHECKING:
    from app.modules.orchestrator.schemas import PortPoolStatsOut

logger = get_logger(__name__)


async def ensure_port(session: AsyncSession, instance: Instance) -> int:
    """分配一个 SSH NodePort。已分配则原样返回(幂等)。"""
    settings = get_settings()
    mine = (
        await session.execute(
            select(PortAllocation).where(PortAllocation.instance_id == instance.id)
        )
    ).scalar_one_or_none()
    if mine is not None:
        return mine.port
    free = (
        await session.execute(
            select(PortAllocation)
            .where(PortAllocation.instance_id.is_(None), PortAllocation.blocked.is_(False))
            .order_by(PortAllocation.port)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if free is not None:
        free.instance_id = instance.id
        await session.flush()
        return free.port
    max_port = (await session.execute(select(func.max(PortAllocation.port)))).scalar_one()
    next_port = settings.ssh_port_range_start if max_port is None else max_port + 1
    while next_port in settings.ssh_port_excluded:
        next_port += 1
    if next_port > settings.ssh_port_range_end:
        raise AppError(ErrorCode.NO_CAPACITY, key="orchestrator.sshPortsExhausted")
    alloc = PortAllocation(port=next_port, instance_id=instance.id)
    session.add(alloc)
    await session.flush()
    return next_port


async def block_port(sm: Any, port: int, *, reason: str, expected_instance_id: int | None) -> None:
    """把一个被集群其它对象占用的端口标记为不可分配。独立事务提交(调用方那笔要回滚)。

    调用方须先 rollback 再调本函数,否则未提交的同端口 PortAllocation 会锁死这笔事务。
    防迟到的占用报告覆盖活分配:仅当该端口空闲、或正分配给发起本次报告的实例
    (expected_instance_id,重启换端口自愈路径)时才落 blocked;已分配给其它实例的
    端口说明报告已过时,跳过不破坏在用归属。
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    condition = PortAllocation.instance_id.is_(None)
    if expected_instance_id is not None:
        condition = condition | (PortAllocation.instance_id == expected_instance_id)
    async with sm() as session:
        await session.execute(
            pg_insert(PortAllocation)
            .values(port=port, instance_id=None, blocked=True)
            .on_conflict_do_update(
                index_elements=["port"],
                set_={"blocked": True, "instance_id": None},
                where=condition,
            )
        )
        await session.commit()
    logger.error("ssh_port_blocked", port=port, reason=reason)


async def free_port(session: AsyncSession, instance_id: int) -> None:
    await session.execute(
        update(PortAllocation)
        .where(PortAllocation.instance_id == instance_id)
        .values(instance_id=None)
    )


async def port_pool_stats(session: AsyncSession) -> "PortPoolStatsOut":
    """端口池水位(管理端 /nodes 页):blocked 计数是撞占标记与周期复检效果的观口。"""
    from app.modules.orchestrator.schemas import PortPoolStatsOut

    total, assigned, blocked = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(PortAllocation.instance_id.is_not(None)),
                func.count().filter(PortAllocation.blocked.is_(True)),
            ).select_from(PortAllocation)
        )
    ).one()
    return PortPoolStatsOut(total=total, assigned=assigned, blocked=blocked)


async def active_gpu_counts_by_sku(session: AsyncSession) -> dict[int, int]:
    """活跃实例按 SKU 的 GPU 张数合计(口径与用户配额一致:creating/starting/running)。"""
    rows = await session.execute(
        select(Instance.sku_id, func.coalesce(func.sum(Instance.gpu_count), 0))
        .where(Instance.status.in_((sm_def.CREATING, sm_def.STARTING, sm_def.RUNNING)))
        .group_by(Instance.sku_id)
    )
    return {sku_id: int(total) for sku_id, total in rows.all()}
