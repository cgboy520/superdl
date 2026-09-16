"""Arrears patrol by available balance: warnings, stops, freezes, reclamation and unfreezing;
grace durations come from the runtime policy."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.locks import LockKey, advisory_lock
from app.core.logging import get_logger
from app.core.metrics import WALLET_NEGATIVE_COUNT, WALLET_NEGATIVE_SUM
from app.core.money import as_amount, hourly_cost, money_label
from app.core.patrol import for_each
from app.core.platform_config import get_runtime_config
from app.core.pricing import MARKET_SUBSCRIPTION
from app.core.servercopy import copy as server_copy
from app.core.timeutil import hour_floor, now_utc
from app.modules.account import service as account_service
from app.modules.billing import wallet
from app.modules.billing.models import BillHourly, Wallet
from app.modules.billing.settlement import (
    DiskBillingInput,
    bill_amount,
    billing_view,
    get_watermark,
    running_seconds_in_window,
    settle_disk_pending_days,
)
from app.modules.notify import service as notify_service
from app.modules.orchestrator import (
    queries as orchestrator_queries,
    statemachine as sm_def,
    transitions as orchestrator_transitions,
)

if TYPE_CHECKING:
    from app.modules.orchestrator.models import DataDisk, Instance

logger = get_logger(__name__)


async def balance_patrol(sm: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    counts = {
        "warned": 0,
        "stopped": 0,
        "frozen": 0,
        "reclaimed": 0,
        "unfrozen": 0,
        "disks": 0,
    }
    async with advisory_lock(sm, LockKey.BALANCE_PATROL) as got:
        if not got:
            return counts
        await _patrol_frozen_tenants(sm, counts)
        await _patrol_running(sm, counts)
        await _patrol_frozen_and_arrears_stopped(sm, counts)
        await _patrol_disks(sm, counts)
        await _refresh_negative_balance_gauges(sm)
    logger.info("balance_patrol_done", **counts)
    return counts


async def _refresh_negative_balance_gauges(sm: async_sessionmaker[AsyncSession]) -> None:
    """Refresh the negative-wallet count and absolute negative-balance sum metrics."""
    async with sm() as session:
        count, total = (
            await session.execute(
                select(func.count(), func.coalesce(func.sum(-Wallet.balance), 0)).where(
                    Wallet.balance < 0
                )
            )
        ).one()
    WALLET_NEGATIVE_COUNT.set(int(count))
    WALLET_NEGATIVE_SUM.set(float(total))


async def _patrol_frozen_tenants(
    sm: async_sessionmaker[AsyncSession], counts: dict[str, int]
) -> None:
    """Stop the running instances of frozen accounts, one independent transaction per user."""
    async with sm() as session:
        frozen_user_ids = await account_service.frozen_user_ids(session)

    async def stop_all(user_id: int) -> None:
        async with sm() as session:
            stopped = await orchestrator_transitions.stop_all_for_user(
                session, user_id, reason="tenant_frozen"
            )
            await session.commit()
            counts["stopped"] += stopped

    await for_each(
        frozen_user_ids, stop_all, stage="frozen_tenant", ident=lambda uid: {"user_id": uid}
    )


async def _unsettled_burn(
    session: AsyncSession, inst: "Instance", now: datetime, settled_through: datetime | None
) -> Decimal:
    """Estimate unbilled consumption without posting; starts at the earlier of the current hour and
    the hour after the watermark.

    Events are rebuilt through the billing view (occupancy edges expanded, loss edges truncated)
    into running seconds minus billed seconds; at most 31 days of seconds.
    """
    h0 = hour_floor(now)
    start = h0 if settled_through is None else min(h0, settled_through + timedelta(hours=1))
    events = await orchestrator_queries.billing_events(session, inst.id)
    seconds = running_seconds_in_window(billing_view(events), start, now)
    billed = (
        await session.execute(
            select(func.coalesce(func.sum(BillHourly.seconds_used), 0)).where(
                BillHourly.instance_id == inst.id, BillHourly.hour_start >= start
            )
        )
    ).scalar_one()
    unsettled_seconds = max(0, seconds - billed)
    return bill_amount(
        inst.price_hourly, inst.gpu_count, unsettled_seconds, max_seconds=31 * 24 * 3600
    )


async def _patrol_running(sm: async_sessionmaker[AsyncSession], counts: dict[str, int]) -> None:
    """Check the user's non-subscription running instances; the available balance minus unsettled
    consumption decides stop or warning."""
    async with sm() as session:
        by_user = await orchestrator_queries.list_running_instances_by_user(session)
        thresholds = await account_service.get_warn_thresholds(session, list(by_user))
        settled_through = await get_watermark(session, "hourly")

    on_demand = {
        uid: [i for i in insts if i.market != MARKET_SUBSCRIPTION] for uid, insts in by_user.items()
    }

    async def check_user(user_id: int) -> None:
        async with sm() as session:
            await _check_user_burn(
                session,
                user_id,
                on_demand[user_id],
                settled_through=settled_through,
                warn_hours=thresholds.get(user_id),
                counts=counts,
            )

    await for_each(
        [uid for uid, insts in on_demand.items() if insts],
        check_user,
        stage="running",
        ident=lambda uid: {"user_id": uid},
    )


async def _check_user_burn(
    session: AsyncSession,
    user_id: int,
    instances: list["Instance"],
    *,
    settled_through: datetime | None,
    warn_hours: int | None,
    counts: dict[str, int],
) -> None:
    """Check the user's unsettled consumption; in arrears lock the instances by id ascending, then
    the wallet and re-check the balance.

    Stop, tail bill and notification commit together; without a stop, warn by the remaining-hours
    threshold.
    """
    available = await wallet.get_available_balance(session, user_id)
    burn_per_hour = sum(
        (hourly_cost(i.price_hourly, i.gpu_count) for i in instances), Decimal("0.00")
    )
    now = now_utc()
    unsettled = Decimal("0.00")
    for inst in instances:
        unsettled += await _unsettled_burn(session, inst, now, settled_through)
    effective = as_amount(available - unsettled)
    if effective <= 0:
        locked_instances = [
            fresh
            for inst in sorted(instances, key=lambda i: i.id)
            if (fresh := await orchestrator_queries.lock_instance(session, inst.id)) is not None
        ]
        locked = await wallet.lock_wallet(session, user_id)
        effective = as_amount(wallet.available_of(locked) - unsettled)
        if effective <= 0:
            for fresh in locked_instances:
                if fresh.status == sm_def.RUNNING:
                    await orchestrator_transitions.system_stop(
                        session, fresh, reason="arrears_stop"
                    )
                    counts["stopped"] += 1
            await notify_service.send_arrears_notice(
                session,
                user_id,
                action="auto_stop",
                detail=server_copy("billing.arrears.auto_stop.detail"),
            )
            await session.commit()
            return
    if warn_hours is None or burn_per_hour <= 0:
        return
    est_hours = float(effective / burn_per_hour)
    if est_hours < warn_hours:
        await notify_service.send_low_balance_warning(
            session, user_id, est_hours=est_hours, balance=money_label(available)
        )
        counts["warned"] += 1


async def _patrol_frozen_and_arrears_stopped(
    sm: async_sessionmaker[AsyncSession], counts: dict[str, int]
) -> None:
    async with sm() as policy_session:
        policies = await get_runtime_config(policy_session)
    now = now_utc()

    async with sm() as session:
        stopped = await orchestrator_queries.list_instances_by_status(session, sm_def.STOPPED)
        frozen = await orchestrator_queries.list_instances_by_status(session, sm_def.FROZEN)

    async def freeze_if_in_arrears(inst: "Instance") -> None:
        async with sm() as session:
            available = await wallet.get_available_balance(session, inst.user_id)
            if available > 0:
                return
            fresh = await orchestrator_queries.get_instance(session, inst.user_id, inst.uuid)
            if fresh.status != sm_def.STOPPED:
                return
            deadline = now + timedelta(hours=policies.freeze_grace_hours)
            await orchestrator_transitions.freeze_instance(session, fresh, deadline)
            await notify_service.send_arrears_notice(
                session,
                inst.user_id,
                action="freeze",
                detail=server_copy(
                    "billing.arrears.freeze.detail", hours=policies.freeze_grace_hours
                ),
            )
            await session.commit()
            counts["frozen"] += 1

    async def unfreeze_or_reclaim(inst: "Instance") -> None:
        async with sm() as session:
            fresh = await orchestrator_queries.get_instance(session, inst.user_id, inst.uuid)
            if fresh.status != sm_def.FROZEN:
                return
            available = await wallet.get_available_balance(session, inst.user_id)
            if available > 0 and fresh.market != MARKET_SUBSCRIPTION:
                await orchestrator_transitions.unfreeze_instance(session, fresh)
                counts["unfrozen"] += 1
            elif fresh.frozen_deadline is not None and fresh.frozen_deadline <= now:
                await orchestrator_transitions.reclaim_frozen(session, fresh)
                await notify_service.send_arrears_notice(
                    session,
                    inst.user_id,
                    action="reclaim",
                    detail=server_copy("billing.arrears.reclaim.detail"),
                )
                counts["reclaimed"] += 1
            await session.commit()

    ident = lambda inst: {"instance_id": inst.id}  # noqa: E731
    await for_each(
        [i for i in stopped if i.market != MARKET_SUBSCRIPTION],
        freeze_if_in_arrears,
        stage="freeze",
        ident=ident,
    )
    await for_each(frozen, unfreeze_or_reclaim, stage="frozen", ident=ident)


async def _settle_disk_pending(session: AsyncSession, disk: "DataDisk") -> None:
    """Settle the disk's billed days before it enters the arrears grace, no commit."""
    await settle_disk_pending_days(
        session,
        DiskBillingInput(
            id=disk.id,
            user_id=disk.user_id,
            price_gb_month=disk.price_gb_month,
            size_gb=disk.size_gb,
            created_at=disk.created_at,
        ),
    )


async def _patrol_disks(sm: async_sessionmaker[AsyncSession], counts: dict[str, int]) -> None:
    """Data-disk arrears chain: arrears → grace (read-only, disk_grace_days) → frozen
    (disk_frozen_days) → erased;
    payment restores it.
    The patrol set is orchestrator.queries.arrears_chain_disk_user_ids.
    """
    async with sm() as session:
        user_ids = await orchestrator_queries.arrears_chain_disk_user_ids(session)

    async def advance_chain(user_id: int) -> None:
        async with sm() as session:
            available = await wallet.get_available_balance(session, user_id)
            changed = await orchestrator_transitions.arrears_transition_disks(
                session, user_id, available <= 0, settle_pending=_settle_disk_pending
            )
            await session.commit()
            counts["disks"] += changed

    await for_each(user_ids, advance_chain, stage="disks", ident=lambda uid: {"user_id": uid})
