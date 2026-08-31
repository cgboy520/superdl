"""SSH 端口池(由 service.py 门面再导出):

端口池 30000–32767 与 K8s NodePort 同段,集群其它对象会硬占其中某些端口,两道防护:
`ssh_port_excluded` 预先跳过已知占用;`blocked` 由 handle_create 在运行期撞占后标记。
分配在段内随机(复用空闲行与扩段都随机):顺序分配让在用的 SSH 入口
恒占低段、可枚举;随机只是过渡减面,统一入口/连接审计由 SSH gateway 承担。
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
    """分配一个 SSH NodePort。已分配则原样返回(幂等)。

    并发同段扩段:新段插入用 on_conflict_do_nothing(撞唯一索引不抛错、
    等对方事务落定后自然落空),落空则重查空闲/最大值再试——冲突在函数内消化,
    不把整笔建实例事务打成 outbox 退避(白烧一次 ~10s 重试预算)。
    重试预算 8:扩段冲突会排队成链(第 N 个等待者最多撞 N-1 次),
    8 覆盖并发创建风暴的现实规模,超出仍回 NO_CAPACITY 由 outbox 退避兜底。
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
        # 复用空闲行:随机取(顺序分配会让在用 SSH 端口恒聚低段、可枚举,见模块 docstring)
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
        # 扩段:段内随机挑未占端口(generate_series 差集)。
        # 段长 ≤2768,差集+随机排序是微秒级;撞唯一索引由重试预算消化(并发同挑一个口)。
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
        # 撞段:胜出方可能扩了段或新标了 blocked,下一轮重查空闲行/随机候选
    raise AppError(ErrorCode.NO_CAPACITY, key="orchestrator.sshPortsExhausted")


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
