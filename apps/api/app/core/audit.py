"""审计:所有写操作(POST/PUT/PATCH/DELETE)由中间件统一落 audit_log。

actor 由鉴权依赖写入 request.state.audit_actor;管理端动作带 "admin." 前缀。
审计必须用独立 session 写入,不得并入业务事务:业务失败也要留痕。
"""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from fastapi import Request, Response
from sqlalchemy import String, func
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.db import Base, get_sessionmaker
from app.core.logging import get_logger

logger = get_logger(__name__)

AUDIT_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# 不审计的路径前缀(高频只读或基础设施)
AUDIT_EXCLUDE_PREFIXES = ("/healthz", "/metrics", "/docs", "/openapi.json")


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
        response = await call_next(request)
        # 默认只审计写操作;敏感读端点显式调 mark_audited_read 后也落一行
        if request.method not in AUDIT_METHODS and not getattr(request.state, "audit_force", False):
            return response
        path = request.url.path
        if path.startswith(AUDIT_EXCLUDE_PREFIXES):
            return response
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
                        result=response.status_code,
                        detail=detail,
                    )
                )
                await session.commit()
        except Exception:
            # 审计失败不得影响业务响应
            logger.exception("audit_write_failed", path=path)
        return response


def set_audit_target(request: Request, target: str, detail: dict[str, Any] | None = None) -> None:
    """业务代码在写操作里标注审计目标(如 instance:uuid)。detail 禁止落凭据明文,只落键名。"""
    request.state.audit_target = target
    if detail is not None:
        request.state.audit_detail = detail


def mark_audited_read(request: Request, target: str, detail: dict[str, Any] | None = None) -> None:
    """把一次**读**也记进审计(敏感检索用)。"""
    request.state.audit_force = True
    set_audit_target(request, target, detail)
