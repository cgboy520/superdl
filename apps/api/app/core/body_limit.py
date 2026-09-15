"""缓冲 HTTP 请求体;Content-Length 非法、为负或声明/实际大小超限时返回 413。"""

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import payload_too_large_response

MAX_REQUEST_BODY_BYTES = 1024 * 1024


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int = MAX_REQUEST_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        content_length = Headers(scope=scope).get("content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                declared = -1
            if declared < 0 or declared > self.max_bytes:
                await payload_too_large_response()(scope, receive, send)
                return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.max_bytes:
                await payload_too_large_response()(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        buffered = bytes(body)
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": buffered, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
