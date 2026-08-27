"""边缘收口:管理端 API、/metrics 与网关内部回调不从公网 API 域暴露。

- /api/admin/*:仅放行 Host = admin_host 的请求。admin SPA 经 admin 域 nginx
  同源反代 /api/admin/(deploy/app/nginx.admin.conf),公网 api 域本就不需要承载
  管理端流量。Host 判定在此拓扑下是安全的:ingress 按 Host 路由,伪造 admin Host
  的请求会被路由到 admin SPA 而不是本服务。
- /metrics 与 /api/internal:带 X-Forwarded-For 的请求(经网关进入)一律 404;
  集群内直连 Pod/Service 不携带该头。
  /metrics 侧的 Bearer 校验(main.py metrics_guard)保持不变,双闸并存。
  /api/internal 是网关鉴权回调(SecurityPolicy.extAuth → endpoint-auth),
  它本身不带鉴权(带了就成了鸡生蛋),唯一的保护就是这条收口 —— 而平台 API 的
  HTTPRoute 是 path: / 的前缀匹配,不收口它就跟着一起暴露在公网上,
  任何人都能拿它当 API Key 的在线爆破预言机。
启用口径:prod 恒开,无开关。environment 只有 dev/test/prod 三值,类生产环境(staging)
也以 prod 运行(独立 secrets),收口随之生效;本地 dev/test 无 ingress,不启用。
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
        if settings.environment == "prod":
            path: str = scope.get("path", "")
            headers = Headers(scope=scope)
            if path.startswith("/api/admin"):
                host = (headers.get("host") or "").split(":")[0].lower()
                if host != settings.admin_host.lower():
                    await PlainTextResponse("not found", status_code=404)(scope, receive, send)
                    return
            elif (
                path.startswith("/metrics") or path.startswith("/api/internal")
            ) and "x-forwarded-for" in headers:
                await PlainTextResponse("not found", status_code=404)(scope, receive, send)
                return
        await self.app(scope, receive, send)
