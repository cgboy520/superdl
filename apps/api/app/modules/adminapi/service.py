from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.logging import get_logger
from app.core.ratelimit import check_rate_limit
from app.core.security import create_token, hash_password, verify_password
from app.modules.adminapi.models import AdminUser

logger = get_logger(__name__)

# 不存在的用户名也走一次哈希校验,拉平时间侧信道(防用户名枚举)
_DUMMY_HASH = hash_password("dummy-timing-equalizer")

LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300.0


async def login(
    session: AsyncSession, username: str, password: str, *, client_ip: str | None = None
) -> tuple[str, AdminUser]:
    check_rate_limit(
        f"admin-login:{client_ip or '-'}:{username}",
        max_attempts=LOGIN_MAX_ATTEMPTS,
        window_seconds=LOGIN_WINDOW_SECONDS,
    )
    admin = (
        await session.execute(select(AdminUser).where(AdminUser.username == username))
    ).scalar_one_or_none()
    password_ok = verify_password(password, admin.password_hash if admin else _DUMMY_HASH)
    if admin is None or not password_ok:
        logger.warning("admin_login_failed", username=username, ip=client_ip)
        raise AppError(ErrorCode.LOGIN_FAILED, "用户名或密码错误")
    if admin.status != "active":
        raise AppError(ErrorCode.USER_FROZEN, "账号已停用", http_status=status.HTTP_403_FORBIDDEN)
    token = create_token(str(admin.id), "admin", token_type="access")
    return token, admin


async def create_admin(session: AsyncSession, username: str, password: str, role: str) -> AdminUser:
    admin = AdminUser(username=username, password_hash=hash_password(password), role=role)
    session.add(admin)
    await session.commit()
    await session.refresh(admin)
    return admin


async def ensure_bootstrap_admin(session: AsyncSession, password: str) -> None:
    """dev/test 启动引导:无任何管理员时创建 admin 账号。"""
    existing = (await session.execute(select(AdminUser).limit(1))).scalar_one_or_none()
    if existing is None:
        await create_admin(session, "admin", password, "admin")
        logger.info("bootstrap_admin_created", username="admin")


async def create_adjustment(
    session: AsyncSession, *, user_id: int, amount, reason: str, created_by: int
):
    from decimal import Decimal

    from app.core.errors import AppError, ErrorCode
    from app.core.money import as_amount
    from app.modules.adminapi.models import AdminAdjustment

    amount = as_amount(Decimal(str(amount)))
    if amount == 0:
        raise AppError(ErrorCode.VALIDATION_ERROR, "调账金额不能为 0")
    adj = AdminAdjustment(user_id=user_id, amount=amount, reason=reason, created_by=created_by)
    session.add(adj)
    await session.commit()
    await session.refresh(adj)
    return adj


async def review_adjustment(
    session: AsyncSession,
    adjustment_id: int,
    *,
    approve: bool,
    reviewer_id: int,
    comment: str | None,
):
    """双人复核:复核人不得是发起人;通过即生效(钱包 + 流水,同事务)。"""
    from app.core.errors import AppError, ErrorCode, not_found
    from app.core.timeutil import now_utc
    from app.modules.adminapi.models import AdminAdjustment
    from app.modules.billing import service as billing_service

    adj = await session.get(AdminAdjustment, adjustment_id)
    if adj is None:
        raise not_found("调账单不存在")
    if adj.status != "pending":
        raise AppError(ErrorCode.CONFLICT, "调账单已处理", http_status=409)
    if adj.created_by == reviewer_id:
        raise AppError(
            ErrorCode.ADMIN_SECOND_REVIEW_REQUIRED, "调账必须由第二位管理员复核", http_status=403
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
        )
    await session.commit()
    return adj


async def list_adjustments(session: AsyncSession):
    from app.modules.adminapi.models import AdminAdjustment

    return list(
        (
            await session.execute(
                select(AdminAdjustment).order_by(AdminAdjustment.id.desc()).limit(200)
            )
        ).scalars()
    )
