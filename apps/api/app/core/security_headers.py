"""安全响应头(纯 ASGI,避开 BaseHTTPMiddleware 的流式响应问题)。

API 只出 JSON:CSP default-src 'none' + 禁嵌入;/docs /redoc 需要加载 swagger 资源,
仅对其豁免 CSP。HSTS 只在 prod 下发(需 TLS 终端在前)。
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings

_DOCS_PATHS = ("/docs", "/redoc")


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
                if not path.startswith(_DOCS_PATHS):
                    headers.setdefault(
                        "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
                    )
                if get_settings().environment == "prod":
                    headers.setdefault(
                        "Strict-Transport-Security", "max-age=15552000; includeSubDomains"
                    )
            await send(message)

        await self.app(scope, receive, send_with_headers)
