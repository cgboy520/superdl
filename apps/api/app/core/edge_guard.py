"""边缘收口(prod 恒开,dev/test 不启用):/api/admin/* 双闸 Host = admin_host 且
X-Admin-Edge-Token = admin_edge_token(admin 域 nginx 注入,见 deploy/app/nginx.admin.conf);
/metrics 与 /api/internal 带 X-Forwarded-For 的请求一律 404。"""

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
                edge = settings.admin_edge_token
                token_ok = hmac.compare_digest(
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
