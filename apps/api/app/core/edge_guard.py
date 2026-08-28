"""边缘收口:管理端 API、/metrics 与网关内部回调不从公网 API 域暴露。

prod 恒开、无开关;dev/test 无 ingress,不启用。

- /api/admin/*:双闸——Host = admin_host 且 X-Admin-Edge-Token = admin_edge_token
  (admin SPA 经 admin 域 nginx 同源反代 /api/admin/ 并注入该头,见
  deploy/app/nginx.admin.conf)。只验 Host 时,集群内任何能直连 API Service 的调用方
  伪造 Host 即穿闸;共享密钥把「知道 admin_host」与「能进 admin 反代面」分开。
- /metrics 与 /api/internal:带 X-Forwarded-For 的请求(经网关进入)一律 404,
  集群内直连 Pod/Service 不携带该头;/metrics 另有 Bearer 校验,双闸并存。
  /api/internal 是网关鉴权回调(SecurityPolicy.extAuth → endpoint-auth),自身不带鉴权,
  这条收口是它唯一的保护 —— 平台 API 的 HTTPRoute 按 path: / 前缀匹配,不收口即随平台
  API 一起暴露在公网,成为 API Key 的在线爆破预言机。
"""

import hmac

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
                # prod 启动校验已 fail-fast 保证密钥非空;compare_digest 防时序侧信道
                edge = settings.admin_edge_token
                token_ok = bool(edge) and hmac.compare_digest(
                    (headers.get("x-admin-edge-token") or "").encode(), edge.encode()
                )
                if host != settings.admin_host.lower() or not token_ok:
                    await PlainTextResponse("not found", status_code=404)(scope, receive, send)
                    return
            elif (
                path.startswith("/metrics") or path.startswith("/api/internal")
            ) and "x-forwarded-for" in headers:
                await PlainTextResponse("not found", status_code=404)(scope, receive, send)
                return
        await self.app(scope, receive, send)
