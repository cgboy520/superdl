"""按保留期清理验证码、refresh 记录、done/discarded outbox、审计和限流计数。"""

from typing import Any, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def cleanup_expired_rows(sm: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """同事务清理并提交,返回各类删除行数;审计按 audit_retention_days 保留,
    经 SECURITY DEFINER 函数 audit_log_prune 删(应用角色对 audit_log 无 DELETE 权限)。"""
    retention = get_settings().audit_retention_days
    stmts = {
        "sms_codes": "DELETE FROM sms_codes WHERE expires_at < now() - interval '7 days'",
        "used_refresh_tokens": "DELETE FROM used_refresh_tokens WHERE expires_at < now()",
        "outbox_done": (
            "DELETE FROM outbox_tasks WHERE status IN ('done', 'discarded') "
            "AND updated_at < now() - interval '7 days'"
        ),
        "rate_limit_counters": (
            "DELETE FROM rate_limit_counters WHERE updated_at < now() - interval '2 days'"
        ),
    }
    counts: dict[str, int] = {}
    async with sm() as session:
        for name, stmt in stmts.items():
            result = cast(CursorResult[Any], await session.execute(text(stmt)))
            counts[name] = result.rowcount or 0
        counts["audit_log"] = int(
            (
                await session.execute(text("SELECT audit_log_prune(:days)"), {"days": retention})
            ).scalar_one()
        )
        await session.commit()
    if any(counts.values()):
        logger.info("cleanup_expired_rows", **counts)
    return counts
