"""请求体硬上限(纯 ASGI;内层防御,外层为 Envoy requestBuffer,见 04-gateway.yaml)。

- Content-Length 已超限:不读 body 直接 413(挡「声明巨大长度」的廉价攻击);
- 无/伪造 Content-Length:流式计数缓冲,超限即 413 短路,剩余流不再消费。

缓冲重放不改变行为:本应用没有任何请求方向流式端点(全仓无 request.stream()/
UploadFile),上限即单连接内存上限。不用 BaseHTTPMiddleware:它先把整个 body
读进内存再进路由,上限形同虚设。
"""

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import payload_too_large_response

# 平台 API 最大合法载荷是 SSH 公钥/平台配置文本(数 KB),1MiB 裕量已很宽;
# 收紧它先查真实流量分位数。匿名 webhook 在边缘另有更严的 256Ki(04-gateway.yaml)。
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
