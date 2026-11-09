"""Custom images: chunked uploads of Docker archives, import into the registry's user project,
sharing inside a tenant and the instance-creation check. The request path writes chunks to the
upload volume and the database only; the import, the registry and the deletions run on the
image-ops worker through the outbox."""

import asyncio
import base64
import hashlib
import math
from uuid import uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode, conflict, not_found
from app.core.idempotency import (
    IDEMPOTENCY_WINDOW,
    find_replay,
    insert_idempotent,
    request_fingerprint,
)
from app.core.logging import get_logger
from app.core.outbox import enqueue
from app.core.platform_config import RuntimeConfig, get_runtime_config
from app.core.timeutil import ensure_utc, now_utc
from app.modules.customimages import staging
from app.modules.customimages.models import (
    STATUS_DELETING,
    STATUS_FAILED,
    STATUS_IMPORTING,
    STATUS_READY,
    STATUS_UPLOADING,
    CustomImage,
)
from app.modules.customimages.schemas import (
    ChunkAck,
    CustomImageCreate,
    CustomImageOut,
    CustomImageUpdate,
    archive_format_of,
)
from app.modules.orchestrator import queries as orchestrator_queries
from app.modules.orgs import service as orgs_service

logger = get_logger(__name__)

GIB = 1024**3
IMPORT_TASK = "image.import"
DELETE_TASK = "image.delete"
_LIVE = (STATUS_UPLOADING, STATUS_IMPORTING, STATUS_READY, STATUS_FAILED)


def repository_of(cfg: RuntimeConfig, image: CustomImage) -> str:
    """Repository inside the registry: <user project>/u<user id>/<name>."""
    return f"{cfg.registry_user_project}/u{image.user_id}/{image.name}"


def user_project_prefix(cfg: RuntimeConfig) -> str | None:
    host = cfg.registry_host.strip().rstrip("/")
    return f"{host}/{cfg.registry_user_project}/" if host else None


def _bit_count(bitmap: bytes) -> int:
    return sum(b.bit_count() for b in bitmap)


def to_out(image: CustomImage, *, owned: bool, owner_label: str | None = None) -> CustomImageOut:
    uploading = image.status == STATUS_UPLOADING and owned
    return CustomImageOut.model_validate(
        {
            "uuid": image.uuid,
            "name": image.name,
            "tag": image.tag,
            "description": image.description,
            "status": image.status,
            "owned": owned,
            "owner_label": owner_label,
            "shared_with_org": image.shared_with_org,
            "filename": image.filename,
            "archive_format": image.archive_format,
            "upload_bytes": image.upload_bytes,
            "chunk_bytes": image.chunk_bytes,
            "chunks_total": image.chunks_total,
            "chunks_received": _bit_count(image.received_bitmap),
            "received_bitmap": (
                base64.b64encode(image.received_bitmap).decode() if uploading else None
            ),
            "upload_expires_at": image.upload_expires_at,
            "progress_bytes": image.progress_bytes,
            "error_key": image.error_key,
            "error_detail": image.error_detail,
            "image_ref": image.image_ref,
            "size_bytes": image.size_bytes,
            "unpacked_bytes": image.unpacked_bytes,
            "architecture": image.architecture,
            "os": image.os,
            "entrypoint": image.entrypoint,
            "cmd": image.cmd,
            "exposed_ports": image.exposed_ports,
            "dev_ready": image.dev_ready,
            "layers": image.layers,
            "created_at": image.created_at,
            "updated_at": image.updated_at,
        }
    )


async def _usage(session: AsyncSession, user_id: int) -> tuple[int, int]:
    """(images counted against the quota, bytes: registry size when ready, archive size while
    uploading or importing)."""
    row = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(
                    func.sum(func.coalesce(CustomImage.size_bytes, CustomImage.upload_bytes)),
                    0,
                ),
            ).where(CustomImage.user_id == user_id, CustomImage.status.in_(_LIVE))
        )
    ).one()
    return int(row[0]), int(row[1])


async def _check_quota(session: AsyncSession, cfg: RuntimeConfig, user_id: int, size: int) -> None:
    if size > cfg.custom_image_max_upload_gb * GIB:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="customImages.uploadTooLarge",
            params={"max": cfg.custom_image_max_upload_gb},
        )
    count, used = await _usage(session, user_id)
    if count >= cfg.max_custom_images_per_user:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="customImages.countQuota",
            params={"max": cfg.max_custom_images_per_user},
        )
    if used + size > cfg.max_custom_image_gb_per_user * GIB:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="customImages.storageQuota",
            params={"max": cfg.max_custom_image_gb_per_user},
        )


async def create_upload(
    session: AsyncSession, user_id: int, body: CustomImageCreate, idempotency_key: str | None
) -> tuple[CustomImage, bool]:
    """Open an upload session (24 h): the row reserves name:tag and the quota until the import
    finishes or the upload expires."""
    fingerprint = request_fingerprint(
        user_id, body.name, body.tag, body.filename, body.size_bytes, body.first_chunk_sha256
    )
    if idempotency_key:
        existing = await find_replay(
            session,
            CustomImage,
            owner_col=CustomImage.user_id,
            owner_id=user_id,
            key=idempotency_key,
            window=IDEMPOTENCY_WINDOW,
            fingerprint=fingerprint,
        )
        if existing is not None:
            return existing, False
    archive_format = archive_format_of(body.filename)
    if archive_format is None:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="customImages.formatUnsupported")
    cfg = await get_runtime_config(session)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"custom-images:{user_id}"}
    )
    if await session.scalar(
        select(CustomImage.id).where(
            CustomImage.user_id == user_id,
            CustomImage.name == body.name,
            CustomImage.tag == body.tag,
        )
    ):
        raise conflict(key="customImages.nameTagTaken", params={"ref": f"{body.name}:{body.tag}"})
    await _check_quota(session, cfg, user_id, body.size_bytes)
    chunks = math.ceil(body.size_bytes / staging.CHUNK_BYTES)
    image = CustomImage(
        uuid=uuid4().hex,
        user_id=user_id,
        name=body.name,
        tag=body.tag,
        description=body.description,
        shared_with_org=body.shared_with_org,
        status=STATUS_UPLOADING,
        filename=body.filename,
        archive_format=archive_format,
        upload_bytes=body.size_bytes,
        chunk_bytes=staging.CHUNK_BYTES,
        chunks_total=chunks,
        received_bitmap=bytes(math.ceil(chunks / 8)),
        first_chunk_sha256=body.first_chunk_sha256,
        upload_expires_at=now_utc() + staging.UPLOAD_TTL,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
    )
    result = await insert_idempotent(
        session,
        image,
        model=CustomImage,
        owner_col=CustomImage.user_id,
        owner_id=user_id,
        key=idempotency_key,
        window=IDEMPOTENCY_WINDOW,
        fingerprint=fingerprint,
    )
    if result is not image:
        return result, False
    await session.commit()
    await session.refresh(image)
    logger.info("custom_image_upload_started", image_uuid=image.uuid, size=body.size_bytes)
    return image, True


async def _owned(session: AsyncSession, user_id: int, uuid: str) -> CustomImage:
    image = await session.scalar(
        select(CustomImage).where(CustomImage.uuid == uuid, CustomImage.user_id == user_id)
    )
    if image is None:
        raise not_found(key="customImages.notFound")
    return image


def _require_open(image: CustomImage) -> None:
    if image.status != STATUS_UPLOADING or ensure_utc(image.upload_expires_at) <= now_utc():
        raise conflict(key="customImages.uploadClosed")


async def put_chunk(
    session: AsyncSession, user_id: int, uuid: str, index: int, data: bytes, sha256_hex: str
) -> ChunkAck:
    """Store one chunk (size and sha256 must match), then set its bit; a resent chunk simply
    overwrites the same file."""
    image = await _owned(session, user_id, uuid)
    _require_open(image)
    if not 0 <= index < image.chunks_total:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="customImages.chunkIndex")
    expected = staging.expected_chunk_size(index, image.chunk_bytes, image.upload_bytes)
    digest = hashlib.sha256(data).hexdigest()
    if len(data) != expected or digest != sha256_hex.lower():
        raise AppError(ErrorCode.VALIDATION_ERROR, key="customImages.chunkMismatch")
    if index == 0 and digest != image.first_chunk_sha256:
        raise conflict(key="customImages.differentFile")
    await asyncio.to_thread(staging.write_chunk, staging.staging_dir(user_id, uuid), index, data)
    row = await session.execute(
        update(CustomImage)
        .where(CustomImage.id == image.id, CustomImage.status == STATUS_UPLOADING)
        .values(received_bitmap=func.set_bit(CustomImage.received_bitmap, index, 1))
        .returning(CustomImage.received_bitmap)
    )
    bitmap = row.scalar_one_or_none()
    await session.commit()
    if bitmap is None:
        raise conflict(key="customImages.uploadClosed")
    return ChunkAck(chunks_received=_bit_count(bitmap), chunks_total=image.chunks_total)


async def complete_upload(session: AsyncSession, user_id: int, uuid: str) -> CustomImage:
    """Every chunk is in: hand the archive to the importer (image-ops)."""
    image = await _owned(session, user_id, uuid)
    if image.status == STATUS_IMPORTING:
        return image
    _require_open(image)
    if _bit_count(image.received_bitmap) != image.chunks_total:
        raise conflict(key="customImages.chunksMissing")
    image.status = STATUS_IMPORTING
    image.progress_bytes = 0
    enqueue(session, IMPORT_TASK, {"image_id": image.id})
    await session.commit()
    await session.refresh(image)
    return image


async def _shared_with(session: AsyncSession, user_id: int) -> list[tuple[CustomImage, str]]:
    """Ready images other members of the caller's tenant share, with their owner's label."""
    member = await orgs_service.membership_of(session, user_id)
    if member is None:
        return []
    member_ids = (await orgs_service.active_member_ids(session, [member.org_id]))[member.org_id]
    others = member_ids - {user_id}
    if not others:
        return []
    rows = (
        await session.scalars(
            select(CustomImage)
            .where(
                CustomImage.user_id.in_(others),
                CustomImage.shared_with_org.is_(True),
                CustomImage.status == STATUS_READY,
            )
            .order_by(CustomImage.id.desc())
        )
    ).all()
    labels = await orgs_service.member_labels(session, member.org_id, others)
    return [(r, labels[r.user_id].label if r.user_id in labels else "") for r in rows]


async def list_images(session: AsyncSession, user_id: int) -> list[CustomImageOut]:
    own = (
        await session.scalars(
            select(CustomImage)
            .where(CustomImage.user_id == user_id)
            .order_by(CustomImage.id.desc())
        )
    ).all()
    out = [to_out(i, owned=True) for i in own]
    out += [
        to_out(i, owned=False, owner_label=label)
        for i, label in await _shared_with(session, user_id)
    ]
    return out


async def get_image(session: AsyncSession, user_id: int, uuid: str) -> CustomImageOut:
    return to_out(await _owned(session, user_id, uuid), owned=True)


async def update_image(
    session: AsyncSession, user_id: int, uuid: str, body: CustomImageUpdate
) -> CustomImageOut:
    image = await _owned(session, user_id, uuid)
    if "description" in body.model_fields_set:
        image.description = body.description or None
    if body.shared_with_org is not None:
        image.shared_with_org = body.shared_with_org
    await session.commit()
    await session.refresh(image)
    return to_out(image, owned=True)


async def _drop_unimported(session: AsyncSession, image: CustomImage) -> None:
    await asyncio.to_thread(staging.remove_dir, staging.staging_dir(image.user_id, image.uuid))
    await session.delete(image)


async def delete_image(session: AsyncSession, user_id: int, uuid: str) -> None:
    """An upload or a failed import goes at once; a ready image only when no unreleased instance
    uses it (its registry artifact is deleted by image-ops); an import in progress must finish."""
    image = await _owned(session, user_id, uuid)
    await _delete(session, image, force=False)
    await session.commit()


async def _delete(session: AsyncSession, image: CustomImage, *, force: bool) -> None:
    if image.status in (STATUS_UPLOADING, STATUS_FAILED):
        await _drop_unimported(session, image)
        return
    if image.status == STATUS_IMPORTING:
        raise conflict(key="customImages.importing")
    if image.status == STATUS_DELETING:
        return
    if not force and image.image_ref:
        users = await orchestrator_queries.count_unreleased_by_image_ref(session, image.image_ref)
        if users:
            raise conflict(key="customImages.inUse", params={"count": users})
    image.status = STATUS_DELETING
    enqueue(session, DELETE_TASK, {"image_id": image.id})


async def require_usable(session: AsyncSession, user_id: int, image_ref: str) -> CustomImage:
    """Instance creation with an image of the registry's user project: it must be a ready image
    the caller uploaded, or one a member of the caller's tenant shares."""
    image = await session.scalar(
        select(CustomImage).where(
            CustomImage.image_ref == image_ref, CustomImage.status == STATUS_READY
        )
    )
    if image is not None and image.user_id == user_id:
        return image
    if image is not None and image.shared_with_org:
        mine = await orgs_service.membership_of(session, user_id)
        owner = await orgs_service.membership_of(session, image.user_id)
        if mine is not None and owner is not None and mine.org_id == owner.org_id:
            return image
    raise AppError(ErrorCode.VALIDATION_ERROR, key="customImages.notUsable")


async def admin_list(
    session: AsyncSession, *, user_id: int | None, status: str | None, limit: int = 200
) -> list[tuple[CustomImage, CustomImageOut]]:
    stmt = select(CustomImage).order_by(CustomImage.id.desc()).limit(limit)
    if user_id is not None:
        stmt = stmt.where(CustomImage.user_id == user_id)
    if status is not None:
        stmt = stmt.where(CustomImage.status == status)
    rows = (await session.scalars(stmt)).all()
    return [(r, to_out(r, owned=True)) for r in rows]


async def admin_delete(session: AsyncSession, uuid: str) -> None:
    """Platform removal (abuse): even while instances use it; their next start cannot pull it."""
    image = await session.scalar(select(CustomImage).where(CustomImage.uuid == uuid))
    if image is None:
        raise not_found(key="customImages.notFound")
    await _delete(session, image, force=True)
    await session.commit()
