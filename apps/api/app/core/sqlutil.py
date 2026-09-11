"""SQL LIKE/ILIKE 元字符转义(单一定义点)。与 SQLAlchemy 的 escape="\\" 配套。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute


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
