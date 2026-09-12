"""编排只读查询:实例 / 事件 / 数据盘的读取与聚合。
不依赖 billing;billing 与各巡检直接 import 本模块。"""

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import not_found
from app.core.money import hourly_cost
from app.core.pricing import MARKET_SPOT, MARKET_SUBSCRIPTION
from app.modules.orchestrator import statemachine as sm_def
from app.modules.orchestrator.models import DataDisk, Instance, InstanceEvent

# 数据盘计费态 / 欠费链状态:日结、燃烧率与欠费巡检据此选盘;grace(欠费宽限)停计费,frozen 不计费
DISK_BILLABLE_STATUSES: tuple[str, ...] = ("active",)
DISK_ARREARS_CHAIN_STATUSES: tuple[str, ...] = ("active", "grace", "frozen")


async def lock_instance(session: AsyncSession, instance_id: int) -> Instance | None:
    """FOR UPDATE 锁实例行并重读;不存在返回 None。"""
    return await session.get(Instance, instance_id, with_for_update=True, populate_existing=True)


async def instance_by_id(session: AsyncSession, instance_id: int) -> Instance:
    """按主键取实例(不限归属与状态);系统侧用,用户请求走 get_instance。"""
    return (await session.execute(select(Instance).where(Instance.id == instance_id))).scalar_one()


async def instance_status(session: AsyncSession, instance_id: int) -> str | None:
    """按主键取实例状态;不存在返回 None(不抛)。"""
    instance = await session.get(Instance, instance_id)
    return None if instance is None else instance.status


async def get_instance(session: AsyncSession, user_id: int, uuid: str) -> Instance:
    """按 (owner, uuid) 取实例;不存在或非属主 → 404。"""
    instance = (
        await session.execute(
            select(Instance).where(Instance.uuid == uuid, Instance.user_id == user_id)
        )
    ).scalar_one_or_none()
    if instance is None:
        raise not_found("实例不存在")
    return instance


async def pending_hourly(session: AsyncSession, user_id: int) -> Decimal:
    """该用户 creating/starting 实例的时费合计,由 wallet.assert_can_afford 内部并入。"""
    rows = (
        (
            await session.execute(
                select(Instance.price_hourly, Instance.gpu_count).where(
                    Instance.user_id == user_id,
                    Instance.status.in_((sm_def.CREATING, sm_def.STARTING)),
                    # 包周期实例已预付,不计
                    Instance.market != MARKET_SUBSCRIPTION,
                )
            )
        )
        .tuples()
        .all()
    )
    return sum((hourly_cost(price, count) for price, count in rows), Decimal("0.00"))


async def lock_instance_for_billing(session: AsyncSession, instance_id: int) -> None:
    """结算前 FOR UPDATE 锁实例行再读事件;锁序 instance → bill_hourly → wallet。"""
    await session.execute(
        select(Instance.id)
        .where(Instance.id == instance_id)
        # 必须 FOR UPDATE(FOR KEY SHARE 挡不住 transition 的 UPDATE)
        .with_for_update()
    )


async def billing_events_before(
    session: AsyncSession, instance_id: int, before: Any
) -> list[tuple[Any, str | None, str, Any]]:
    """实例截至某时刻的事件 (created_at, from_status, to_status, event_metadata),按发生序;
    node_lost/pod_lost 边的 metadata 带 unready_since 供结算截断。"""
    return list(
        (
            await session.execute(
                select(
                    InstanceEvent.created_at,
                    InstanceEvent.from_status,
                    InstanceEvent.to_status,
                    InstanceEvent.event_metadata,
                )
                .where(
                    InstanceEvent.instance_id == instance_id,
                    InstanceEvent.created_at < before,
                )
                .order_by(InstanceEvent.id)
            )
        )
        .tuples()
        .all()
    )


async def billing_history_exists_before(session: AsyncSession, kind: str, before: Any) -> bool:
    """结算引导判据:窗口起点之前是否存在过可计费对象(hourly 看实例事件,daily_disk 看数据盘)。"""
    if kind == "daily_disk":
        stmt = select(DataDisk.id).where(DataDisk.created_at < before).limit(1)
    else:
        stmt = select(InstanceEvent.id).where(InstanceEvent.created_at < before).limit(1)
    return (await session.execute(stmt)).first() is not None


async def billing_candidates(
    session: AsyncSession, window_start: Any
) -> list[tuple[int, int, Any, int]]:
    """小时结算候选:(instance_id, user_id, price_hourly, gpu_count) =
    当前 running ∪ 窗口起点以来离开过 running 的实例;包周期实例只在此处跳过。"""
    running_now = select(Instance.id.label("iid")).where(Instance.status == sm_def.RUNNING)
    exited = (
        select(InstanceEvent.instance_id.label("iid"))
        .where(
            InstanceEvent.from_status == sm_def.RUNNING,
            InstanceEvent.created_at >= window_start,
        )
        .distinct()
    )
    candidates = [row[0] for row in (await session.execute(union(running_now, exited))).all()]
    return [
        _billing_row(i)
        for i in await instances_by_ids(session, candidates)
        if i.market != MARKET_SUBSCRIPTION
    ]


def _billing_row(i: Instance) -> tuple[int, int, Any, int]:
    return (i.id, i.user_id, i.price_hourly, i.gpu_count)


async def instances_by_ids(session: AsyncSession, instance_ids: Iterable[int]) -> list[Instance]:
    """按 id 批量取实例(不限状态);下面各投影都从这一份取。"""
    ids = list(instance_ids)
    if not ids:
        return []
    return list((await session.execute(select(Instance).where(Instance.id.in_(ids)))).scalars())


async def instance_locations(
    session: AsyncSession, instance_ids: Iterable[int]
) -> dict[int, tuple[str, str, str | None]]:
    """metering 聚合用:instance_id → (k8s_namespace, uuid, pool_label)。"""
    return {
        i.id: (i.k8s_namespace, i.uuid, (i.spec or {}).get("pool_label"))
        for i in await instances_by_ids(session, instance_ids)
    }


async def instance_hourly_prices(
    session: AsyncSession, instance_ids: Iterable[int]
) -> dict[int, Any]:
    """对账用:instance_id → 时费(单价 × 计费份数;CPU 实例份数恒 1)。"""
    return {
        i.id: hourly_cost(i.price_hourly, i.gpu_count)
        for i in await instances_by_ids(session, instance_ids)
    }


async def instance_names(session: AsyncSession, instance_ids: Iterable[int]) -> dict[int, str]:
    """账单展示用:instance_id → 实例名(释放后行保留,改名跟当前名)。"""
    return {i.id: i.name for i in await instances_by_ids(session, instance_ids)}


async def list_running_instances_by_user(session: AsyncSession) -> dict[int, list[Instance]]:
    """欠费巡检用:user_id → running 实例列表。"""
    rows = (
        (await session.execute(select(Instance).where(Instance.status == sm_def.RUNNING)))
        .scalars()
        .all()
    )
    by_user: dict[int, list[Instance]] = {}
    for inst in rows:
        by_user.setdefault(inst.user_id, []).append(inst)
    return by_user


async def running_instances_of_user(session: AsyncSession, user_id: int) -> list[Instance]:
    """单用户 running 实例(钱包锁内路径,不扫全平台)。"""
    return list(
        (
            await session.execute(
                select(Instance).where(
                    Instance.user_id == user_id, Instance.status == sm_def.RUNNING
                )
            )
        )
        .scalars()
        .all()
    )


async def billable_disks_of_user(session: AsyncSession, user_id: int) -> list[DataDisk]:
    """单用户计费态盘(DISK_BILLABLE_STATUSES)。"""
    return list(
        (
            await session.execute(
                select(DataDisk).where(
                    DataDisk.user_id == user_id, DataDisk.status.in_(DISK_BILLABLE_STATUSES)
                )
            )
        )
        .scalars()
        .all()
    )


async def count_instances_by_status(session: AsyncSession) -> dict[str, int]:
    """各状态实例数(GROUP BY,一条 SQL,不物化行)。"""
    rows = (
        await session.execute(select(Instance.status, func.count()).group_by(Instance.status))
    ).all()
    return {str(status): int(n) for status, n in rows}


async def list_instances_by_status(session: AsyncSession, status: str) -> list[Instance]:
    return list(
        (await session.execute(select(Instance).where(Instance.status == status))).scalars()
    )


async def instance_disk_stats_by_user(
    session: AsyncSession, user_ids: list[int]
) -> dict[int, dict[str, int]]:
    """管理端租户表:user_id → {instances, disk_gb},只聚合给定用户。"""
    if not user_ids:
        return {}
    inst_stmt = (
        select(Instance.user_id, func.count())
        .where(Instance.status != sm_def.RELEASED, Instance.user_id.in_(user_ids))
        .group_by(Instance.user_id)
    )
    disk_stmt = (
        select(DataDisk.user_id, func.coalesce(func.sum(DataDisk.size_gb), 0))
        .where(DataDisk.status != "deleted", DataDisk.user_id.in_(user_ids))
        .group_by(DataDisk.user_id)
    )
    inst_rows = (await session.execute(inst_stmt)).tuples().all()
    disk_rows = (await session.execute(disk_stmt)).tuples().all()
    stats: dict[int, dict[str, int]] = {}
    for uid, n in inst_rows:
        stats.setdefault(uid, {"instances": 0, "disk_gb": 0})["instances"] = int(n)
    for uid, gb in disk_rows:
        stats.setdefault(uid, {"instances": 0, "disk_gb": 0})["disk_gb"] = int(gb)
    return stats


async def deletion_leftovers(session: AsyncSession, user_id: int) -> dict[str, list[str]]:
    """注销前置校验:未释放实例(非 released/failed)与未删除数据盘的 uuid 清单。"""
    instances = (
        (
            await session.execute(
                select(Instance.uuid).where(
                    Instance.user_id == user_id,
                    Instance.status.notin_((sm_def.RELEASED, sm_def.FAILED)),
                )
            )
        )
        .scalars()
        .all()
    )
    disks = (
        (
            await session.execute(
                select(DataDisk.uuid).where(
                    DataDisk.user_id == user_id, DataDisk.status != "deleted"
                )
            )
        )
        .scalars()
        .all()
    )
    return {"instances": list(instances), "disks": list(disks)}


async def deletion_leftover_counts(
    session: AsyncSession, user_ids: list[int]
) -> dict[int, dict[str, int]]:
    """deletion_leftovers 的批量版(管理端注销申请列表):user_id → {instances, disks} 计数。"""
    if not user_ids:
        return {}
    inst_rows = (
        (
            await session.execute(
                select(Instance.user_id, func.count())
                .where(
                    Instance.user_id.in_(user_ids),
                    Instance.status.notin_((sm_def.RELEASED, sm_def.FAILED)),
                )
                .group_by(Instance.user_id)
            )
        )
        .tuples()
        .all()
    )
    disk_rows = (
        (
            await session.execute(
                select(DataDisk.user_id, func.count())
                .where(DataDisk.user_id.in_(user_ids), DataDisk.status != "deleted")
                .group_by(DataDisk.user_id)
            )
        )
        .tuples()
        .all()
    )
    counts: dict[int, dict[str, int]] = {}
    for uid, n in inst_rows:
        counts.setdefault(uid, {"instances": 0, "disks": 0})["instances"] = int(n)
    for uid, n in disk_rows:
        counts.setdefault(uid, {"instances": 0, "disks": 0})["disks"] = int(n)
    return counts


async def running_gpu_share_by_pool(session: AsyncSession) -> dict[str, float]:
    """超卖报表:各池已售算力份额(等效整卡数)。共享档按 gpu_cores_pct 折算。"""
    rows = (
        (
            await session.execute(
                select(Instance).where(
                    Instance.status.in_((sm_def.RUNNING, sm_def.STARTING, sm_def.CREATING))
                )
            )
        )
        .scalars()
        .all()
    )
    by_pool: dict[str, float] = {}
    for inst in rows:
        pool = inst.spec["pool_label"]
        share = inst.gpu_count * (inst.spec["gpu_cores_pct"] / 100.0)
        by_pool[pool] = by_pool.get(pool, 0.0) + share
    return by_pool


async def running_spot_gpus_by_pool(session: AsyncSession) -> dict[str, int]:
    """池 → running 竞价实例占用卡数合计(device 计数);
    超卖档可能大于台账 gpu_used,调用方按已租截断。"""
    # Python 侧聚合(参数化 JSONB 表达式不能进 GROUP BY)
    rows = (
        (
            await session.execute(
                select(Instance.spec, Instance.gpu_count).where(
                    Instance.status == sm_def.RUNNING, Instance.market == MARKET_SPOT
                )
            )
        )
        .tuples()
        .all()
    )
    by_pool: dict[str, int] = {}
    for spec, gpus in rows:
        pool = (spec or {}).get("pool_label")
        if pool:
            by_pool[pool] = by_pool.get(pool, 0) + int(gpus)
    return by_pool


async def pool_by_instance(session: AsyncSession, instance_ids: Iterable[int]) -> dict[int, str]:
    """实例 → 池标签(不限状态,含已释放)。"""
    return {i.id: i.spec["pool_label"] for i in await instances_by_ids(session, instance_ids)}


# ---------- 数据盘(计费与巡检的读取面) ----------


async def instance_billing_snapshot(
    session: AsyncSession, instance_id: int
) -> tuple[int, int, Any, int] | None:
    """单实例计费快照:(id, user_id, price_hourly, gpu_count);不存在返回 None(缺口重放用)。"""
    rows = await instances_by_ids(session, [instance_id])
    return _billing_row(rows[0]) if rows else None


async def disk_billing_snapshot(
    session: AsyncSession, disk_id: int
) -> tuple[int, int, Any, int] | None:
    """单盘计费快照:(id, user_id, price_gb_month, size_gb);不存在返回 None(缺口重放用)。"""
    return (
        (
            await session.execute(
                select(
                    DataDisk.id, DataDisk.user_id, DataDisk.price_gb_month, DataDisk.size_gb
                ).where(DataDisk.id == disk_id)
            )
        )
        .tuples()
        .one_or_none()
    )


async def billable_disks(session: AsyncSession) -> list[DataDisk]:
    """全平台计费态盘(日结与整窗重放共用)。"""
    return list(
        (
            await session.execute(
                select(DataDisk).where(DataDisk.status.in_(DISK_BILLABLE_STATUSES))
            )
        ).scalars()
    )


async def arrears_chain_disk_user_ids(session: AsyncSession) -> list[int]:
    """欠费巡检的用户集合:名下有任何一块 active/grace/frozen 盘。"""
    return list(
        (
            await session.execute(
                select(DataDisk.user_id)
                .where(DataDisk.status.in_(DISK_ARREARS_CHAIN_STATUSES))
                .distinct()
            )
        ).scalars()
    )
