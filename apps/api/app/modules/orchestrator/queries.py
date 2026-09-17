"""Instance, event and data-disk queries and aggregates."""

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, or_, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import not_found
from app.core.money import hourly_cost
from app.core.pricing import MARKET_SPOT, MARKET_SUBSCRIPTION
from app.modules.orchestrator import statemachine as sm_def
from app.modules.orchestrator.models import DataDisk, Instance, InstanceEvent

DISK_BILLABLE_STATUSES: tuple[str, ...] = ("active",)
DISK_ARREARS_CHAIN_STATUSES: tuple[str, ...] = ("active", "grace", "frozen")


async def lock_instance(session: AsyncSession, instance_id: int) -> Instance | None:
    """FOR UPDATE lock the instance row and re-read; None when missing."""
    return await session.get(Instance, instance_id, with_for_update=True, populate_existing=True)


async def instance_by_id(session: AsyncSession, instance_id: int) -> Instance:
    """Fetch the instance by primary key (any owner or status); system-side use, user requests go
    through get_instance."""
    return (await session.execute(select(Instance).where(Instance.id == instance_id))).scalar_one()


async def instance_status(session: AsyncSession, instance_id: int) -> str | None:
    """Fetch the instance status by primary key; None when missing (no raise)."""
    instance = await session.get(Instance, instance_id)
    return None if instance is None else instance.status


async def get_instance(session: AsyncSession, user_id: int, uuid: str) -> Instance:
    """Fetch the instance by (owner, uuid); missing or not the owner → 404."""
    instance = (
        await session.execute(
            select(Instance).where(Instance.uuid == uuid, Instance.user_id == user_id)
        )
    ).scalar_one_or_none()
    if instance is None:
        raise not_found(key="orchestrator.instanceNotFound")
    return instance


async def pending_hourly(session: AsyncSession, user_id: int) -> Decimal:
    """Hourly cost of the user's creating/starting instances, merged in by
    wallet.assert_can_afford."""
    rows = (
        (
            await session.execute(
                select(Instance.price_hourly, Instance.gpu_count).where(
                    Instance.user_id == user_id,
                    Instance.status.in_((sm_def.CREATING, sm_def.STARTING)),
                    Instance.market != MARKET_SUBSCRIPTION,
                )
            )
        )
        .tuples()
        .all()
    )
    return sum((hourly_cost(price, count) for price, count in rows), Decimal("0.00"))


async def lock_instance_for_billing(session: AsyncSession, instance_id: int) -> None:
    """FOR UPDATE lock the instance row before settlement, then read the events; lock order instance
    → bill_hourly → wallet."""
    await session.execute(select(Instance.id).where(Instance.id == instance_id).with_for_update())


async def billing_events(
    session: AsyncSession, instance_id: int
) -> list[tuple[Any, str | None, str, Any]]:
    """Every event of the instance (created_at, from_status, to_status, event_metadata) in
    occurrence order; window clipping is done by
    settlement (a failure edge after the window may carry occupied_since / unready_since, deciding
    the billing bounds inside the window)."""
    return list(
        (
            await session.execute(
                select(
                    InstanceEvent.created_at,
                    InstanceEvent.from_status,
                    InstanceEvent.to_status,
                    InstanceEvent.event_metadata,
                )
                .where(InstanceEvent.instance_id == instance_id)
                .order_by(InstanceEvent.id)
            )
        )
        .tuples()
        .all()
    )


async def billing_history_exists_before(session: AsyncSession, kind: str, before: Any) -> bool:
    """Settlement bootstrap criterion: whether a billable object existed before the window start
    (hourly looks at instance events, daily_disk at data disks)."""
    if kind == "daily_disk":
        stmt = select(DataDisk.id).where(DataDisk.created_at < before).limit(1)
    else:
        stmt = select(InstanceEvent.id).where(InstanceEvent.created_at < before).limit(1)
    return (await session.execute(stmt)).first() is not None


async def billing_candidates(
    session: AsyncSession, window_start: Any
) -> list[tuple[int, int, Any, int]]:
    """Hourly settlement candidates: (instance_id, user_id, price_hourly, gpu_count) =
    currently running ∪ instances that left running or went creating/starting→failed since the
    window start;
    subscription instances are skipped here only."""
    running_now = select(Instance.id.label("iid")).where(Instance.status == sm_def.RUNNING)
    exited = (
        select(InstanceEvent.instance_id.label("iid"))
        .where(
            or_(
                InstanceEvent.from_status == sm_def.RUNNING,
                and_(
                    InstanceEvent.to_status == sm_def.FAILED,
                    InstanceEvent.from_status.in_((sm_def.CREATING, sm_def.STARTING)),
                ),
            ),
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
    """Fetch instances by id in batch, any status."""
    ids = list(instance_ids)
    if not ids:
        return []
    return list((await session.execute(select(Instance).where(Instance.id.in_(ids)))).scalars())


async def instance_locations(
    session: AsyncSession, instance_ids: Iterable[int]
) -> dict[int, tuple[str, str, str | None]]:
    """For metering aggregation: instance_id → (k8s_namespace, uuid, pool_label)."""
    return {
        i.id: (i.k8s_namespace, i.uuid, (i.spec or {}).get("pool_label"))
        for i in await instances_by_ids(session, instance_ids)
    }


async def instance_hourly_prices(
    session: AsyncSession, instance_ids: Iterable[int]
) -> dict[int, Any]:
    """For reconciliation: instance_id → hourly cost (unit price × billing units; CPU instances
    always 1 unit)."""
    return {
        i.id: hourly_cost(i.price_hourly, i.gpu_count)
        for i in await instances_by_ids(session, instance_ids)
    }


async def instance_names(session: AsyncSession, instance_ids: Iterable[int]) -> dict[int, str]:
    """For bill display: instance_id → instance name (rows survive release, renames follow the
    current name)."""
    return {i.id: i.name for i in await instances_by_ids(session, instance_ids)}


async def list_running_instances_by_user(session: AsyncSession) -> dict[int, list[Instance]]:
    """For the arrears patrol: user_id → running instances."""
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
    """Running instances of one user (wallet-lock path, no platform-wide scan)."""
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
    """Billable disks of one user (DISK_BILLABLE_STATUSES)."""
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
    """Instance counts aggregated by status."""
    rows = (
        await session.execute(select(Instance.status, func.count()).group_by(Instance.status))
    ).all()
    return {str(status): int(n) for status, n in rows}


async def count_active_instances_on_node(session: AsyncSession, node_name: str) -> int:
    """Instances on the node with status != released, stopped/frozen/failed/releasing included."""
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(Instance)
                .where(Instance.node_name == node_name, Instance.status != sm_def.RELEASED)
            )
        ).scalar_one()
    )


async def count_active_instances_by_node(session: AsyncSession) -> dict[str, int]:
    """Node name → unreleased instance count; same definition as count_active_instances_on_node."""
    rows = (
        await session.execute(
            select(Instance.node_name, func.count())
            .where(Instance.node_name.is_not(None), Instance.status != sm_def.RELEASED)
            .group_by(Instance.node_name)
        )
    ).all()
    return {str(name): int(n) for name, n in rows}


async def list_instances_by_status(session: AsyncSession, status: str) -> list[Instance]:
    return list(
        (await session.execute(select(Instance).where(Instance.status == status))).scalars()
    )


async def instance_disk_stats_by_user(
    session: AsyncSession, user_ids: list[int]
) -> dict[int, dict[str, int]]:
    """Admin tenant table: user_id → {instances, disk_gb}, aggregated for the given users only."""
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
    """Deletion pre-check: uuids of unreleased instances (not released/failed) and non-deleted data
    disks."""
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
    """Batch variant of deletion_leftovers (admin deletion request list): user_id → {instances,
    disks} counts."""
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
    """Oversell report: sold compute share per pool (whole-card equivalents). The shared tier
    converts by gpu_cores_pct."""
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
    """Pool → cards held by running spot instances (device count);
    may exceed the inventory gpu_used in oversold tiers, the caller caps by rented."""
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
    """Instance → pool label (any status, released included)."""
    return {i.id: i.spec["pool_label"] for i in await instances_by_ids(session, instance_ids)}


async def instance_billing_snapshot(
    session: AsyncSession, instance_id: int
) -> tuple[int, int, Any, int] | None:
    """Billing snapshot of one instance: (id, user_id, price_hourly, gpu_count); None when missing
    (for gap replay)."""
    rows = await instances_by_ids(session, [instance_id])
    return _billing_row(rows[0]) if rows else None


async def disk_billing_snapshot(
    session: AsyncSession, disk_id: int
) -> tuple[int, int, Any, int] | None:
    """Billing snapshot of one disk: (id, user_id, price_gb_month, size_gb); None when missing (for
    gap replay)."""
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
    """Platform-wide billable disks (shared by daily settlement and whole-window replay)."""
    return list(
        (
            await session.execute(
                select(DataDisk).where(DataDisk.status.in_(DISK_BILLABLE_STATUSES))
            )
        ).scalars()
    )


async def arrears_chain_disk_user_ids(session: AsyncSession) -> list[int]:
    """User set of the arrears patrol: anyone owning an active/grace/frozen disk."""
    return list(
        (
            await session.execute(
                select(DataDisk.user_id)
                .where(DataDisk.status.in_(DISK_ARREARS_CHAIN_STATUSES))
                .distinct()
            )
        ).scalars()
    )
