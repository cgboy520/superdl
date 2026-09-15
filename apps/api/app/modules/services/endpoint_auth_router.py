"""Envoy extAuth 内部回调,须由 edge_guard 限制访问并由网关限流。

成功响应必须显式覆盖端点与密钥归属头,匿名端点也须返回 key-id。
"""

from typing import Annotated

from fastapi import APIRouter, Header, Response

from app.core.db import DbSession
from app.modules.services import service

router = APIRouter(tags=["endpoint-auth"], include_in_schema=False)

_EXT_AUTH_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


@router.api_route("/endpoint-auth", methods=_EXT_AUTH_METHODS)
async def authorize_endpoint(
    session: DbSession,
    host: Annotated[str | None, Header()] = None,
    x_forwarded_host: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> Response:
    """校验一次端点访问:通过回 200 + 归属头,不通过抛 401。
    slug 从 Host 取(可带端口),x-forwarded-host 只在 Host 不含 slug 时兜底。
    """
    slug = service.endpoint_slug_from_host(host) or service.endpoint_slug_from_host(
        x_forwarded_host
    )
    key = x_api_key
    if not key and authorization and authorization.lower().startswith("bearer "):
        key = authorization[7:].strip()
    result = await service.verify_endpoint_key(session, slug=slug, key=key)
    return Response(
        status_code=200,
        headers={
            "x-superdl-endpoint": result.slug,
            "x-superdl-key-id": str(result.key_id) if result.key_id is not None else "anonymous",
        },
    )
