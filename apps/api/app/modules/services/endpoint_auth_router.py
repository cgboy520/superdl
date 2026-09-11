"""网关鉴权回调:Envoy `SecurityPolicy.extAuth` 对 `svc-https` listener 的每个请求同步回调,
2xx 放行、401 拒绝。本端点不带鉴权,靠 core/edge_guard 边缘收口;不过审计中间件、不过通用限流
(限流在网关 BackendTrafficPolicy);精确路径,网关侧用 `extAuth.http.pathOverride`。
"""

from typing import Annotated

from fastapi import APIRouter, Header, Response

from app.core.db import DbSession
from app.modules.services import service

router = APIRouter(tags=["endpoint-auth"], include_in_schema=False)

# ext_authz 用客户端原始请求的方法回调,全方法接住
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
            # headersToBackend 是覆盖语义:匿名放行也必须显式回 key-id
            "x-superdl-endpoint": result.slug,
            "x-superdl-key-id": str(result.key_id) if result.key_id is not None else "anonymous",
        },
    )
