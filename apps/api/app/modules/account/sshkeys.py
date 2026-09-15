"""SSH 公钥的增删与查询。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode, conflict, not_found
from app.core.logging import get_logger
from app.core.ratelimit import check_rate_limit
from app.modules.account.models import SshKey
from app.modules.account.sshkey_util import parse_public_key
from app.modules.orchestrator import service as orchestrator_service

logger = get_logger(__name__)


async def list_ssh_keys(session: AsyncSession, user_id: int) -> list[SshKey]:
    return list(
        (
            await session.execute(
                select(SshKey).where(SshKey.user_id == user_id).order_by(SshKey.id)
            )
        ).scalars()
    )


MAX_SSH_KEYS_PER_USER = 50


async def add_ssh_key(session: AsyncSession, user_id: int, name: str, public_key: str) -> SshKey:
    await check_rate_limit(f"ssh-key-add:{user_id}", max_attempts=20, window_seconds=3600.0)
    try:
        normalized, fingerprint = parse_public_key(public_key)
    except ValueError as exc:
        raise AppError(ErrorCode.SSH_KEY_INVALID, str(exc)) from exc
    dup = (
        await session.execute(
            select(SshKey).where(SshKey.user_id == user_id, SshKey.fingerprint == fingerprint)
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise AppError(ErrorCode.SSH_KEY_DUPLICATE, key="account.sshKeyDuplicate")
    count = (
        await session.execute(select(func.count()).where(SshKey.user_id == user_id))
    ).scalar_one()
    if count >= MAX_SSH_KEYS_PER_USER:
        raise conflict(key="account.sshKeyLimitReached", params={"max": MAX_SSH_KEYS_PER_USER})
    key = SshKey(user_id=user_id, name=name, public_key=normalized, fingerprint=fingerprint)
    session.add(key)
    await session.commit()
    await session.refresh(key)
    return key


async def delete_ssh_key(session: AsyncSession, user_id: int, key_id: int) -> None:
    """同事务删除公钥并更新未释放实例的密钥快照;运行中实例下次启动生效。"""
    key = await session.get(SshKey, key_id)
    if key is None or key.user_id != user_id:
        raise not_found()
    await session.delete(key)
    stripped = await orchestrator_service.strip_ssh_key_from_instances(
        session, user_id, key.public_key
    )
    await session.commit()
    if stripped:
        logger.info("ssh_key_stripped_from_instances", user_id=user_id, instances=stripped)
