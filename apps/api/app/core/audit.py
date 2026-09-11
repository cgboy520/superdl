"""审计:所有写操作由中间件统一落 audit_log(独立 session,fail-open);actor 取
request.state.audit_actor,管理端动作带 "admin." 前缀。资金域出金动作改用 write_audit_sync
与业务同事务(审计写失败即业务回滚)。"""

from datetime import datetime
from typing import Any

from fastapi import Request
from sqlalchemy import String, func, text
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
# 不审计的路径前缀(高频只读、基础设施、网关 endpoint-auth 回调)
AUDIT_EXCLUDE_PREFIXES = (
    "/healthz",
    "/metrics",
    "/docs",
    "/openapi.json",
    "/api/internal/v1/endpoint-auth",
)

# 审计闸:连续失败超阈值后写操作 fail-closed,任一次成功即复位;同步审计不经本闸
AUDIT_FAIL_CLOSED_THRESHOLD = 10
_audit_consecutive_failures = 0


def reset_audit_gate() -> None:
    """测试隔离用:进程内闸门是全局态,逐用例复位。"""
    global _audit_consecutive_failures
    _audit_consecutive_failures = 0


def audit_gate_open() -> bool:
    """写操作准入:连续失败未超阈放行;超阈 fail-closed(监控指标 AUDIT_WRITE_FAILED_TOTAL 告警)。"""
    return _audit_consecutive_failures < AUDIT_FAIL_CLOSED_THRESHOLD


async def audit_probe_ok() -> bool:
    """半开探针:闸门关闭期间 SELECT 1 探活,通了即复位放行,不通继续 503。"""
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

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_type: Mapped[str] = mapped_column(String(16))  # user / admin / system / anonymous
    actor_id: Mapped[str | None] = mapped_column(String(64))
    # action/target 入库前按列宽截断(_build_audit_row)
    action: Mapped[str] = mapped_column(String(ACTION_MAX_LENGTH), index=True)  # POST /api/v1/...
    target: Mapped[str | None] = mapped_column(String(TARGET_MAX_LENGTH))  # 如 instance:uuid
    ip: Mapped[str | None] = mapped_column(INET)
    result: Mapped[int]  # HTTP 状态码
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # 与结构化日志/响应头 X-Request-ID 同一值
    request_id: Mapped[str | None] = mapped_column(String(REQUEST_ID_MAX_LENGTH))
    user_agent: Mapped[str | None] = mapped_column(String(USER_AGENT_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)


class AuditActor:
    """鉴权依赖构造后挂到 request.state.audit_actor。"""

    def __init__(self, actor_type: str, actor_id: str | None) -> None:
        self.actor_type = actor_type
        self.actor_id = actor_id


class AuditMiddleware:
    """纯 ASGI 中间件(不走 BaseHTTPMiddleware,不缓冲流式响应):响应头落定即按状态码落审计,
    未构造出响应的异常按 500 留痕。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        status_holder = {"status": 500}

        # 审计闸:关闸态先探活,通了即复位放行
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
                # request_id 从响应头抄(本中间件在 Observability 外层,contextvar 此时已解绑)
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
    """审计行的单一定义点(两条写路径共用);action/target/user_agent 按列宽截断。"""
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
    # 默认只审计写操作;敏感读端点显式 mark_audited_read 后也落一行
    if request.method not in AUDIT_METHODS and not getattr(request.state, "audit_force", False):
        return
    if getattr(request.state, "audit_synced", False):
        return  # 同步审计已随业务事务落库(资金域 write_audit_sync),防双写
    path = request.url.path
    if path.startswith(AUDIT_EXCLUDE_PREFIXES):
        return
    try:
        async with get_sessionmaker()() as session:
            session.add(_build_audit_row(request, result))
            await session.commit()
        reset_audit_gate()
    except Exception:
        # 审计失败不影响业务响应;连续失败累计进审计闸
        global _audit_consecutive_failures
        _audit_consecutive_failures += 1
        AUDIT_WRITE_FAILED_TOTAL.inc()
        logger.exception("audit_write_failed", path=path)


async def write_audit_sync(request: Request, session: AsyncSession, *, result: int = 200) -> None:
    """资金域同步审计:与业务同事务写入,在最终 commit 前调用;写后置 audit_synced,中间件跳过。"""
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
