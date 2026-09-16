"""Request observability (pure ASGI): a well-formed X-Request-ID is kept, otherwise generated and
returned, bound into structlog contextvars;
the HTTP latency histogram takes the route template as route, "unmatched" when none matched."""

import re
import time
from uuid import uuid4

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.metrics import HTTP_REQUEST_DURATION

_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")


def request_id_from_header(value: str | None) -> str:
    """An inbound X-Request-ID is kept only when it matches `[A-Za-z0-9._-]{1,64}`, otherwise 16 hex
    digits are generated."""
    if value is not None and _REQUEST_ID_RE.fullmatch(value):
        return value
    return uuid4().hex[:16]


def _full_route_template(scope: Scope) -> str:
    """Full route template (including include_router prefixes), reconstructed from path_params:
    /api/v1/instances/{uuid}."""
    template = getattr(scope.get("route"), "path", None)
    if not template:
        return "unmatched"
    concrete = template
    for key, value in scope.get("path_params", {}).items():
        concrete = concrete.replace("{" + key + "}", str(value))
    raw: str = scope.get("path", "")
    if raw.endswith(concrete):
        return raw[: len(raw) - len(concrete)] + template
    return template


class ObservabilityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = request_id_from_header(Headers(scope=scope).get("x-request-id"))
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()
        status_holder = {"status": 500}

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                MutableHeaders(scope=message).setdefault("X-Request-ID", request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            route_path = _full_route_template(scope)
            HTTP_REQUEST_DURATION.labels(
                method=scope.get("method", "-"),
                route=route_path,
                status=str(status_holder["status"]),
            ).observe(time.perf_counter() - start)
            structlog.contextvars.unbind_contextvars("request_id")
