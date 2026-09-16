"""Internal Envoy extAuth callback; access must be restricted by edge_guard and rate-limited by the
gateway.

A success response must explicitly override the endpoint and key ownership headers; anonymous
endpoints return a key-id as well.
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
    """Check one endpoint access: pass → 200 + ownership headers, fail → 401.
    The slug comes from Host (port allowed); x-forwarded-host is only the fallback when Host has no
    slug.
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
