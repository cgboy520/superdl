"""Buffer the HTTP request body; 413 when Content-Length is invalid or negative, or the declared /
actual size exceeds the cap. Chunk uploads of custom images are the one exception: a larger cap,
a mandatory Content-Length and no buffering (the body streams to the handler, which the declared
length bounds)."""

import re

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import payload_too_large_response

MAX_REQUEST_BODY_BYTES = 1024 * 1024
UPLOAD_CHUNK_MAX_BYTES = 8 * 1024 * 1024
_UPLOAD_CHUNK_PATH = re.compile(r"^/api/v1/custom-images/[0-9a-f]{32}/chunks/[0-9]{1,6}$")


def _declared_length(scope: Scope) -> int | None:
    raw = Headers(scope=scope).get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return -1


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int = MAX_REQUEST_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope.get("method") == "PUT" and _UPLOAD_CHUNK_PATH.match(scope.get("path", "")):
            await self._stream_chunk(scope, receive, send)
            return
        declared = _declared_length(scope)
        if declared is not None and not 0 <= declared <= self.max_bytes:
            await payload_too_large_response()(scope, receive, send)
            return
        buffered, disconnected = await self._buffer(receive)
        if disconnected:
            return
        if buffered is None:
            await payload_too_large_response()(scope, receive, send)
            return
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": buffered, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    async def _stream_chunk(self, scope: Scope, receive: Receive, send: Send) -> None:
        declared = _declared_length(scope)
        if declared is None or not 0 < declared <= UPLOAD_CHUNK_MAX_BYTES:
            await payload_too_large_response()(scope, receive, send)
            return
        await self.app(scope, receive, send)

    async def _buffer(self, receive: Receive) -> tuple[bytes | None, bool]:
        """(the whole body or None past the cap, whether the client disconnected first)."""
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return None, True
            body.extend(message.get("body", b""))
            if len(body) > self.max_bytes:
                return None, False
            if not message.get("more_body", False):
                return bytes(body), False
