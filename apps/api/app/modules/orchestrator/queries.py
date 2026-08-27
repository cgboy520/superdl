"""编排查询聚合(从 service.py 拆出,门面再导出):

billing 结算/对账只读接口(事件是计费主依据,经 service 层暴露)、
管理端/巡检聚合查询、数据盘门面(模块边界)。
"""

from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import hourly_cost
from app.modules.orchestrator import statemachine as sm_def
from app.modules.orchestrator.models import DataDisk, Instance, InstanceEvent


async def lock_instance_for_billing(session: AsyncSession, instance_id: int) -> None:
    """结算前先拿实例行锁,再读事件。同事务内重复加锁是 no-op。

    transition() 首步 `UPDATE instances` 持该行写锁到提交,事件 created_at 在拿锁后生成。
    先拿锁则:在飞的迁移已提交(读得到),或迁移被挡住(其事件落进下一个小时窗口)。
    锁序 instance → bill_hourly → wallet,与 transition 一致。
    """
    await session.execute(
        select(Instance.id)
        .where(Instance.id == instance_id)
        .with_for_update(read=False, key_share=True)
    )


async def billing_events_before(
    session: AsyncSession, instance_id: int, before: Any
) -> list[tuple[Any, str | None, str, Any]]:
    """实例截至某时刻的事件 (created_at, from_status, to_status, event_metadata),按发生序。

    metadata 随行返回:node_lost/pod_lost 的退出边带 unready_since,结算据此把
    计费截断到 Pod 首次不可用时点(平台责任时段不向用户计费)。
    """
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


async def billing_candidates(
    session: AsyncSession, window_start: Any, window_end: Any
) -> list[tuple[int, int, Any, int]]:
    """小时结算候选:(instance_id, user_id, price_hourly, gpu_count)。

    候选 = 当前 running 的实例 ∪ 自窗口起点以来离开过 running 的实例。
    完备性论证:「窗口末仍在 running」= 现在仍 running ∪ 窗口末之后才离开 running;
    每个已结束的 running 区间都有一条 from_status='running' 的离开事件。
    两条腿都走索引(instances.status / instance_events.created_at)。
    """
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
    return [_billing_row(i) for i in await instances_by_ids(session, candidates)]


def _billing_row(i: Instance) -> tuple[int, int, Any, int]:
    return (i.id, i.user_id, i.price_hourly, i.gpu_count)


async def instances_by_ids(session: AsyncSession, instance_ids: Iterable[int]) -> list[Instance]:
    """按 id 精确取实例(不限状态,不走列表截断);下面各投影都从这一份取。"""
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


async def list_instances_by_status(session: AsyncSession, status: str) -> list[Instance]:
    return list(
        (await session.execute(select(Instance).where(Instance.status == status))).scalars()
    )


async def instance_disk_stats_by_user(
    session: AsyncSession, user_ids: list[int]
) -> dict[int, dict[str, int]]:
    """管理端租户表:user_id → {instances, disk_gb},只聚合给定(本页)用户,不做全表 GROUP BY。"""
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
    """注销前置校验:未释放实例(status 不在 released/failed 终态)与
    未删除数据盘(status != deleted)的 uuid 清单。空清单 = 资源已清空。"""
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


async def pool_by_instance(session: AsyncSession, instance_ids: Iterable[int]) -> dict[int, str]:
    """实例 → 池标签(不限状态,已释放实例也算:超卖报表按池聚合近 24h 利用率用)。"""
    return {i.id: i.spec["pool_label"] for i in await instances_by_ids(session, instance_ids)}


# ---------- 数据盘门面(billing/巡检经此访问,模块边界) ----------


async def instance_billing_snapshot(
    session: AsyncSession, instance_id: int
) -> tuple[int, int, Any, int] | None:
    """单实例计费快照:(id, user_id, price_hourly, gpu_count);不存在返回 None。
    缺口重放按 object_id 精确取价(结算缺口的补结必须是当时落库的快照价,非 SKU 现价)。"""
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


async def billable_disks(session: AsyncSession) -> list[Any]:
    from app.modules.orchestrator import disks as disks_service

    return await disks_service.list_billable_disks(session)


async def arrears_chain_disk_user_ids(session: AsyncSession) -> list[int]:
    from app.modules.orchestrator import disks as disks_service

    return await disks_service.list_arrears_chain_user_ids(session)


async def disks_arrears_transition(session: AsyncSession, user_id: int, in_arrears: bool) -> int:
    from app.modules.orchestrator import disks as disks_service

    return await disks_service.arrears_transition_disks(session, user_id, in_arrears)
