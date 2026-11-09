from datetime import datetime

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field

from app.core.audit import set_audit_target
from app.core.db import DbSession
from app.core.http import mark_idempotent_replay
from app.core.money import MoneyOut
from app.core.params import IdempotencyKey
from app.core.ratelimit import check_rate_limit
from app.modules.account import service as account_service
from app.modules.account.deps import CurrentUser
from app.modules.orchestrator import disks as service
from app.modules.orchestrator.disks import DEFAULT_MOUNT_PATH
from app.modules.orchestrator.models import DataDisk

router = APIRouter(tags=["disks"])


_MOUNT_PATH_DOC = (
    "Mount point inside every instance of the user: /data, /data<suffix>, /workspace<suffix> or"
    " /mnt/<name> (not /mnt/org)"
)


class DiskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    size_gb: int = Field(ge=1)
    mount_path: str = Field(
        default=DEFAULT_MOUNT_PATH, min_length=2, max_length=64, description=_MOUNT_PATH_DOC
    )


class DiskUpdate(BaseModel):
    """Grow the disk and / or move its mount point (applies from each instance's next start)."""

    size_gb: int | None = Field(default=None, ge=1)
    mount_path: str | None = Field(
        default=None, min_length=2, max_length=64, description=_MOUNT_PATH_DOC
    )


class DiskUserOut(BaseModel):
    """An instance whose Pod mounts the disk."""

    uuid: str
    name: str
    status: str


class DiskOut(BaseModel):
    id: int
    uuid: str
    name: str
    size_gb: int
    mount_path: str
    status: str
    price_gb_month: MoneyOut
    grace_started_at: datetime | None
    frozen_started_at: datetime | None
    provisioned: bool
    bytes_used: int | None = Field(
        default=None, description="Bytes stored on the disk, refreshed by the storage patrol"
    )
    usage_checked_at: datetime | None = None
    mounted_by: list[DiskUserOut] = Field(
        default_factory=list, description="Instances whose Pod currently mounts the disk"
    )
    created_at: datetime

    model_config = {"from_attributes": True}


async def _outs(session: DbSession, user_id: int, disks: list[DataDisk]) -> list[DiskOut]:
    users = await service.disk_users(session, user_id, [d.id for d in disks])
    out: list[DiskOut] = []
    for d in disks:
        item = DiskOut.model_validate(d)
        item.mounted_by = [
            DiskUserOut(uuid=i.uuid, name=i.name, status=i.status) for i in users.get(d.id, [])
        ]
        out.append(item)
    return out


@router.post("/disks", status_code=status.HTTP_201_CREATED)
async def create_disk(
    body: DiskCreate,
    user: CurrentUser,
    session: DbSession,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> DiskOut:
    await account_service.require_real_name_if_required(session, user, key="disks.realNameRequired")
    await check_rate_limit(f"disk-create:{user.id}", max_attempts=20, window_seconds=3600.0)
    disk, created = await service.create_disk(
        session, user.id, body.name, body.size_gb, idempotency_key, mount_path=body.mount_path
    )
    if not created:
        mark_idempotent_replay(response)
    set_audit_target(request, f"disk:{disk.uuid}")
    return (await _outs(session, user.id, [disk]))[0]


@router.get("/disks")
async def list_disks(user: CurrentUser, session: DbSession) -> list[DiskOut]:
    """Every disk of the user; each is mounted on all of the user's instances at its mount point."""
    return await _outs(session, user.id, await service.list_disks(session, user.id))


@router.patch("/disks/{uuid}")
async def update_disk(
    uuid: str, body: DiskUpdate, user: CurrentUser, session: DbSession, request: Request
) -> DiskOut:
    """Grow (never shrink) and / or move the mount point."""
    await check_rate_limit(f"disk-expand:{user.id}", max_attempts=20, window_seconds=3600.0)
    disk = await service.update_disk(
        session, user.id, uuid, size_gb=body.size_gb, mount_path=body.mount_path
    )
    set_audit_target(
        request,
        f"disk:{uuid}",
        detail=body.model_dump(exclude_none=True),
    )
    return (await _outs(session, user.id, [disk]))[0]


@router.delete("/disks/{uuid}")
async def delete_disk(
    uuid: str, user: CurrentUser, session: DbSession, request: Request
) -> DiskOut:
    """Delete the data disk (irreversible); forbidden while an instance's Pod mounts it."""
    disk = await service.delete_disk(session, user.id, uuid)
    set_audit_target(request, f"disk:{uuid}")
    return (await _outs(session, user.id, [disk]))[0]
