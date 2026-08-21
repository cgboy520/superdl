from typing import Any

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode, not_found
from app.core.logging import get_logger
from app.core.ratelimit import check_rate_limit
from app.core.security import (
    create_token,
    hash_password,
    hash_password_sync,
    verify_password,
)
from app.modules.adminapi.models import AdminUser

logger = get_logger(__name__)

# 不存在的用户名也走一次哈希校验,拉平时间侧信道
_DUMMY_HASH = hash_password_sync("dummy-timing-equalizer")

LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300.0


async def login(
    session: AsyncSession, username: str, password: str, *, client_ip: str | None = None
) -> tuple[str, AdminUser]:
    await check_rate_limit(
        f"admin-login:{client_ip or '-'}:{username}",
        max_attempts=LOGIN_MAX_ATTEMPTS,
        window_seconds=LOGIN_WINDOW_SECONDS,
    )
    admin = (
        await session.execute(select(AdminUser).where(AdminUser.username == username))
    ).scalar_one_or_none()
    password_ok = await verify_password(password, admin.password_hash if admin else _DUMMY_HASH)
    if admin is None or not password_ok:
        logger.warning("admin_login_failed", username=username, ip=client_ip)
        raise AppError(ErrorCode.LOGIN_FAILED, key="adminapi.loginFailed")
    if admin.status != "active":
        raise AppError(
            ErrorCode.USER_FROZEN,
            key="adminapi.userDisabled",
            http_status=status.HTTP_403_FORBIDDEN,
        )
    token = create_token(
        str(admin.id), "admin", token_type="access", extra={"ver": admin.token_version}
    )
    return token, admin


async def create_admin(session: AsyncSession, username: str, password: str, role: str) -> AdminUser:
    from sqlalchemy.exc import IntegrityError

    admin = AdminUser(username=username, password_hash=await hash_password(password), role=role)
    session.add(admin)
    try:
        await session.commit()
    except IntegrityError as exc:  # username 唯一
        await session.rollback()
        raise AppError(
            ErrorCode.CONFLICT,
            key="adminapi.adminUsernameTaken",
            http_status=status.HTTP_409_CONFLICT,
        ) from exc
    await session.refresh(admin)
    return admin


async def ensure_bootstrap_admin(session: AsyncSession, password: str) -> None:
    """启动引导:**表为空时**创建首个 admin 账号,之后自动失效。

    此前这条路径被 environment == "dev" 硬门挡着,而全站 51 个管理端点里没有任何一个能
    管理 admin_users:生产库一启动就是空表,管理控制台开箱不可登录,唯一办法是人工连库
    INSERT 一行 bcrypt hash。更糟的是调账强制双人复核(复核人不得是发起人)——
    生产只能有一个共用账号 → 任何调账单都永远无法通过复核,财务补偿功能在生产上是死的;
    审计的 actor_id 也全部指向同一个账号,追溯不到人。
    """
    existing = (await session.execute(select(AdminUser).limit(1))).scalar_one_or_none()
    if existing is not None:
        return
    await create_admin(session, "admin", password, "admin")
    logger.warning(
        "bootstrap_admin_created",
        username="admin",
        hint="首个管理员已创建;请立即登录改密并建出第二个 admin(调账双人复核需要两个人)",
    )


async def list_admins(session: AsyncSession) -> list[AdminUser]:
    return list((await session.execute(select(AdminUser).order_by(AdminUser.id))).scalars().all())


async def _get_admin(session: AsyncSession, admin_id: int) -> AdminUser:
    admin = await session.get(AdminUser, admin_id, with_for_update=True)
    if admin is None:
        raise not_found("管理员不存在")
    return admin


async def update_admin(
    session: AsyncSession,
    admin_id: int,
    *,
    role: str | None,
    new_status: str | None,
    actor_id: int,
) -> tuple[AdminUser, dict[str, Any]]:
    """改角色/停用。返回 (账号, 旧值快照) —— 审计只记新值答不出「从什么改成什么」。"""
    admin = await _get_admin(session, admin_id)
    if admin.id == actor_id and (new_status == "disabled" or (role and role != admin.role)):
        # 自己停用自己 / 自己降权是最常见的一键锁死操作。挡住这一条就够了:调用方必然是
        # 一个 active 的 admin(require_roles() 只放行 admin),他动别人时自己仍在,
        # 所以「最后一个超管被拿掉」在这组端点上不可能发生 —— 不需要再加一道计数守卫。
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="adminapi.cannotChangeSelf",
            http_status=status.HTTP_409_CONFLICT,
        )
    before: dict[str, Any] = {}
    if role is not None and role != admin.role:
        before["role"] = admin.role
        admin.role = role
    if new_status is not None and new_status != admin.status:
        before["status"] = admin.status
        admin.status = new_status
    if before:
        # 角色变更、停用都必须立即失效已签发的 token,不能等 2 小时 TTL
        admin.token_version += 1
    await session.commit()
    await session.refresh(admin)
    return admin, before


async def reset_admin_password(session: AsyncSession, admin_id: int, password: str) -> AdminUser:
    admin = await _get_admin(session, admin_id)
    admin.password_hash = await hash_password(password)
    admin.token_version += 1  # 改密即踢掉全部在外会话(含泄露的那个)
    await session.commit()
    await session.refresh(admin)
    return admin


async def change_own_password(
    session: AsyncSession, admin_id: int, current_password: str, new_password: str
) -> None:
    admin = await _get_admin(session, admin_id)
    if not await verify_password(current_password, admin.password_hash):
        raise AppError(ErrorCode.LOGIN_FAILED, key="adminapi.loginFailed")
    admin.password_hash = await hash_password(new_password)
    admin.token_version += 1
    await session.commit()


async def create_adjustment(
    session: AsyncSession, *, user_id: int, amount, reason: str, created_by: int
):
    from decimal import Decimal

    from app.core.errors import AppError, ErrorCode
    from app.core.money import as_amount
    from app.modules.adminapi.models import AdminAdjustment

    amount = as_amount(Decimal(str(amount)))
    if amount == 0:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="adminapi.adjustNotZero")
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

    # 行锁:并发复核时后到者等锁,看到非 pending 即 409
    adj = await session.get(AdminAdjustment, adjustment_id, with_for_update=True)
    if adj is None:
        raise not_found("调账单不存在")
    if adj.status != "pending":
        raise AppError(ErrorCode.CONFLICT, key="adminapi.adjustAlreadyProcessed", http_status=409)
    if adj.created_by == reviewer_id:
        raise AppError(
            ErrorCode.ADMIN_SECOND_REVIEW_REQUIRED,
            key="adminapi.adjustSecondReviewer",
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
            # 纠正一笔错误入账不能被当前余额卡住(否则冲正金额被当前余额封顶)
            allow_negative=True,
        )
    await session.commit()
    return adj


async def list_adjustments(session: AsyncSession):
    from app.modules.adminapi.models import AdminAdjustment

    return list(
        (
            await session.execute(
                # 固定截断,与 admin 的 LIST_CAPS.adjustments 对齐
                select(AdminAdjustment).order_by(AdminAdjustment.id.desc()).limit(200)
            )
        ).scalars()
    )
