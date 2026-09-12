"""管理端资金动作:调账(双人复核)、渠道冲正处置、调账列表。"""

from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode, conflict, not_found
from app.core.idempotency import request_fingerprint
from app.core.logging import get_logger
from app.core.metrics import (
    PAYMENT_REVERSAL_RESOLVED_TOTAL,
)
from app.core.money import as_amount, money_str
from app.core.pagination import Page, paginate_by_id
from app.core.timeutil import ensure_utc, now_utc
from app.modules.account import service as account_service
from app.modules.adminapi.models import AdminAdjustment, AdminUser
from app.modules.adminapi.schemas import (
    AdjustmentOut,
)
from app.modules.billing import service as billing_service

logger = get_logger(__name__)

# 单笔调账绝对值上限:超出走线下流程
ADJUST_MAX_ABS = Decimal("100000.00")


async def create_adjustment(
    session: AsyncSession,
    *,
    user_id: int,
    amount,
    reason: str,
    created_by: int,
    idempotency_key: str | None = None,
) -> tuple["AdminAdjustment", bool]:
    """发起调账。返回 (调账单, created):created=False = 幂等重放,路由回 200 + 重放区分头。
    幂等键作用域为 (发起人,租户,键);同键重放比对请求体指纹,不一致 409。"""
    amount = as_amount(Decimal(str(amount)))
    fingerprint = request_fingerprint(user_id, amount, reason)

    if idempotency_key:
        # 幂等归属为 (created_by, user_id) 双列,不可用 find_replay(仅支持单列)
        existing = (
            await session.execute(
                select(AdminAdjustment).where(
                    AdminAdjustment.created_by == created_by,
                    AdminAdjustment.user_id == user_id,
                    AdminAdjustment.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise conflict(key="common.idempotencyKeyMismatch")
            return existing, False  # 幂等重放

    await account_service.get_user(session, user_id)
    if amount == 0:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="adminapi.adjustNotZero")
    if abs(amount) > ADJUST_MAX_ABS:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="common.validation",
            detail={"field": "amount", "max_abs": money_str(ADJUST_MAX_ABS)},
        )
    adj = AdminAdjustment(
        user_id=user_id,
        amount=amount,
        reason=reason,
        created_by=created_by,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
    )
    session.add(adj)
    # 并发同键撞 uq_admin_adjustments_idem_scope 由唯一约束兜底(500),不回查
    await session.commit()
    await session.refresh(adj)
    return adj, True


async def review_adjustment(
    session: AsyncSession,
    adjustment_id: int,
    *,
    approve: bool,
    reviewer_id: int,
    comment: str | None,
    audit_writer: Callable[[AsyncSession], Awaitable[None]] | None = None,
):
    """双人复核:复核人不得是发起人,且须为调账发起前已存在的账号;通过即生效(钱包+流水,同事务)。
    audit_writer 在 approve 分支 commit 前调用,写失败即整体回滚。"""
    # 行锁:后到者看到非 pending 即 409
    adj = await session.get(AdminAdjustment, adjustment_id, with_for_update=True)
    if adj is None:
        raise not_found()
    if adj.status != "pending":
        raise conflict(key="adminapi.adjustAlreadyProcessed")
    if adj.created_by == reviewer_id:
        raise AppError(
            ErrorCode.ADMIN_SECOND_REVIEW_REQUIRED,
            key="adminapi.adjustSecondReviewer",
            http_status=403,
        )
    reviewer = await session.get(AdminUser, reviewer_id)
    if reviewer is None or ensure_utc(reviewer.created_at) >= ensure_utc(adj.created_at):
        # 发起后才创建的账号不构成独立的第二人
        raise AppError(
            ErrorCode.ADMIN_SECOND_REVIEW_REQUIRED,
            key="adminapi.adjustReviewerTooNew",
            http_status=403,
        )
    adj.reviewed_by = reviewer_id
    adj.review_comment = comment
    adj.reviewed_at = now_utc()
    if not approve:
        adj.status = "rejected"
        await session.commit()
        return adj
    adj.status = "approved"
    if adj.amount > 0:
        await billing_service.credit(
            session,
            adj.user_id,
            adj.amount,
            type_="adjust",
            ref_type="adjustment",
            ref_id=str(adj.id),
            remark=f"调账:{adj.reason}",
        )
    else:
        await billing_service.debit(
            session,
            adj.user_id,
            -adj.amount,
            type_="adjust",
            ref_type="adjustment",
            ref_id=str(adj.id),
            remark=f"调账:{adj.reason}",
            allow_negative=True,
        )
    if audit_writer is not None:
        await audit_writer(session)  # 与生效同事务
    await session.commit()
    return adj


async def resolve_reversal(
    session: AsyncSession,
    order_no: str,
    *,
    action: Literal["release", "chargeback"],
    reason: str,
    operator_id: int,
    audit_writer: Callable[[AsyncSession], Awaitable[None]] | None = None,
) -> None:
    """核销渠道冲正(channel_reversed 分桶的唯一出口)。

    - release:解冻等额冻结额,订单恢复退款资格;
    - chargeback:解冻 + 等额扣减(ledger adjust,允许透支)。
    两种都只写 resolved_at + action,不清 channel_reversed_at(同一通知重放不再二次冻结)。
    单操作人 + 同步审计 + 计数(release 条条告警 PaymentReversalReleased)。
    """
    order = (
        await session.execute(
            select(billing_service.Order)
            .where(billing_service.Order.order_no == order_no)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if order is None:
        raise not_found("订单不存在")
    if not billing_service.reversal_pending(order):
        raise conflict(key="adminapi.reversalNotPending")
    await billing_service.release_freeze(session, order.user_id, order.amount)
    order.channel_reversal_resolved_at = now_utc()
    order.channel_reversal_action = action
    if action != "release":
        await billing_service.debit(
            session,
            order.user_id,
            order.amount,
            type_="adjust",
            ref_type="reversal",
            ref_id=order.order_no,
            remark=f"渠道冲正核销:{reason}",
            allow_negative=True,  # 核销后余额为负走欠费链路
        )
    if audit_writer is not None:
        await audit_writer(session)  # 与核销同事务
    await session.commit()
    PAYMENT_REVERSAL_RESOLVED_TOTAL.labels(action=action).inc()
    logger.info(
        "reversal_resolved",
        order_no=order_no,
        action=action,
        operator_id=operator_id,
        amount=str(order.amount),
    )


async def list_adjustments(
    session: AsyncSession,
    *,
    status: str | None = None,
    user_id: int | None = None,
    day_range: tuple[datetime, datetime] | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> Page[AdjustmentOut]:
    """调账单列表(游标分页,降序)。status/user_id 精确;day_range 按 created_at 过滤。"""
    stmt = select(AdminAdjustment).order_by(AdminAdjustment.id.desc())
    if status:
        stmt = stmt.where(AdminAdjustment.status == status)
    if user_id is not None:
        stmt = stmt.where(AdminAdjustment.user_id == user_id)
    if day_range is not None:
        stmt = stmt.where(
            AdminAdjustment.created_at >= day_range[0], AdminAdjustment.created_at < day_range[1]
        )
    page_items, next_cursor = await paginate_by_id(
        session, stmt, id_col=AdminAdjustment.id, cursor=cursor, limit=limit
    )
    return Page[AdjustmentOut](
        items=[
            AdjustmentOut(
                id=r.id,
                user_id=r.user_id,
                amount=money_str(r.amount),
                reason=r.reason,
                status=r.status,
                created_by=r.created_by,
                reviewed_by=r.reviewed_by,
                review_comment=r.review_comment,
                created_at=r.created_at.isoformat(),
            )
            for r in page_items
        ],
        next_cursor=next_cursor,
    )
