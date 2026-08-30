"""安全响应头(纯 ASGI,不走 BaseHTTPMiddleware)。

CSP `default-src 'none'` + 禁嵌入;/docs /redoc 豁免 CSP。HSTS 仅 prod 下发。
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
                # 浏览器特性面收窄(API 响应用不到硬件/支付 API)
                headers.setdefault(
                    "Permissions-Policy",
                    "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
                    "bluetooth=(), serial=(), accelerometer=(), gyroscope=(), magnetometer=()",
                )
                headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
                # same-site(非同源):console/admin 与 api 是同站兄弟子域,反代缺席时
                # 浏览器直连仍放行;跨站引用(第三方页面 fetch API)被丢弃
                headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
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
