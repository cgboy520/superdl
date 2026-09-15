"""账号注销申请、撤销、管理端执行与驳回。"""

import math
import secrets
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import ADMIN_LIST_CAP
from app.core.errors import AppError, ErrorCode, conflict, not_found
from app.core.logging import get_logger, mask_phone_value
from app.core.money import money_str
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
    session: AsyncSession, user: User, *, phone: str, reason: str
) -> AccountDeletionRequest:
    """校验手机号并提交注销申请;已有 pending 时返回既有申请。"""
    if user.phone != phone:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="account.deletionPhoneMismatch")
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
    """当前 pending;无则最近一条。"""
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
    """锁定并撤销最新的 pending 申请后提交;无申请回 404,终态回 409。"""
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
        phone_masked=mask_phone_value(user.phone),
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
    """注销申请列表(固定截断),行内附执行前校验计数。"""
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
    """返回单条注销申请及当前残留资源数、余额。"""
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
    """执行前校验不过的自动驳回:残留清单/余额写进 note。"""
    req.status = "rejected"
    req.processed_by = admin_id
    req.processed_at = now_utc()
    req.note = note


async def approve_deletion(
    session: AsyncSession, request_id: int, *, admin_id: int, note: str
) -> AccountDeletionRequest:
    """锁定申请与用户后执行注销;调用方负责管理员授权与 note 校验。

    冷静期未满回 409;残留资源或余额非零时提交驳回后回 409。
    通过时同事务改写手机号、清空姓名与脱敏证件号、撤销会话并标记 deleted;
    证件摘要与账单保留。
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
            note=(
                f"自动驳回:名下仍有未释放实例 {len(leftovers['instances'])} 台"
                f"({', '.join(leftovers['instances'])})、未删除数据盘 {len(leftovers['disks'])} 块"
                f"({', '.join(leftovers['disks'])});请先清空资源后重新申请"
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
            note=f"自动驳回:余额 ¥{money_str(balance)} 未提现,请先经退款流程提现,到账后重新申请",
        )
        await session.commit()
        raise conflict(
            key="account.deletionBalanceRemaining",
            params={"balance": money_str(balance)},
            detail={"balance": money_str(balance)},
        )
    user.phone = f"del:{user.id}:{secrets.token_hex(8)}"
    user.id_name = None
    user.id_number = None
    user.verification_status = "unverified"
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
    """锁定并驳回 pending 申请后提交;不受冷静期限制,调用方校验 note。"""
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
