"""SSH 端口池 30000–32767(由 service.py 再导出)。
`ssh_port_excluded` 预先跳过已知占用,`blocked` 由 handle_create 撞占后标记;分配在段内随机。
"""

from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, text, update
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
    """分配一个 SSH NodePort;已分配则原样返回。
    扩段用 on_conflict_do_nothing,落空重试 8 次,超出回 NO_CAPACITY 由 outbox 退避兜底。
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    settings = get_settings()
    mine = (
        await session.execute(
            select(PortAllocation).where(PortAllocation.instance_id == instance.id)
        )
    ).scalar_one_or_none()
    if mine is not None:
        return mine.port
    for _ in range(8):
        # 复用空闲行:随机取
        free = (
            await session.execute(
                select(PortAllocation)
                .where(PortAllocation.instance_id.is_(None), PortAllocation.blocked.is_(False))
                .order_by(func.random())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if free is not None:
            free.instance_id = instance.id
            await session.flush()
            return free.port
        # 扩段:段内随机挑未占端口(generate_series 差集)
        candidate = (
            await session.execute(
                text(
                    "SELECT p FROM generate_series(CAST(:start AS int), CAST(:end AS int)) AS p"
                    " WHERE p <> ALL(CAST(:excluded AS int[]))"
                    " AND NOT EXISTS (SELECT 1 FROM port_allocations WHERE port = p)"
                    " ORDER BY random() LIMIT 1"
                ),
                {
                    "start": settings.ssh_port_range_start,
                    "end": settings.ssh_port_range_end,
                    "excluded": sorted(settings.ssh_port_excluded),
                },
            )
        ).scalar_one_or_none()
        if candidate is None:
            raise AppError(ErrorCode.NO_CAPACITY, key="orchestrator.sshPortsExhausted")
        inserted = (
            await session.execute(
                pg_insert(PortAllocation)
                .values(port=candidate, instance_id=instance.id)
                .on_conflict_do_nothing(index_elements=["port"])
                .returning(PortAllocation.port)
            )
        ).scalar_one_or_none()
        if inserted is not None:
            return inserted
        # 撞段:下一轮重查
    raise AppError(ErrorCode.NO_CAPACITY, key="orchestrator.sshPortsExhausted")


async def block_port(sm: Any, port: int, *, reason: str, expected_instance_id: int | None) -> None:
    """把被集群其它对象占用的端口标 blocked,独立事务提交;调用方须先 rollback。
    仅当端口空闲或正分配给 expected_instance_id 时才落 blocked。
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
    """端口池水位(管理端 /nodes 页)。"""
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
    """活跃实例(creating/starting/running)按 SKU 的 GPU 张数合计。"""
    rows = await session.execute(
        select(Instance.sku_id, func.coalesce(func.sum(Instance.gpu_count), 0))
        .where(Instance.status.in_((sm_def.CREATING, sm_def.STARTING, sm_def.RUNNING)))
        .group_by(Instance.sku_id)
    )
    return {sku_id: int(total) for sku_id, total in rows.all()}
