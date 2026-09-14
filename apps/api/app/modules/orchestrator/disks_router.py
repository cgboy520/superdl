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

router = APIRouter(tags=["disks"])


class DiskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    size_gb: int = Field(ge=1)


class DiskExpand(BaseModel):
    size_gb: int = Field(ge=1)


class DiskOut(BaseModel):
    id: int
    uuid: str
    name: str
    size_gb: int
    status: str
    price_gb_month: MoneyOut
    mounted_instance_id: int | None
    grace_started_at: datetime | None
    frozen_started_at: datetime | None
    # False = 目录硬配额未生效
    provisioned: bool
    created_at: datetime

    model_config = {"from_attributes": True}


@router.post("/disks", status_code=status.HTTP_201_CREATED)
async def create_disk(
    body: DiskCreate,
    user: CurrentUser,
    session: DbSession,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> DiskOut:
    # 实名闸门(与开通算力同一开关)
    await account_service.require_real_name_if_required(session, user, key="disks.realNameRequired")
    # 资源创建按用户限流
    await check_rate_limit(f"disk-create:{user.id}", max_attempts=20, window_seconds=3600.0)
    disk, created = await service.create_disk(
        session, user.id, body.name, body.size_gb, idempotency_key
    )
    if not created:
        mark_idempotent_replay(response)
    set_audit_target(request, f"disk:{disk.uuid}")
    return DiskOut.model_validate(disk)


@router.get("/disks")
async def list_disks(user: CurrentUser, session: DbSession) -> list[DiskOut]:
    return [DiskOut.model_validate(d) for d in await service.list_disks(session, user.id)]


@router.patch("/disks/{uuid}")
async def expand_disk(
    uuid: str, body: DiskExpand, user: CurrentUser, session: DbSession, request: Request
) -> DiskOut:
    """扩容(只增不减)。"""
    await check_rate_limit(f"disk-expand:{user.id}", max_attempts=20, window_seconds=3600.0)
    disk = await service.expand_disk(session, user.id, uuid, body.size_gb)
    set_audit_target(request, f"disk:{uuid}", detail={"size_gb": body.size_gb})
    return DiskOut.model_validate(disk)


@router.delete("/disks/{uuid}")
async def delete_disk(
    uuid: str, user: CurrentUser, session: DbSession, request: Request
) -> DiskOut:
    """删除数据盘(不可恢复);挂载中禁止。"""
    disk = await service.delete_disk(session, user.id, uuid)
    set_audit_target(request, f"disk:{uuid}")
    return DiskOut.model_validate(disk)
