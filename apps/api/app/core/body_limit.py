"""请求体硬上限(纯 ASGI;外层另有 Envoy requestBuffer,见 04-gateway.yaml):Content-Length 超限直接
413;否则流式计数缓冲,超限 413 短路。"""

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import payload_too_large_response

# 匿名 webhook 在边缘另有更严的 256Ki(04-gateway.yaml)
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
                declared = -1  # 非法 Content-Length:与超限同等对待,不进读流
            if declared < 0 or declared > self.max_bytes:
                await payload_too_large_response()(scope, receive, send)
                return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":  # http.disconnect 等:中止,不交路由
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
            return await receive()  # 流已收全,之后只剩 http.disconnect

        await self.app(scope, replay, send)
