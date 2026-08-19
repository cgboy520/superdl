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
