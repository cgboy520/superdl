"""数据保洁(每日):过期验证码 / 已用 refresh 记录 / 已完成 outbox / 超保留期审计 / 过期限流计数。"""

from typing import Any, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def cleanup_expired_rows(sm: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """数据保洁(每日):过期验证码/已用 refresh 记录/已完成 outbox/超保留期审计。"""
    retention = get_settings().audit_retention_days
    stmts = {
        "sms_codes": "DELETE FROM sms_codes WHERE expires_at < now() - interval '7 days'",
        "used_refresh_tokens": "DELETE FROM used_refresh_tokens WHERE expires_at < now()",
        "outbox_done": (
            "DELETE FROM outbox_tasks WHERE status IN ('done', 'discarded') "
            "AND updated_at < now() - interval '7 days'"
        ),
        # 保留期走绑定参数(make_interval)
        "audit_log": (
            "DELETE FROM audit_log WHERE created_at < now() - make_interval(days => :days)"
        ),
        # 限流计数窗口最长 24h,留 2 天余量
        "rate_limit_counters": (
            "DELETE FROM rate_limit_counters WHERE updated_at < now() - interval '2 days'"
        ),
    }
    counts: dict[str, int] = {}
    async with sm() as session:
        for name, stmt in stmts.items():
            params = {"days": retention} if name == "audit_log" else {}
            result = cast(CursorResult[Any], await session.execute(text(stmt), params))
            counts[name] = result.rowcount or 0
        await session.commit()
    if any(counts.values()):
        logger.info("cleanup_expired_rows", **counts)
    return counts
