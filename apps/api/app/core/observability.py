"""请求可观测性(纯 ASGI):request-id 贯穿 + HTTP 时延直方图。

- 入站带 X-Request-ID 则沿用(网关生成),否则生成;响应回带同名头;
- structlog contextvars 绑定 request_id,一次请求的全部日志可串联;
- 直方图 route 取路由模板(scope["route"]),未命中记 "unmatched",避免高基数。
"""

import time
from uuid import uuid4

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.metrics import HTTP_REQUEST_DURATION


def _full_route_template(scope: Scope) -> str:
    """完整路由模板:include_router 下 route.path 不含前缀,用 path_params 反推。

    例:path=/api/v1/instances/abc + route.path=/instances/{uuid} + {"uuid": "abc"}
    → /api/v1/instances/{uuid}。避免直方图 label 高基数,也避免用户端/管理端同名混淆。
    """
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
        request_id = Headers(scope=scope).get("x-request-id") or uuid4().hex[:16]
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
