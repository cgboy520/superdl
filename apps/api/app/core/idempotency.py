"""创建类接口的幂等键重放:按 (归属列, 键) 查已有行,窗内则重放。

重放的响应形态(200 + X-Idempotent-Replay)由路由经 core/http.mark_idempotent_replay 统一;
并发同键由各表 UNIQUE(归属列, idempotency_key) 兜底,撞约束后再以本函数回查胜出方。

异参检测(对齐 Stripe 惯例):调用方传入 fingerprint(request_fingerprint() 对全部
业务形态参数取 sha256),命中重放且指纹不符抛 409 —— 弱键被复用到不同请求时得到
显式拒绝,而不是静默返回上一单(那会把「金额/规格改了」的重试错当成重放)。
"""

import hashlib
from datetime import datetime, timedelta
from typing import Any, Protocol, overload

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, Mapped

from app.core.errors import AppError, ErrorCode
from app.core.timeutil import ensure_utc, now_utc

# 幂等键有效期(实例 / 数据盘):窗口内重放返回既有资源;窗口外同一键按新单处理
IDEMPOTENCY_WINDOW = timedelta(hours=24)


class _HasIdempotencyKey(Protocol):
    idempotency_key: Mapped[str | None]
    created_at: Mapped[datetime]


class _HasRequestFingerprint(_HasIdempotencyKey, Protocol):
    """落库了请求指纹的创建类表(orders/instances/data_disks/refund_requests/
    invoice_requests 与 adminapi 调账单)——只有它们能走带 fingerprint 的重放比对。"""

    request_fingerprint: Mapped[str | None]


def request_fingerprint(*parts: object) -> str:
    """异参检测指纹:对「决定本单业务形态的全部请求参数」取 sha256。

    调用方按固定顺序给出参数(先归属列,后业务参数);repr 区分 None 与空串、
    数字与字符串,dict 请先排序成 items 再传入保证确定性。
    """
    return hashlib.sha256("|".join(repr(p) for p in parts).encode()).hexdigest()


@overload
async def find_replay[RowT: _HasIdempotencyKey](
    session: AsyncSession,
    model: type[RowT],
    *,
    owner_col: InstrumentedAttribute[int] | None,
    owner_id: int | None,
    key: str,
    window: timedelta | None = None,
    fingerprint: None = None,
) -> RowT | None: ...


@overload
async def find_replay[RowT: _HasRequestFingerprint](
    session: AsyncSession,
    model: type[RowT],
    *,
    owner_col: InstrumentedAttribute[int] | None,
    owner_id: int | None,
    key: str,
    window: timedelta | None = None,
    fingerprint: str,
) -> RowT | None: ...


async def find_replay(
    session: AsyncSession,
    model: type[Any],
    *,
    owner_col: InstrumentedAttribute[int] | None,
    owner_id: int | None,
    key: str,
    window: timedelta | None = None,
    fingerprint: str | None = None,
) -> Any | None:
    """返回同 (owner, key) 的已有行,没有则 None。

    owner_col 为 None 表示键全局唯一(公告)。window 给定时只认 created_at 在窗内的行:
    窗外的行先释放键位(UNIQUE(归属列, idempotency_key) 不再挡新单)再按无既有行处理。
    fingerprint 给定时做异参检测:行上指纹(老行为 NULL,兼容放行)与本次不符抛 409。
    """
    stmt = select(model).where(model.idempotency_key == key)
    if owner_col is not None:
        stmt = stmt.where(owner_col == owner_id)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is None:
        return None
    if window is not None and now_utc() - ensure_utc(existing.created_at) >= window:
        existing.idempotency_key = None
        await session.flush()
        return None
    if fingerprint is not None:
        stored = existing.request_fingerprint
        # 老行(指纹上线前落库)/ 窗口外释放过键位的行没有指纹:不比对,按重放放行
        if stored is not None and stored != fingerprint:
            raise AppError(
                ErrorCode.CONFLICT,
                key="common.idempotencyKeyMismatch",
                http_status=409,
            )
    return existing
