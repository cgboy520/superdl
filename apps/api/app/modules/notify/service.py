"""notify 服务门面。WP9 前为最小桩:站内信/短信仅落日志,接口先稳定。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)


async def send_low_balance_warning(
    _session: AsyncSession, user_id: int, *, est_hours: float, balance: str
) -> None:
    logger.warning(
        "low_balance_warning", user_id=user_id, est_hours=round(est_hours, 1), balance=balance
    )


async def send_arrears_notice(
    _session: AsyncSession, user_id: int, *, action: str, detail: str
) -> None:
    logger.warning("arrears_notice", user_id=user_id, action=action, detail=detail)
