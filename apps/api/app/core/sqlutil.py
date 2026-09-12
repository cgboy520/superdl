"""SQL 小工具:LIKE/ILIKE 元字符转义(单一定义点,与 SQLAlchemy 的 escape="\\" 配套)、
日内单号序列、FOR UPDATE 取行或 404。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.errors import AppError, ErrorCode


def like_escape(q: str) -> str:
    """转义 LIKE 元字符(\\ % _):调用方拼 f"%{like_escape(q)}%" 并传 escape="\\"。"""
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def next_daily_seq(
    session: AsyncSession, no_col: InstrumentedAttribute[str], prefix: str
) -> int:
    """日内单号下一序列(count("{prefix}-%") + 1);并发撞车由唯一约束兜底,调用方换序列重试。"""
    count = (
        await session.execute(select(func.count()).where(no_col.like(f"{prefix}-%")))
    ).scalar_one()
    return count + 1


async def get_for_update_or_404[T](
    session: AsyncSession, model: type[T], row_id: int, *, key: str
) -> T:
    """FOR UPDATE 按主键取行;不存在 → NOT_FOUND(文案键 key)。审批流 / 缺口重放的行锁入口共用。"""
    row = await session.get(model, row_id, with_for_update=True)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, key=key, http_status=404)
    return row
