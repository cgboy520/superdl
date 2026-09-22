"""Audit every write request and explicitly marked read requests except excluded paths.

The middleware commits independently; once consecutive failures reach the threshold, audited write
requests must pass a committed audit insert probe or get 503. Synchronous audits commit in the
caller's business transaction; admin actions carry the admin. prefix.
"""

from datetime import datetime
from typing import Any

from fastapi import Request
from sqlalchemy import Index, String, func
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
    """Reset the in-process consecutive audit failure counter."""
    global _audit_consecutive_failures
    _audit_consecutive_failures = 0


def audit_gate_open() -> bool:
    """Allow write requests while the consecutive failure count is below the threshold."""
    return _audit_consecutive_failures < AUDIT_FAIL_CLOSED_THRESHOLD


async def audit_probe_ok() -> bool:
    """Commit an audit row on an independent session; leaves the gate state alone.

    Read connectivity cannot prove that audit inserts or commits are permitted. Keep the probe
    row as evidence of recovery rather than relying on rollback or append-only-table deletes.
    """
    try:
        async with get_sessionmaker()() as session:
            session.add(AuditLog(actor_type="system", action="audit.recovery_probe", result=200))
            await session.commit()
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
    """Attached to request.state.audit_actor once the auth dependency has run."""

    def __init__(self, actor_type: str, actor_id: str | None) -> None:
        self.actor_type = actor_type
        self.actor_id = actor_id


class AuditMiddleware:
    """Does not buffer the response; audits with the captured status when downstream exits, 500 when
    no response headers were seen."""

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
    """request_id of the audit row: copied from the response header on the middleware path, read
    from the contextvar on the synchronous path."""
    return getattr(request.state, "audit_request_id", None) or current_request_id()


def _build_audit_row(request: Request, result: int) -> AuditLog:
    """Build the audit row; action, target, request_id and user_agent are truncated to column
    width."""
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
    """Add the audit row to the business session and mark the middleware to skip; the caller flushes
    and commits."""
    session.add(_build_audit_row(request, result))
    request.state.audit_synced = True


def set_audit_target(request: Request, target: str, detail: dict[str, Any] | None = None) -> None:
    """Business code marks the audit target inside a write (e.g. instance:uuid). detail must never
    carry credential plaintext, key names only."""
    request.state.audit_target = target
    if detail is not None:
        request.state.audit_detail = detail


def mark_audited_read(request: Request, target: str, detail: dict[str, Any] | None = None) -> None:
    """Record a **read** in the audit as well (for sensitive lookups)."""
    request.state.audit_force = True
    set_audit_target(request, target, detail)
