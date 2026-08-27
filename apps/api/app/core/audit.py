"""审计:所有写操作(POST/PUT/PATCH/DELETE)由中间件统一落 audit_log。

actor 由鉴权依赖写入 request.state.audit_actor;管理端动作带 "admin." 前缀。
默认走独立 session(fail-open,业务失败也留痕,审计失败不拖垮业务);
资金域出金动作(退款打款/调账复核/人工补单)改用 write_audit_sync 与业务同事务:
审计写失败即业务失败回滚——宁可不出金,不可无留痕。
"""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from fastapi import Request, Response
from sqlalchemy import String, func
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.db import Base, get_sessionmaker
from app.core.logging import get_logger
from app.core.metrics import AUDIT_WRITE_FAILED_TOTAL

logger = get_logger(__name__)

AUDIT_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# 不审计的路径前缀(高频只读或基础设施)。
# endpoint-auth 是网关对**每一次**服务调用的同步鉴权回调,方法跟着客户端走(含 POST):
# 审计它等于按服务实例的 QPS 往 audit_log 灌行,真正要查的写操作会被埋掉。
# 端点侧的可观测走结构化日志与网关访问日志,不走审计表。
AUDIT_EXCLUDE_PREFIXES = (
    "/healthz",
    "/metrics",
    "/docs",
    "/openapi.json",
    "/api/internal/v1/endpoint-auth",
)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_type: Mapped[str] = mapped_column(String(16))  # user / admin / system / anonymous
    actor_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(128), index=True)  # e.g. POST /api/v1/instances
    target: Mapped[str | None] = mapped_column(String(256))  # 资源定位,如 instance:uuid
    ip: Mapped[str | None] = mapped_column(INET)
    result: Mapped[int]  # HTTP 状态码
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)


class AuditActor:
    """鉴权依赖构造后挂到 request.state.audit_actor。"""

    def __init__(self, actor_type: str, actor_id: str | None) -> None:
        self.actor_type = actor_type
        self.actor_id = actor_id


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # 未捕获异常由内层 Uniform500Middleware 渲染成 500 响应(main.py 的中间件顺序),
        # 到这里已是普通响应:按状态码落行,500 同样留痕
        response = await call_next(request)
        await _write_audit_row(request, response.status_code)
        return response


async def _write_audit_row(request: Request, result: int) -> None:
    # 默认只审计写操作;敏感读端点显式调 mark_audited_read 后也落一行
    if request.method not in AUDIT_METHODS and not getattr(request.state, "audit_force", False):
        return
    if getattr(request.state, "audit_synced", False):
        return  # 同步审计已随业务事务落库(资金域 write_audit_sync),防双写
    path = request.url.path
    if path.startswith(AUDIT_EXCLUDE_PREFIXES):
        return
    try:
        actor: AuditActor | None = getattr(request.state, "audit_actor", None)
        target: str | None = getattr(request.state, "audit_target", None)
        detail: dict[str, Any] | None = getattr(request.state, "audit_detail", None)
        action_prefix = "admin." if path.startswith("/api/admin/") else ""
        async with get_sessionmaker()() as session:
            session.add(
                AuditLog(
                    actor_type=actor.actor_type if actor else "anonymous",
                    actor_id=actor.actor_id if actor else None,
                    action=f"{action_prefix}{request.method} {path}",
                    target=target,
                    ip=request.client.host if request.client else None,
                    result=result,
                    detail=detail,
                )
            )
            await session.commit()
    except Exception:
        # 审计失败不得影响业务响应;但必须可告警(失败即留痕缺口,资金域已改同步审计)
        AUDIT_WRITE_FAILED_TOTAL.inc()
        logger.exception("audit_write_failed", path=path)


async def write_audit_sync(request: Request, session: AsyncSession, *, result: int = 200) -> None:
    """资金域关键动作的同步审计:与业务同一事务写入(审计失败即业务失败回滚)。

    在业务 service 的最终 commit 前调用(经 service 的 audit_writer 钩子传入);
    写后置 audit_synced 标志,中间件的通用审计行跳过本请求,避免双写。
    """
    actor: AuditActor | None = getattr(request.state, "audit_actor", None)
    target: str | None = getattr(request.state, "audit_target", None)
    detail: dict[str, Any] | None = getattr(request.state, "audit_detail", None)
    path = request.url.path
    action_prefix = "admin." if path.startswith("/api/admin/") else ""
    session.add(
        AuditLog(
            actor_type=actor.actor_type if actor else "anonymous",
            actor_id=actor.actor_id if actor else None,
            action=f"{action_prefix}{request.method} {path}",
            target=target,
            ip=request.client.host if request.client else None,
            result=result,
            detail=detail,
        )
    )
    request.state.audit_synced = True


def set_audit_target(request: Request, target: str, detail: dict[str, Any] | None = None) -> None:
    """业务代码在写操作里标注审计目标(如 instance:uuid)。detail 禁止落凭据明文,只落键名。"""
    request.state.audit_target = target
    if detail is not None:
        request.state.audit_detail = detail


def mark_audited_read(request: Request, target: str, detail: dict[str, Any] | None = None) -> None:
    """把一次**读**也记进审计(敏感检索用)。"""
    request.state.audit_force = True
    set_audit_target(request, target, detail)
