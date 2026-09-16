"""Edge cut-off (always on in prod, off in dev/test): /api/admin/* needs both Host = admin_host and
X-Admin-Edge-Token = admin_edge_token (injected by the admin-domain nginx, see
deploy/app/nginx.admin.conf); /metrics and /api/internal requests carrying X-Forwarded-For
get 404."""

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
            elif (path.startswith(("/metrics", "/api/internal"))) and "x-forwarded-for" in headers:
                await PlainTextResponse("not found", status_code=404)(scope, receive, send)
                return
        await self.app(scope, receive, send)
