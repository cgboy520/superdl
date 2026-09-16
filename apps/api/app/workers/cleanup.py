"""Retention cleanup of verification codes, refresh records, done/discarded outbox tasks, audit
rows and rate-limit counters."""

from typing import Any, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def cleanup_expired_rows(sm: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """Clean up and commit in one transaction, returning the deleted row counts; audit rows are kept
    for audit_retention_days
    and deleted through the SECURITY DEFINER function audit_log_prune (the application role has no
    DELETE on audit_log)."""
    retention = get_settings().audit_retention_days
    stmts = {
        "verification_codes": (
            "DELETE FROM verification_codes WHERE expires_at < now() - interval '7 days'"
        ),
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
