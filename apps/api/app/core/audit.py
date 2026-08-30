"""审计:所有写操作(POST/PUT/PATCH/DELETE)由中间件统一落 audit_log。

actor 由鉴权依赖写入 request.state.audit_actor;管理端动作带 "admin." 前缀。
默认走独立 session(fail-open,业务失败也留痕,审计失败不拖垮业务);
资金域出金动作(退款打款/调账复核/人工补单)改用 write_audit_sync 与业务同事务:
审计写失败即业务失败回滚——宁可不出金,不可无留痕。
"""

from datetime import datetime
from typing import Any

from fastapi import Request
from sqlalchemy import String, func, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.db import Base, get_sessionmaker
from app.core.errors import audit_unavailable_response
from app.core.http import client_ip
from app.core.logging import get_logger
from app.core.metrics import AUDIT_WRITE_FAILED_TOTAL

logger = get_logger(__name__)

AUDIT_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# 不审计的路径前缀(高频只读或基础设施)。endpoint-auth 是网关对每一次服务调用的同步
# 鉴权回调(方法跟着客户端走,含 POST),审计它等于按服务 QPS 往 audit_log 灌行;
# 端点侧的可观测走结构化日志与网关访问日志,不走审计表。
AUDIT_EXCLUDE_PREFIXES = (
    "/healthz",
    "/metrics",
    "/docs",
    "/openapi.json",
    "/api/internal/v1/endpoint-auth",
)

# 审计 fail-open 的升级闸:连续失败超阈值后写操作 fail-closed(宁可停写,不留无审计窗口)。
# 单条失败仍 fail-open(抖动不拖垮业务);任一次成功即复位。资金域出金走同步审计
# 同事务(write_audit_sync),本就 fail-closed,不经本闸。
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
    """半开探针:闸门关闭期间用一次 SELECT 1 探活;通了即复位放行(自愈),不通继续 503。
    被拦的写请求本身不再产生审计写(不推进计数),没有探针闸门会永久卡死。"""
    try:
        async with get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


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


class AuditMiddleware:
    """纯 ASGI 实现(对齐 ObservabilityMiddleware):不经过 BaseHTTPMiddleware 的
    请求/响应包装——流式路由(强制审计的 CSV 导出)不再被整段缓冲,
    anyio 任务/取消语义差异也一并消失。响应头落定(http.response.start)即按状态码
    落审计,与原「call_next 返回后落行」语义一致;连响应都没构造出来的异常按 500 留痕。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # Request(scope) 是零成本视图:state/client 与原请求共享同一 scope
        request = Request(scope)
        status_holder = {"status": 500}

        # 审计闸:连续失败超阈值时写操作 fail-closed(读/基础设施路径不受影响);
        # 关闸态先探活,通了即复位放行(自愈),避免「被拦请求不写审计→永不复位」死锁
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
            await send(message)

        try:
            await self.app(scope, receive, send_capture)
        finally:
            # 未捕获异常已被内层 Uniform500Middleware 渲成 500 响应,到这里按状态码落行
            await _write_audit_row(request, status_holder["status"])


def _build_audit_row(request: Request, result: int) -> AuditLog:
    """审计行的单一定义点(异步独立事务与资金域同步事务两条写路径共用)。"""
    actor: AuditActor | None = getattr(request.state, "audit_actor", None)
    path = request.url.path
    action_prefix = "admin." if path.startswith("/api/admin/") else ""
    return AuditLog(
        actor_type=actor.actor_type if actor else "anonymous",
        actor_id=actor.actor_id if actor else None,
        action=f"{action_prefix}{request.method} {path}",
        target=getattr(request.state, "audit_target", None),
        ip=client_ip(request),
        result=result,
        detail=getattr(request.state, "audit_detail", None),
    )


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
        async with get_sessionmaker()() as session:
            session.add(_build_audit_row(request, result))
            await session.commit()
        reset_audit_gate()
    except Exception:
        # 审计失败不得影响业务响应;但必须可告警(失败即留痕缺口,资金域已改同步审计)
        # 连续失败累计进 fail-closed 闸(见 AuditMiddleware 的审计闸)
        global _audit_consecutive_failures
        _audit_consecutive_failures += 1
        AUDIT_WRITE_FAILED_TOTAL.inc()
        logger.exception("audit_write_failed", path=path)


async def write_audit_sync(request: Request, session: AsyncSession, *, result: int = 200) -> None:
    """资金域关键动作的同步审计:与业务同一事务写入(审计失败即业务失败回滚)。

    在业务 service 的最终 commit 前调用(经 service 的 audit_writer 钩子传入);
    写后置 audit_synced 标志,中间件的通用审计行跳过本请求,避免双写。
    """
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
