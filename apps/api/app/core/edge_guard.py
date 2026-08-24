"""边缘收口:管理端 API 与 /metrics 不从公网 API 域暴露。

- /api/admin/*:仅放行 Host = admin_host 的请求。admin SPA 经 admin 域 nginx
  同源反代 /api/admin/(deploy/app/nginx.admin.conf),公网 api 域本就不需要承载
  管理端流量。Host 判定在此拓扑下是安全的:ingress 按 Host 路由,伪造 admin Host
  的请求会被路由到 admin SPA 而不是本服务。
- /metrics:带 X-Forwarded-For 的请求(经 ingress 进入)一律 404;集群内
  Prometheus 直刮 Pod/Service 不携带该头。Bearer 校验(main.py metrics_guard)
  保持不变,双闸并存。
启用口径:edge_guard_enabled 显式布尔优先;默认(None)仅 prod 启用。
复用部署清单的类生产环境(staging 等)必须显式开启——否则管理端 API 只剩
JWT audience 一道闸暴露在公网 API 域上。本地 dev/test 无 ingress,默认不受影响。
"""

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import get_settings


class EdgeGuardMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        settings = get_settings()
        enabled = (
            settings.environment == "prod"
            if settings.edge_guard_enabled is None
            else settings.edge_guard_enabled
        )
        if enabled:
            path: str = scope.get("path", "")
            headers = Headers(scope=scope)
            if path.startswith("/api/admin"):
                host = (headers.get("host") or "").split(":")[0].lower()
                if host != settings.admin_host.lower():
                    await PlainTextResponse("not found", status_code=404)(scope, receive, send)
                    return
            elif path.startswith("/metrics") and "x-forwarded-for" in headers:
                await PlainTextResponse("not found", status_code=404)(scope, receive, send)
                return
        await self.app(scope, receive, send)
