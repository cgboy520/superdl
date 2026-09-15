"""安全响应头(纯 ASGI):CSP `default-src 'none'` + 禁嵌入,/docs /redoc 豁免 CSP;HSTS 仅 prod;
API、文档与指标响应 `Cache-Control: no-store`。"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings

_DOCS_PATHS = ("/docs", "/redoc")
_NO_STORE_PATHS = ("/api/", "/metrics", "/docs", "/redoc", "/openapi.json")


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault(
                    "Permissions-Policy",
                    "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
                    "bluetooth=(), serial=(), accelerometer=(), gyroscope=(), magnetometer=()",
                )
                headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
                headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
                if not path.startswith(_DOCS_PATHS):
                    headers.setdefault(
                        "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
                    )
                if path.startswith(_NO_STORE_PATHS):
                    headers.setdefault("Cache-Control", "no-store")
                if get_settings().environment == "prod":
                    headers.setdefault(
                        "Strict-Transport-Security", "max-age=15552000; includeSubDomains"
                    )
            await send(message)

        await self.app(scope, receive, send_with_headers)
