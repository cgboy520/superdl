"""Account deletion requests: apply, cancel, admin execution and rejection."""

import math
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import ADMIN_LIST_CAP
from app.core.errors import AppError, ErrorCode, conflict, not_found
from app.core.handles import mask_handle
from app.core.logging import get_logger
from app.core.money import money_label, money_str
from app.core.servercopy import copy as server_copy
from app.core.timeutil import now_utc
from app.modules.account.models import AccountDeletionRequest, User
from app.modules.account.schemas import AdminDeletionRequestOut
from app.modules.account.service import get_user
from app.modules.billing import service as billing_service
from app.modules.orchestrator import queries as orchestrator_queries

logger = get_logger(__name__)


async def _pending_deletion_of_user(
    session: AsyncSession, user_id: int
) -> AccountDeletionRequest | None:
    return (
        await session.execute(
            select(AccountDeletionRequest).where(
                AccountDeletionRequest.user_id == user_id,
                AccountDeletionRequest.status == "pending",
            )
        )
    ).scalar_one_or_none()


async def request_deletion(
    session: AsyncSession, user: User, *, handle: str, reason: str
) -> AccountDeletionRequest:
    """The retyped handle must be one of the account's own; an existing pending request is
    returned unchanged."""
    if handle not in (user.email, user.phone):
        raise AppError(ErrorCode.VALIDATION_ERROR, key="account.deletionHandleMismatch")
    existing = await _pending_deletion_of_user(session, user.id)
    if existing is not None:
        return existing
    req = AccountDeletionRequest(user_id=user.id, reason=reason)
    session.add(req)
    await session.commit()
    await session.refresh(req)
    logger.info("deletion_requested", user_id=user.id)
    return req


async def get_my_deletion_request(
    session: AsyncSession, user_id: int
) -> AccountDeletionRequest | None:
    """The current pending request; otherwise the most recent one."""
    pending = await _pending_deletion_of_user(session, user_id)
    if pending is not None:
        return pending
    return (
        await session.execute(
            select(AccountDeletionRequest)
            .where(AccountDeletionRequest.user_id == user_id)
            .order_by(AccountDeletionRequest.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def cancel_deletion_request(session: AsyncSession, user_id: int) -> AccountDeletionRequest:
    """Lock and cancel the latest pending request, then commit; no request → 404, terminal → 409."""
    req = (
        await session.execute(
            select(AccountDeletionRequest)
            .where(AccountDeletionRequest.user_id == user_id)
            .order_by(AccountDeletionRequest.id.desc())
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if req is None:
        raise not_found()
    if req.status != "pending":
        raise conflict(key="account.deletionNotCancellable", params={"status": req.status})
    req.status = "cancelled"
    await session.commit()
    logger.info("deletion_cancelled", user_id=user_id)
    return req


def _deletion_out(
    req: AccountDeletionRequest,
    user: User,
    leftover_counts: dict[str, int],
    balance: Decimal,
) -> AdminDeletionRequestOut:
    return AdminDeletionRequestOut(
        id=req.id,
        user_id=req.user_id,
        email_masked=mask_handle(user.email) if user.email else None,
        phone_masked=mask_handle(user.phone) if user.phone else None,
        status=req.status,
        reason=req.reason,
        requested_at=req.requested_at,
        cooldown_ends_at=req.cooldown_ends_at,
        processed_by=req.processed_by,
        processed_at=req.processed_at,
        note=req.note,
        instances_active=leftover_counts["instances"],
        disks_active=leftover_counts["disks"],
        balance=money_str(balance),
    )


async def admin_list_deletion_requests(
    session: AsyncSession, status_: str | None = None
) -> list[AdminDeletionRequestOut]:
    """Deletion request list (fixed cap), each row with the pre-execution check counts."""
    stmt = (
        select(AccountDeletionRequest)
        .order_by(AccountDeletionRequest.id.desc())
        .limit(ADMIN_LIST_CAP)
    )
    if status_:
        stmt = stmt.where(AccountDeletionRequest.status == status_)
    rows = list((await session.execute(stmt)).scalars())
    if not rows:
        return []
    user_ids = [r.user_id for r in rows]
    users = {
        u.id: u
        for u in (await session.execute(select(User).where(User.id.in_(user_ids)))).scalars()
    }
    counts = await orchestrator_queries.deletion_leftover_counts(session, user_ids)
    balances = await billing_service.balances_by_user(session, user_ids)
    return [
        _deletion_out(
            r,
            users[r.user_id],
            counts.get(r.user_id, {"instances": 0, "disks": 0}),
            balances.get(r.user_id, Decimal("0.00")),
        )
        for r in rows
        if r.user_id in users
    ]


async def admin_get_deletion_out(session: AsyncSession, request_id: int) -> AdminDeletionRequestOut:
    """One deletion request with its current leftover resource counts and balance."""
    req = await session.get(AccountDeletionRequest, request_id)
    if req is None:
        raise not_found()
    user = await get_user(session, req.user_id)
    counts = await orchestrator_queries.deletion_leftover_counts(session, [req.user_id])
    balance = await billing_service.get_balance(session, req.user_id)
    return _deletion_out(
        req,
        user,
        counts.get(req.user_id, {"instances": 0, "disks": 0}),
        balance,
    )


async def _get_deletion_for_update(
    session: AsyncSession, request_id: int
) -> AccountDeletionRequest:
    req = await session.get(AccountDeletionRequest, request_id, with_for_update=True)
    if req is None:
        raise not_found()
    return req


def _auto_reject_deletion(req: AccountDeletionRequest, *, admin_id: int, note: str) -> None:
    """Auto-rejection when the pre-execution checks fail: leftovers / balance go into note."""
    req.status = "rejected"
    req.processed_by = admin_id
    req.processed_at = now_utc()
    req.note = note


async def approve_deletion(
    session: AsyncSession, request_id: int, *, admin_id: int, note: str
) -> AccountDeletionRequest:
    """Lock the request and the user, then execute the deletion; the caller handles admin
    authorisation and note validation.

    Cooling-off not over → 409; leftover resources or a non-zero balance commit a rejection and
    return 409.
    On approval, the same transaction clears both handles and the KYC name / masked identity,
    revokes sessions and marks the account deleted; the identity digest and bills are kept.
    """
    req = await _get_deletion_for_update(session, request_id)
    if req.status != "pending":
        raise conflict(key="account.deletionNotPending", params={"status": req.status})
    remaining = req.cooldown_ends_at - now_utc()
    if remaining.total_seconds() > 0:
        raise conflict(
            key="account.deletionCooldown",
            params={"hours": math.ceil(remaining.total_seconds() / 3600)},
        )
    user = await session.get(User, req.user_id, with_for_update=True)
    if user is None:
        raise not_found()
    leftovers = await orchestrator_queries.deletion_leftovers(session, user.id)
    if leftovers["instances"] or leftovers["disks"]:
        _auto_reject_deletion(
            req,
            admin_id=admin_id,
            note=server_copy(
                "account.deletion_reject.leftovers",
                instances=len(leftovers["instances"]),
                instance_list=", ".join(leftovers["instances"]),
                disks=len(leftovers["disks"]),
                disk_list=", ".join(leftovers["disks"]),
            ),
        )
        await session.commit()
        raise conflict(
            key="account.deletionLeftovers",
            params={
                "instances": len(leftovers["instances"]),
                "disks": len(leftovers["disks"]),
            },
            detail=leftovers,
        )
    balance = await billing_service.get_balance(session, user.id)
    if balance != 0:
        _auto_reject_deletion(
            req,
            admin_id=admin_id,
            note=server_copy("account.deletion_reject.balance", balance=money_label(balance)),
        )
        await session.commit()
        raise conflict(
            key="account.deletionBalanceRemaining",
            params={"balance": money_label(balance)},
            detail={"balance": money_str(balance)},
        )
    user.email = None
    user.email_verified_at = None
    user.phone = None
    user.kyc_name = None
    user.kyc_identity_masked = None
    user.kyc_provider = None
    user.kyc_ref = None
    user.kyc_verified_at = None
    user.kyc_status = "unverified"
    user.token_version += 1
    user.status = "deleted"
    req.status = "completed"
    req.processed_by = admin_id
    req.processed_at = now_utc()
    req.note = note
    await session.commit()
    logger.info("account_deleted", user_id=user.id)
    return req


async def reject_deletion(
    session: AsyncSession, request_id: int, *, admin_id: int, note: str
) -> AccountDeletionRequest:
    """Lock and reject a pending request, then commit; not bound by the cooling-off period, the
    caller validates note."""
    req = await _get_deletion_for_update(session, request_id)
    if req.status != "pending":
        raise conflict(key="account.deletionNotPending", params={"status": req.status})
    req.status = "rejected"
    req.processed_by = admin_id
    req.processed_at = now_utc()
    req.note = note
    await session.commit()
    logger.info("deletion_rejected", request_id=request_id)
    return req
