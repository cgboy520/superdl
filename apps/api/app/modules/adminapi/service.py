from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.logging import get_logger
from app.core.security import create_token, hash_password, verify_password
from app.modules.adminapi.models import AdminUser

logger = get_logger(__name__)


async def login(session: AsyncSession, username: str, password: str) -> tuple[str, AdminUser]:
    admin = (
        await session.execute(select(AdminUser).where(AdminUser.username == username))
    ).scalar_one_or_none()
    if admin is None or not verify_password(password, admin.password_hash):
        raise AppError(ErrorCode.LOGIN_FAILED, "用户名或密码错误")
    if admin.status != "active":
        raise AppError(ErrorCode.USER_FROZEN, "账号已停用")
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
