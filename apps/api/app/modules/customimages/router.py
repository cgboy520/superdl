from typing import Annotated

from fastapi import APIRouter, Header, Request, Response, status

from app.core.audit import set_audit_target
from app.core.db import DbSession
from app.core.http import mark_idempotent_replay
from app.core.params import IdempotencyKey
from app.core.ratelimit import check_rate_limit
from app.modules.account import service as account_service
from app.modules.account.deps import CurrentUser
from app.modules.customimages import service
from app.modules.customimages.schemas import (
    ChunkAck,
    CustomImageCreate,
    CustomImageOut,
    CustomImageUpdate,
)

router = APIRouter(prefix="/custom-images", tags=["custom-images"])

CHUNK_PATH = "/custom-images/{uuid}/chunks/{index}"
CHUNKS_PER_HOUR = 12000


@router.get("")
async def list_images(user: CurrentUser, session: DbSession) -> list[CustomImageOut]:
    """The caller's uploads (any state) and the ready images members of the caller's tenant
    share."""
    return await service.list_images(session, user.id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def start_upload(
    body: CustomImageCreate,
    user: CurrentUser,
    session: DbSession,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> CustomImageOut:
    """Open a 24-hour upload of a `docker save` archive (.tar, .tar.gz / .tgz, .zip): name:tag is
    reserved, the archive size counts against the quota. Send the chunks (chunk_bytes each, the
    last one shorter), then complete."""
    await account_service.require_real_name_if_required(
        session, user, key="customImages.realNameRequired"
    )
    await check_rate_limit(f"custom-image-create:{user.id}", max_attempts=30, window_seconds=3600.0)
    image, created = await service.create_upload(session, user.id, body, idempotency_key)
    if not created:
        mark_idempotent_replay(response)
    set_audit_target(
        request,
        f"custom-image:{image.uuid}",
        detail={"name": body.name, "tag": body.tag, "size_bytes": body.size_bytes},
    )
    return service.to_out(image, owned=True)


@router.get("/{uuid}")
async def get_image(uuid: str, user: CurrentUser, session: DbSession) -> CustomImageOut:
    """One of the caller's images; while uploading, received_bitmap tells which chunks arrived."""
    return await service.get_image(session, user.id, uuid)


@router.put(
    "/{uuid}/chunks/{index}",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def put_chunk(
    uuid: str,
    index: int,
    request: Request,
    user: CurrentUser,
    session: DbSession,
    x_chunk_sha256: Annotated[str, Header(pattern=r"^[0-9a-fA-F]{64}$")],
) -> ChunkAck:
    """Chunk `index` (0-based) as the raw body, its sha256 in X-Chunk-Sha256. Resending a chunk
    replaces it; chunk 0 must match the sha256 given at start."""
    await check_rate_limit(
        f"custom-image-chunk:{user.id}", max_attempts=CHUNKS_PER_HOUR, window_seconds=3600.0
    )
    data = await request.body()
    return await service.put_chunk(session, user.id, uuid, index, data, x_chunk_sha256)


@router.post("/{uuid}/complete")
async def complete_upload(
    uuid: str, user: CurrentUser, session: DbSession, request: Request
) -> CustomImageOut:
    """Every chunk arrived: the import starts (progress_bytes advances, then ready or failed with
    error_key)."""
    image = await service.complete_upload(session, user.id, uuid)
    set_audit_target(request, f"custom-image:{uuid}", detail={"action": "complete"})
    return service.to_out(image, owned=True)


@router.patch("/{uuid}")
async def update_image(
    uuid: str, body: CustomImageUpdate, user: CurrentUser, session: DbSession, request: Request
) -> CustomImageOut:
    """Description and sharing with the caller's tenant."""
    out = await service.update_image(session, user.id, uuid, body)
    set_audit_target(request, f"custom-image:{uuid}", detail=body.model_dump(exclude_none=True))
    return out


@router.delete("/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_image(
    uuid: str, user: CurrentUser, session: DbSession, request: Request
) -> Response:
    """Uploads and failed imports go at once; a ready image only while no unreleased instance uses
    it; an import in progress must finish first."""
    await service.delete_image(session, user.id, uuid)
    set_audit_target(request, f"custom-image:{uuid}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
