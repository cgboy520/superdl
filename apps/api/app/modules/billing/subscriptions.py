"""Subscription prepayment, renewal and expiry patrol; releasing mid-period gives no refund, a
failed first scheduling can refund a never-started subscription."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError, ErrorCode, conflict
from app.core.idempotency import (
    IDEMPOTENCY_WINDOW,
    find_replay,
    insert_idempotent,
    request_fingerprint,
)
from app.core.locks import LockKey, advisory_lock
from app.core.logging import get_logger
from app.core.metrics import SUBSCRIPTION_UNPAID_RUNNING
from app.core.money import money_label
from app.core.patrol import for_each
from app.core.platform_config import get_runtime_config
from app.core.pricing import (
    MARKET_SUBSCRIPTION,
    SubscriptionQuote,
    period_delta,
    quote_subscription,
)
from app.core.servercopy import copy as server_copy
from app.core.timeutil import ensure_utc, now_utc
from app.modules.billing import wallet
from app.modules.billing.models import Subscription
from app.modules.notify import service as notify_service
from app.modules.orchestrator import (
    queries as orchestrator_queries,
    statemachine as sm_def,
    transitions as orchestrator_transitions,
)

if TYPE_CHECKING:
    from app.modules.orchestrator.models import Instance

logger = get_logger(__name__)

STATUS_ACTIVE = "active"
STATUS_EXPIRED = "expired"
STATUS_CANCELLED = "cancelled"

REASON_EXPIRED_STOP = "subscription_expired"
REASON_EXPIRED_FREEZE = "subscription_freeze"

_PERIODS = ("day", "week", "month", "year")

_ACT_NEW = "subscription:new"
_ACT_RENEW = "subscription:renew"


def period_label(period: str) -> str:
    return server_copy(f"billing.period.{period}") if period in _PERIODS else period


def _fingerprint(action: str, user_id: int, instance_id: int, period: str, count: int) -> str:
    """Request fingerprint of a subscription order: action + owner + target instance + period."""
    return request_fingerprint(action, user_id, instance_id, period, count)


async def quote(
    session: AsyncSession,
    *,
    base_hourly: Decimal,
    gpu_count: int,
    period: str,
    period_count: int,
) -> SubscriptionQuote:
    """Compute the subscription quote from the current runtime policy, nothing stored."""
    policies = await get_runtime_config(session)
    return quote_subscription(
        base_hourly,
        gpu_count=gpu_count,
        period=period,
        period_count=period_count,
        policies=policies,
    )


async def charge_new(
    session: AsyncSession,
    *,
    user_id: int,
    instance_id: int,
    instance_name: str,
    sku_id: int,
    base_hourly: Decimal,
    gpu_count: int,
    period: str,
    period_count: int,
    idempotency_key: str | None,
) -> tuple[Subscription, SubscriptionQuote]:
    """Order prepayment: write the subscriptions row + debit + ledger. No commit, the caller merges
    it into the instance creation transaction.
    The debit uses allow_negative=False (pay first); insufficient balance raises
    INSUFFICIENT_BALANCE.
    """
    quoted = await quote(
        session,
        base_hourly=base_hourly,
        gpu_count=gpu_count,
        period=period,
        period_count=period_count,
    )
    started = now_utc()
    row = Subscription(
        user_id=user_id,
        instance_id=instance_id,
        sku_id=sku_id,
        period=period,
        period_count=period_count,
        unit_price=quoted.base_hourly,
        amount_paid=quoted.amount,
        started_at=started,
        expires_at=started + period_delta(period, period_count),
        status=STATUS_ACTIVE,
        idempotency_key=idempotency_key,
        request_fingerprint=_fingerprint(_ACT_NEW, user_id, instance_id, period, period_count),
    )
    session.add(row)
    await session.flush()
    await wallet.debit(
        session,
        user_id,
        quoted.amount,
        type_="consume",
        ref_type="subscription",
        ref_id=str(row.id),
        remark=server_copy(
            "billing.remark.subscription",
            instance=instance_name,
            period=period_label(period),
            count=period_count,
        ),
        allow_negative=False,
    )
    return row, quoted


async def list_expiring_active(
    session: AsyncSession, user_id: int, *, within_days: int
) -> list[Subscription]:
    """Expiring active subscriptions: expires_at ≤ now+within_days, by expiry ascending, cap 50."""
    horizon = now_utc() + timedelta(days=within_days)
    return list(
        (
            await session.execute(
                select(Subscription)
                .where(
                    Subscription.user_id == user_id,
                    Subscription.status == STATUS_ACTIVE,
                    Subscription.expires_at <= horizon,
                )
                .order_by(Subscription.expires_at)
                .limit(50)
            )
        ).scalars()
    )


async def find_replay_row(
    session: AsyncSession,
    *,
    user_id: int,
    key: str,
    instance_id: int | None = None,
    period: str | None = None,
    period_count: int | None = None,
) -> Subscription | None:
    """The subscription row for the same (user_id, key) within the idempotency window.

    With instance_id/period/period_count all given, the fingerprint is checked against the new
    subscription action; otherwise lookup by key only.
    """
    if instance_id is not None and period is not None and period_count is not None:
        return await find_replay(
            session,
            Subscription,
            owner_col=Subscription.user_id,
            owner_id=user_id,
            key=key,
            window=IDEMPOTENCY_WINDOW,
            fingerprint=_fingerprint(_ACT_NEW, user_id, instance_id, period, period_count),
        )
    return await find_replay(
        session,
        Subscription,
        owner_col=Subscription.user_id,
        owner_id=user_id,
        key=key,
        window=IDEMPOTENCY_WINDOW,
    )


async def quote_of_row(
    session: AsyncSession, row: Subscription, gpu_count: int
) -> SubscriptionQuote:
    """Re-quote from the subscription's stored list price and period under the current policy."""
    return await quote(
        session,
        base_hourly=row.unit_price,
        gpu_count=gpu_count,
        period=row.period,
        period_count=row.period_count,
    )


async def convert(
    session: AsyncSession,
    *,
    instance: "Instance",
    period: str,
    period_count: int,
    idempotency_key: str | None,
) -> tuple[Subscription, SubscriptionQuote, bool]:
    """Open the subscription from now and prepay, quoted from instance.price_hourly; no commit.

    The caller must settle the pre-conversion on-demand fees first; refused while the latest
    subscription is active.
    """
    current = await current_for_instance(session, instance.id)
    if current is not None and current.status == STATUS_ACTIVE:
        raise AppError(
            ErrorCode.SUBSCRIPTION_NOT_RENEWABLE, key="billing.subscriptionAlreadyActive"
        )
    row, quoted = await charge_new(
        session,
        user_id=instance.user_id,
        instance_id=instance.id,
        instance_name=instance.name,
        sku_id=instance.sku_id,
        base_hourly=instance.price_hourly,
        gpu_count=instance.gpu_count,
        period=period,
        period_count=period_count,
        idempotency_key=idempotency_key,
    )
    logger.info(
        "subscription_converted",
        subscription_id=row.id,
        instance_id=instance.id,
        period=period,
        count=period_count,
        amount=str(quoted.amount),
    )
    return row, quoted, True


async def renew(
    session: AsyncSession,
    *,
    instance: "Instance",
    period: str,
    period_count: int,
    idempotency_key: str | None,
    actor: str = "user",
) -> tuple[Subscription, SubscriptionQuote, bool]:
    """Renewal: the old row becomes expired, a new row is opened and linked by renewed_from_id. No
    commit.
    Returns (new subscription, quote, created); created=False = idempotent replay.

    The new period starts at max(old expiry, now). The pricing baseline is
    `subscriptions.unit_price` (SKU list-price snapshot).
    The caller must hold the wallet row lock (lock_wallet) first; the old row is re-read FOR UPDATE
    inside; lock order wallet → subscriptions.
    """
    fingerprint = _fingerprint(_ACT_RENEW, instance.user_id, instance.id, period, period_count)
    if idempotency_key:
        existing = await find_replay(
            session,
            Subscription,
            owner_col=Subscription.user_id,
            owner_id=instance.user_id,
            key=idempotency_key,
            window=IDEMPOTENCY_WINDOW,
            fingerprint=fingerprint,
        )
        if existing is not None:
            return existing, await quote_of_row(session, existing, instance.gpu_count), False

    current = await current_for_instance(session, instance.id, for_update=True)
    if current is None:
        raise AppError(ErrorCode.SUBSCRIPTION_NOT_RENEWABLE, key="billing.subscriptionMissing")
    if current.status == STATUS_CANCELLED:
        raise AppError(ErrorCode.SUBSCRIPTION_NOT_RENEWABLE, key="billing.subscriptionCancelled")
    quoted = await quote(
        session,
        base_hourly=current.unit_price,
        gpu_count=instance.gpu_count,
        period=period,
        period_count=period_count,
    )
    row = _next_period_row(
        current,
        quoted,
        period=period,
        period_count=period_count,
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
    )
    current.status = STATUS_EXPIRED
    winner = await _insert_renewal(
        session, row, idempotency_key=idempotency_key, fingerprint=fingerprint
    )
    if winner is not row:
        return winner, await quote_of_row(session, winner, instance.gpu_count), False
    await wallet.debit(
        session,
        instance.user_id,
        quoted.amount,
        type_="consume",
        ref_type="subscription",
        ref_id=str(row.id),
        remark=server_copy(
            "billing.remark.renewal",
            instance=instance.name,
            period=period_label(period),
            count=period_count,
        ),
        allow_negative=False,
    )
    logger.info(
        "subscription_renewed",
        subscription_id=row.id,
        instance_id=instance.id,
        period=period,
        count=period_count,
        amount=str(quoted.amount),
        actor=actor,
    )
    return row, quoted, True


def _next_period_row(
    current: Subscription,
    quoted: SubscriptionQuote,
    *,
    period: str,
    period_count: int,
    idempotency_key: str | None,
    fingerprint: str,
) -> Subscription:
    """Renewal row: carries over the old row's SKU / list price / auto-renew switch, starts at
    max(old expiry, now),
    linked by renewed_from_id."""
    started = max(ensure_utc(current.expires_at), now_utc())
    return Subscription(
        user_id=current.user_id,
        instance_id=current.instance_id,
        sku_id=current.sku_id,
        period=period,
        period_count=period_count,
        unit_price=current.unit_price,
        amount_paid=quoted.amount,
        started_at=started,
        expires_at=started + period_delta(period, period_count),
        status=STATUS_ACTIVE,
        auto_renew=current.auto_renew,
        renewed_from_id=current.id,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
    )


async def _insert_renewal(
    session: AsyncSession, row: Subscription, *, idempotency_key: str | None, fingerprint: str
) -> Subscription:
    """Insert the renewal row: with an idempotency key, concurrent same-key inserts return the
    winner; without a key a collision on the partial unique index
    (at most one active per instance) becomes 409."""
    if idempotency_key:
        return await insert_idempotent(
            session,
            row,
            model=Subscription,
            owner_col=Subscription.user_id,
            owner_id=row.user_id,
            key=idempotency_key,
            fingerprint=fingerprint,
        )
    try:
        await insert_idempotent(
            session, row, model=Subscription, owner_col=None, owner_id=None, key=None
        )
    except IntegrityError:
        raise conflict(key="common.retryableConflict") from None
    return row


async def current_for_instance(
    session: AsyncSession, instance_id: int, *, for_update: bool = False
) -> Subscription | None:
    """The instance's currently effective (or last) subscription row: the largest id.
    for_update=True is for the renewal path; the caller must hold the wallet row lock first (lock
    order wallet → subscriptions).
    """
    stmt = (
        select(Subscription)
        .where(Subscription.instance_id == instance_id)
        .order_by(Subscription.id.desc())
        .limit(1)
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def latest_by_instance(
    session: AsyncSession, instance_ids: list[int]
) -> dict[int, Subscription]:
    """Batch variant of current_for_instance."""
    if not instance_ids:
        return {}
    rows = (
        (
            await session.execute(
                select(Subscription)
                .where(Subscription.instance_id.in_(instance_ids))
                .order_by(Subscription.id)
            )
        )
        .scalars()
        .all()
    )
    return {row.instance_id: row for row in rows}


async def reserved_instance_ids(
    session: AsyncSession, instance_ids: list[int] | None = None
) -> set[int]:
    """Instance ids with an active, unexpired subscription; instance_ids limits to the given set."""
    if instance_ids is not None and not instance_ids:
        return set()
    stmt = select(Subscription.instance_id).where(
        Subscription.status == STATUS_ACTIVE, Subscription.expires_at > now_utc()
    )
    if instance_ids is not None:
        stmt = stmt.where(Subscription.instance_id.in_(instance_ids))
    return set((await session.execute(stmt)).scalars().all())


async def expired_instance_ids(session: AsyncSession) -> set[int]:
    """Instance ids with an expired subscription and no coverage."""
    expired = set(
        (
            await session.execute(
                select(Subscription.instance_id).where(
                    Subscription.status == STATUS_EXPIRED,
                    Subscription.expires_at <= now_utc(),
                )
            )
        )
        .scalars()
        .all()
    )
    return expired - await reserved_instance_ids(session)


async def assert_active(session: AsyncSession, instance_id: int) -> Subscription:
    """Start gate of subscription instances: start only within the period. A missing row counts as
    expired (fail-closed)."""
    row = await current_for_instance(session, instance_id)
    if row is None or row.status != STATUS_ACTIVE or ensure_utc(row.expires_at) <= now_utc():
        raise AppError(
            ErrorCode.SUBSCRIPTION_EXPIRED,
            key="billing.subscriptionExpired",
            http_status=409,
        )
    return row


async def set_auto_renew(
    session: AsyncSession, *, user_id: int, instance_id: int, enabled: bool
) -> Subscription:
    """Toggle auto-renewal on the caller's latest non-cancelled subscription; no commit."""
    row = await current_for_instance(session, instance_id)
    if row is None or row.user_id != user_id:
        raise AppError(ErrorCode.SUBSCRIPTION_NOT_RENEWABLE, key="billing.subscriptionMissing")
    if row.status == STATUS_CANCELLED:
        raise AppError(ErrorCode.SUBSCRIPTION_NOT_RENEWABLE, key="billing.subscriptionCancelled")
    row.auto_renew = enabled
    return row


async def cancel_for_instance(session: AsyncSession, instance_id: int) -> None:
    """Void the subscription when the instance enters releasing (prepayment not refunded). No
    commit. Active rows only, expired history rows are untouched."""
    for row in (
        (
            await session.execute(
                select(Subscription).where(
                    Subscription.instance_id == instance_id,
                    Subscription.status == STATUS_ACTIVE,
                )
            )
        )
        .scalars()
        .all()
    ):
        row.status = STATUS_CANCELLED


async def refund_unstarted(session: AsyncSession, instance_id: int, user_id: int) -> Decimal | None:
    """Lock and void the active subscription, returning the prepayment in full to the given user's
    wallet; no commit.

    The caller must confirm the instance never ran and the first scheduling failed. Returns the
    refund total, None without an active row.
    """
    rows = (
        (
            await session.execute(
                select(Subscription)
                .where(
                    Subscription.instance_id == instance_id,
                    Subscription.status == STATUS_ACTIVE,
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    total = Decimal("0.00")
    for row in rows:
        row.status = STATUS_CANCELLED
        if row.amount_paid > 0:
            await wallet.credit(
                session,
                user_id,
                row.amount_paid,
                type_="refund",
                ref_type="subscription",
                ref_id=str(row.id),
                remark=server_copy("billing.remark.unstarted_refund"),
            )
            total += row.amount_paid
    return total


async def subscription_patrol(sm: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """Under the advisory lock run expiry warnings, auto-renewals, expiry stops and freezes,
    returning action counts."""
    counts = {"warned": 0, "renewed": 0, "renew_failed": 0, "stopped": 0, "frozen": 0}
    async with advisory_lock(sm, LockKey.SUBSCRIPTION_PATROL) as got:
        if not got:
            return counts
        await _patrol_due(sm, counts)
        await _patrol_expired_sweep(sm, counts)
        await _refresh_unpaid_running_gauge(sm)
    logger.info("subscription_patrol_done", **counts)
    return counts


async def _patrol_due(sm: async_sessionmaker[AsyncSession], counts: dict[str, int]) -> None:
    """Expiry warnings + expiry handling. One independent transaction per row."""
    async with sm() as session:
        policies = await get_runtime_config(session)
        horizon = now_utc() + timedelta(days=policies.period_expire_warn_days)
        due_ids = list(
            (
                await session.execute(
                    select(Subscription.id).where(
                        Subscription.status == STATUS_ACTIVE,
                        Subscription.expires_at <= horizon,
                    )
                )
            )
            .scalars()
            .all()
        )

    async def handle(subscription_id: int) -> None:
        async with sm() as session:
            await _handle_due(session, subscription_id, counts)
            await session.commit()

    await for_each(
        due_ids, handle, stage="subscription_due", ident=lambda sid: {"subscription_id": sid}
    )


async def _handle_due(session: AsyncSession, subscription_id: int, counts: dict[str, int]) -> None:
    """Handle an expiring subscription; renewal on expiry locks instance → wallet → subscription.

    Re-read the latest coverage under those locks even when auto-renewal is disabled. After an
    auto-renewal failure commits its notice, reacquire all locks and revalidate coverage before
    entering the stop transaction; manual renewal may have completed in between.
    """
    row = (
        await session.execute(select(Subscription).where(Subscription.id == subscription_id))
    ).scalar_one_or_none()
    if row is None or row.status != STATUS_ACTIVE:
        return
    expires = ensure_utc(row.expires_at)
    now = now_utc()
    if expires > now:
        if await _warn_expiring(session, row, expires, now):
            counts["warned"] += 1
        return

    instance_id = row.instance_id
    due = await _lock_due_subscription(session, instance_id, subscription_id)
    if due is None:
        return
    instance, row = due
    if row.auto_renew:
        if await _try_auto_renew(session, row, instance, counts):
            return
        await session.commit()
        due = await _lock_due_subscription(session, instance_id, subscription_id)
        if due is None:
            return
        instance, row = due
    row.status = STATUS_EXPIRED
    await _expire_instance(session, instance, counts)


async def _lock_due_subscription(
    session: AsyncSession, instance_id: int, subscription_id: int
) -> tuple["Instance", Subscription] | None:
    """Lock instance → wallet → latest subscription, returning only the still-due candidate.

    The candidate row in the identity map is not authoritative: renewal expires it and inserts
    a successor. Never expire that successor on behalf of an older patrol candidate.
    """
    instance = await orchestrator_queries.lock_instance(session, instance_id)
    if instance is None or instance.market != MARKET_SUBSCRIPTION:
        return None
    await wallet.lock_wallet(session, instance.user_id)
    current = await current_for_instance(session, instance_id, for_update=True)
    if (
        current is None
        or current.id != subscription_id
        or current.status != STATUS_ACTIVE
        or ensure_utc(current.expires_at) > now_utc()
    ):
        return None
    return instance, current


async def _warn_expiring(
    session: AsyncSession, row: Subscription, expires: datetime, now: datetime
) -> bool:
    """Expiry warning. warned_for_expiry stores "the expiry instant already warned about"."""
    if row.warned_for_expiry is not None and ensure_utc(row.warned_for_expiry) == expires:
        return False
    row.warned_for_expiry = expires
    days = max(0, round((expires - now).total_seconds() / 86400))
    instance = await orchestrator_queries.instance_by_id(session, row.instance_id)
    await notify_service.send_subscription_notice(
        session,
        row.user_id,
        action="expiring",
        detail=server_copy(
            "billing.subscription.expiring.detail",
            period=period_label(row.period),
            expires=f"{expires:%Y-%m-%d %H:%M}",
            days=days,
        ),
        dedup_suffix=str(row.id),
        target_id=instance.uuid,
    )
    return True


async def _try_auto_renew(
    session: AsyncSession, row: Subscription, instance: "Instance", counts: dict[str, int]
) -> bool:
    """Lock the wallet and refresh the subscription before renewing; returns True when the
    subscription is no longer active, notifies and returns False on insufficient balance.

    No overdraft, no commit; the caller holds the instance lock.
    """
    await wallet.lock_wallet(session, row.user_id)
    await session.refresh(row)
    if row.status != STATUS_ACTIVE:
        return True
    quoted = await quote(
        session,
        base_hourly=row.unit_price,
        gpu_count=instance.gpu_count,
        period=row.period,
        period_count=row.period_count,
    )
    if await wallet.get_available_balance(session, row.user_id) < quoted.amount:
        counts["renew_failed"] += 1
        await notify_service.send_subscription_notice(
            session,
            row.user_id,
            action="renew_failed",
            detail=server_copy("billing.subscription.renew_failed.detail"),
            dedup_suffix=str(row.id),
            target_id=instance.uuid,
        )
        return False
    await renew(
        session,
        instance=instance,
        period=row.period,
        period_count=row.period_count,
        idempotency_key=None,
        actor="system",
    )
    counts["renewed"] += 1
    await notify_service.send_subscription_notice(
        session,
        row.user_id,
        action="renewed",
        detail=server_copy(
            "billing.subscription.renewed.detail",
            period=period_label(row.period),
            count=row.period_count,
            amount=money_label(quoted.amount),
        ),
        dedup_suffix=str(row.id),
        target_id=instance.uuid,
    )
    return True


async def _expire_instance(
    session: AsyncSession, instance: "Instance", counts: dict[str, int]
) -> None:
    """Expiry handling: running → stop; stopped → freeze directly; other statuses are left alone,
    _patrol_expired_sweep re-scans "expired and not covered" every round until they land in those
    two states."""
    if instance.status == sm_def.RUNNING:
        await orchestrator_transitions.system_stop(session, instance, reason=REASON_EXPIRED_STOP)
        counts["stopped"] += 1
    elif instance.status == sm_def.STOPPED:
        await _freeze(session, instance)
        counts["frozen"] += 1
    else:
        return
    await notify_service.send_subscription_notice(
        session,
        instance.user_id,
        action="expired",
        detail=server_copy("billing.subscription.expired.detail"),
        dedup_suffix=str(instance.id),
        target_id=instance.uuid,
    )


async def _freeze(session: AsyncSession, instance: "Instance") -> None:
    """Set the frozen deadline from freeze_grace_hours, no commit."""
    policies = await get_runtime_config(session)
    await orchestrator_transitions.freeze_instance(
        session,
        instance,
        now_utc() + timedelta(hours=policies.freeze_grace_hours),
        reason=REASON_EXPIRED_FREEZE,
    )


_SWEEP_STATUSES = (sm_def.RUNNING, sm_def.STOPPED)


async def _patrol_expired_sweep(
    sm: async_sessionmaker[AsyncSession], counts: dict[str, int]
) -> None:
    """Handle "subscription expired and not covered" running / stopped instances in independent
    transactions:
    running → stop, stopped → freeze. Instances whose expiry fell into creating/starting/stopping
    are picked up by this pass."""
    async with sm() as session:
        expired = await expired_instance_ids(session)
        if not expired:
            return
        candidates = [
            inst
            for status in _SWEEP_STATUSES
            for inst in await orchestrator_queries.list_instances_by_status(session, status)
            if inst.id in expired
        ]

    async def expire_if_still_due(inst: "Instance") -> None:
        async with sm() as session:
            fresh = await orchestrator_queries.lock_instance(session, inst.id)
            if fresh is None or fresh.status not in _SWEEP_STATUSES:
                return
            if fresh.id in await reserved_instance_ids(session, [fresh.id]):
                return
            await _expire_instance(session, fresh, counts)
            await session.commit()

    await for_each(
        candidates,
        expire_if_still_due,
        stage="subscription_sweep",
        ident=lambda inst: {"instance_id": inst.id},
    )


_UNPAID_GAUGE_STATUSES = (sm_def.CREATING, sm_def.STARTING, sm_def.RUNNING)


async def _refresh_unpaid_running_gauge(sm: async_sessionmaker[AsyncSession]) -> None:
    """Refresh the "subscription instance active without coverage" count; > 0 means the expiry chain
    let one through."""
    async with sm() as session:
        active = [
            inst
            for status in _UNPAID_GAUGE_STATUSES
            for inst in await orchestrator_queries.list_instances_by_status(session, status)
            if inst.market == MARKET_SUBSCRIPTION
        ]
        reserved = await reserved_instance_ids(session, [inst.id for inst in active])
    unpaid = sum(1 for inst in active if inst.id not in reserved)
    SUBSCRIPTION_UNPAID_RUNNING.set(unpaid)
    if unpaid:
        logger.error("subscription_unpaid_running", count=unpaid)
