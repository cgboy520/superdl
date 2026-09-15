"""写请求和显式标记的读请求落审计,排除路径及同步审计请求不重复写入。

中间件独立提交;连续失败达到阈值后,受审计的写请求须通过 DB 探针,否则返回 503。
同步审计由调用方与业务同事务提交;管理端动作带 admin. 前缀。
"""

from datetime import datetime
from typing import Any

from fastapi import Request
from sqlalchemy import Index, String, func, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.db import Base, get_sessionmaker
from app.core.errors import audit_unavailable_response, current_request_id
from app.core.http import client_ip
from app.core.logging import get_logger
from app.core.metrics import AUDIT_WRITE_FAILED_TOTAL

logger = get_logger(__name__)

AUDIT_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
AUDIT_EXCLUDE_PREFIXES = (
    "/healthz",
    "/metrics",
    "/docs",
    "/openapi.json",
    "/api/internal/v1/endpoint-auth",
)

AUDIT_FAIL_CLOSED_THRESHOLD = 10
_audit_consecutive_failures = 0


def reset_audit_gate() -> None:
    """复位进程内连续审计失败计数。"""
    global _audit_consecutive_failures
    _audit_consecutive_failures = 0


def audit_gate_open() -> bool:
    """连续失败次数小于阈值时允许写请求。"""
    return _audit_consecutive_failures < AUDIT_FAIL_CLOSED_THRESHOLD


async def audit_probe_ok() -> bool:
    """用独立 session 执行 SELECT 1,返回是否成功;不修改闸门状态。"""
    try:
        async with get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


ACTION_MAX_LENGTH = 128
TARGET_MAX_LENGTH = 256
REQUEST_ID_MAX_LENGTH = 64
USER_AGENT_MAX_LENGTH = 256


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_actor_id_id", "actor_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(ACTION_MAX_LENGTH), index=True)
    target: Mapped[str | None] = mapped_column(String(TARGET_MAX_LENGTH))
    ip: Mapped[str | None] = mapped_column(INET)
    result: Mapped[int]
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    request_id: Mapped[str | None] = mapped_column(String(REQUEST_ID_MAX_LENGTH))
    user_agent: Mapped[str | None] = mapped_column(String(USER_AGENT_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)


class AuditActor:
    """鉴权依赖构造后挂到 request.state.audit_actor。"""

    def __init__(self, actor_type: str, actor_id: str | None) -> None:
        self.actor_type = actor_type
        self.actor_id = actor_id


class AuditMiddleware:
    """不缓冲响应;下游退出时按捕获的响应状态审计,未收到响应头时按 500 记录。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        status_holder = {"status": 500}

        if (
            request.method in AUDIT_METHODS
            and not request.url.path.startswith(AUDIT_EXCLUDE_PREFIXES)
            and not audit_gate_open()
        ):
            if await audit_probe_ok():
                reset_audit_gate()
            else:
                await audit_unavailable_response()(scope, receive, send)
                return

        async def send_capture(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                request.state.audit_request_id = Headers(scope=message).get("x-request-id")
            await send(message)

        try:
            await self.app(scope, receive, send_capture)
        finally:
            await _write_audit_row(request, status_holder["status"])


def _clip(value: str | None, limit: int) -> str | None:
    return value[:limit] if value is not None else None


def _audit_request_id(request: Request) -> str | None:
    """审计行的 request_id:中间件路径从响应头抄,同步审计路径读 contextvar。"""
    return getattr(request.state, "audit_request_id", None) or current_request_id()


def _build_audit_row(request: Request, result: int) -> AuditLog:
    """构造审计行;action、target、request_id、user_agent 按列宽截断。"""
    actor: AuditActor | None = getattr(request.state, "audit_actor", None)
    path = request.url.path
    action_prefix = "admin." if path.startswith("/api/admin/") else ""
    return AuditLog(
        actor_type=actor.actor_type if actor else "anonymous",
        actor_id=actor.actor_id if actor else None,
        action=f"{action_prefix}{request.method} {path}"[:ACTION_MAX_LENGTH],
        target=_clip(getattr(request.state, "audit_target", None), TARGET_MAX_LENGTH),
        ip=client_ip(request),
        result=result,
        detail=getattr(request.state, "audit_detail", None),
        request_id=_clip(_audit_request_id(request), REQUEST_ID_MAX_LENGTH),
        user_agent=_clip(request.headers.get("user-agent"), USER_AGENT_MAX_LENGTH),
    )


async def _write_audit_row(request: Request, result: int) -> None:
    if request.method not in AUDIT_METHODS and not getattr(request.state, "audit_force", False):
        return
    if getattr(request.state, "audit_synced", False):
        return
    path = request.url.path
    if path.startswith(AUDIT_EXCLUDE_PREFIXES):
        return
    try:
        async with get_sessionmaker()() as session:
            session.add(_build_audit_row(request, result))
            await session.commit()
        reset_audit_gate()
    except Exception:
        global _audit_consecutive_failures
        _audit_consecutive_failures += 1
        AUDIT_WRITE_FAILED_TOTAL.inc()
        logger.exception("audit_write_failed", path=path)


async def write_audit_sync(request: Request, session: AsyncSession, *, result: int = 200) -> None:
    """将审计行加入业务 session 并标记中间件跳过;调用方负责 flush 和 commit。"""
    session.add(_build_audit_row(request, result))
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
