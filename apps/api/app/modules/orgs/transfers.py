"""Balance a tenant's admins move: allocate from the caller's own available balance to any member,
reclaim from an account the tenant created back to the caller (up to its available balance).
Both ledger rows and the transfer record share one transaction; moved money is spend-only (never
refunded or invoiced by whoever receives it). Each move notifies the member."""

from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode, conflict, forbidden
from app.core.idempotency import (
    IDEMPOTENCY_WINDOW,
    find_replay,
    insert_idempotent,
    request_fingerprint,
)
from app.core.logging import get_logger
from app.core.money import as_amount, money_label
from app.core.servercopy import copy as server_copy
from app.modules.billing import service as billing_service
from app.modules.notify import service as notify_service
from app.modules.orgs import service
from app.modules.orgs.access import require_org_role
from app.modules.orgs.models import (
    ROLE_MEMBER,
    ROLE_OWNER,
    TRANSFER_ALLOCATE,
    TRANSFER_RECLAIM,
    OrgMember,
    OrgTransfer,
)
from app.modules.orgs.schemas import BalanceTransferCreate, BalanceTransferOut

logger = get_logger(__name__)

LIST_LIMIT = 200


async def _labels(session: AsyncSession, org_id: int, user_ids: set[int]) -> dict[int, str]:
    members = (
        await session.scalars(
            select(OrgMember).where(OrgMember.org_id == org_id, OrgMember.user_id.in_(user_ids))
        )
    ).all()
    outs = await service.member_outs(session, list(members))
    by_user = {m.user_id: out.label for m, out in zip(members, outs, strict=True)}
    return {uid: by_user.get(uid, f"#{uid}") for uid in user_ids}


async def _out(session: AsyncSession, row: OrgTransfer) -> BalanceTransferOut:
    member_uid = row.to_user_id if row.kind == TRANSFER_ALLOCATE else row.from_user_id
    labels = await _labels(session, row.org_id, {member_uid, row.created_by})
    member = await session.scalar(
        select(OrgMember.id).where(OrgMember.org_id == row.org_id, OrgMember.user_id == member_uid)
    )
    return BalanceTransferOut.model_validate(
        {
            "uuid": row.uuid,
            "kind": row.kind,
            "member_id": member,
            "member_label": labels[member_uid],
            "actor_label": labels[row.created_by],
            "amount": row.amount,
            "created_at": row.created_at,
        }
    )


async def transfer(
    session: AsyncSession,
    user_id: int,
    member_id: int,
    body: BalanceTransferCreate,
    idempotency_key: str | None,
) -> tuple[BalanceTransferOut, bool]:
    """(transfer, created); a replay of the same key returns the first transfer."""
    fingerprint = request_fingerprint(user_id, member_id, body.kind, body.amount)
    if idempotency_key:
        existing = await find_replay(
            session,
            OrgTransfer,
            owner_col=OrgTransfer.created_by,
            owner_id=user_id,
            key=idempotency_key,
            window=IDEMPOTENCY_WINDOW,
            fingerprint=fingerprint,
        )
        if existing is not None:
            return await _out(session, existing), False
    org, me = await require_org_role(session, user_id, service.ADMINS, lock=True)
    target = await service.member_in_org(session, org.id, member_id)
    if target.id == me.id:
        raise conflict(key="orgs.transferSelf")
    amount = as_amount(Decimal(body.amount))
    if amount <= 0:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="orgs.transferAmount")
    if body.kind == TRANSFER_RECLAIM:
        if not await service.is_managed_by(session, target):
            raise conflict(key="orgs.reclaimNotManaged")
        if target.role != ROLE_MEMBER and me.role != ROLE_OWNER:
            raise forbidden(key="orgs.roleNotAllowed")
        source, sink = target.user_id, me.user_id
    else:
        source, sink = me.user_id, target.user_id
    row = OrgTransfer(
        uuid=uuid4().hex,
        org_id=org.id,
        kind=body.kind,
        from_user_id=source,
        to_user_id=sink,
        amount=amount,
        created_by=user_id,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
    )
    result = await insert_idempotent(
        session,
        row,
        model=OrgTransfer,
        owner_col=OrgTransfer.created_by,
        owner_id=user_id,
        key=idempotency_key,
        window=IDEMPOTENCY_WINDOW,
        fingerprint=fingerprint,
    )
    if result is not row:
        return await _out(session, result), False
    labels = await _labels(session, org.id, {source, sink})
    await billing_service.transfer_balance(
        session,
        from_user_id=source,
        to_user_id=sink,
        amount=amount,
        ref_id=row.uuid,
        remark_out=server_copy("orgs.transfer_out.remark", org=org.name, peer=labels[sink]),
        remark_in=server_copy("orgs.transfer_in.remark", org=org.name, peer=labels[source]),
    )
    await notify_service.notify(
        session,
        target.user_id,
        type_="org",
        title=server_copy(f"orgs.balance_{body.kind}.title", org=org.name),
        content=server_copy(
            f"orgs.balance_{body.kind}.content", org=org.name, amount=money_label(amount)
        ),
    )
    await session.commit()
    await session.refresh(row)
    logger.info("org_balance_moved", org_uuid=org.uuid, kind=body.kind, transfer_uuid=row.uuid)
    return await _out(session, row), True


async def list_transfers(session: AsyncSession, user_id: int) -> list[BalanceTransferOut]:
    """The tenant's moves, newest first (owners and admins)."""
    org, _ = await require_org_role(session, user_id, service.ADMINS)
    rows = (
        await session.scalars(
            select(OrgTransfer)
            .where(OrgTransfer.org_id == org.id)
            .order_by(OrgTransfer.id.desc())
            .limit(LIST_LIMIT)
        )
    ).all()
    return [await _out(session, r) for r in rows]
