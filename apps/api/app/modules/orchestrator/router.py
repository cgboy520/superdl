from typing import Annotated

from fastapi import APIRouter, Header, Request, status

from app.core.audit import set_audit_target
from app.core.db import DbSession
from app.modules.account.deps import CurrentUser
from app.modules.orchestrator import service
from app.modules.orchestrator.schemas import (
    InstanceAccessOut,
    InstanceCreate,
    InstanceEventOut,
    InstanceOut,
    InstanceRename,
)

router = APIRouter(tags=["instances"])


@router.post("/instances", status_code=status.HTTP_202_ACCEPTED)
async def create_instance(
    body: InstanceCreate,
    user: CurrentUser,
    session: DbSession,
    request: Request,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> InstanceOut:
    instance = await service.create_instance(
        session,
        user.id,
        sku_id=body.sku_id,
        gpu_count=body.gpu_count,
        image_ref=body.image_ref,
        ssh_key_ids=body.ssh_key_ids,
        name=body.name,
        data_disk_id=body.data_disk_id,
        idempotency_key=idempotency_key,
    )
    set_audit_target(request, f"instance:{instance.uuid}")
    return InstanceOut.model_validate(instance)


@router.get("/instances")
async def list_instances(user: CurrentUser, session: DbSession) -> list[InstanceOut]:
    instances = await service.list_instances(session, user.id)
    return [InstanceOut.model_validate(i) for i in instances]


@router.get("/instances/{uuid}")
async def get_instance(uuid: str, user: CurrentUser, session: DbSession) -> InstanceOut:
    return InstanceOut.model_validate(await service.get_instance(session, user.id, uuid))


@router.patch("/instances/{uuid}")
async def rename_instance(
    uuid: str, body: InstanceRename, user: CurrentUser, session: DbSession, request: Request
) -> InstanceOut:
    instance = await service.rename_instance(session, user.id, uuid, body.name)
    set_audit_target(request, f"instance:{uuid}")
    return InstanceOut.model_validate(instance)


@router.post("/instances/{uuid}/stop")
async def stop_instance(
    uuid: str, user: CurrentUser, session: DbSession, request: Request
) -> InstanceOut:
    instance = await service.stop_instance(session, user.id, uuid)
    set_audit_target(request, f"instance:{uuid}")
    return InstanceOut.model_validate(instance)


@router.post("/instances/{uuid}/start")
async def start_instance(
    uuid: str, user: CurrentUser, session: DbSession, request: Request
) -> InstanceOut:
    instance = await service.start_instance(session, user.id, uuid)
    set_audit_target(request, f"instance:{uuid}")
    return InstanceOut.model_validate(instance)


@router.post("/instances/{uuid}/restart")
async def restart_instance(
    uuid: str, user: CurrentUser, session: DbSession, request: Request
) -> InstanceOut:
    instance = await service.restart_instance(session, user.id, uuid)
    set_audit_target(request, f"instance:{uuid}")
    return InstanceOut.model_validate(instance)


@router.delete("/instances/{uuid}")
async def release_instance(
    uuid: str, user: CurrentUser, session: DbSession, request: Request
) -> InstanceOut:
    """释放实例(清除实例盘,数据盘不受影响)。前端多级确认后调用。"""
    instance = await service.release_instance(session, user.id, uuid)
    set_audit_target(request, f"instance:{uuid}")
    return InstanceOut.model_validate(instance)


@router.get("/instances/{uuid}/events")
async def list_instance_events(
    uuid: str, user: CurrentUser, session: DbSession
) -> list[InstanceEventOut]:
    """状态时间线(计费依据)。"""
    instance = await service.get_instance(session, user.id, uuid)
    events = await service.list_events(session, instance.id)
    return [InstanceEventOut.model_validate(e) for e in events]


@router.get("/instances/{uuid}/access")
async def get_instance_access(
    uuid: str, user: CurrentUser, session: DbSession
) -> InstanceAccessOut:
    instance = await service.get_instance(session, user.id, uuid)
    return InstanceAccessOut.model_validate(service.build_access(instance))


@router.post("/instances/{uuid}/reset-jupyter-token")
async def reset_jupyter_token(
    uuid: str, user: CurrentUser, session: DbSession, request: Request
) -> InstanceOut:
    instance = await service.reset_jupyter_token(session, user.id, uuid)
    set_audit_target(request, f"instance:{uuid}")
    return InstanceOut.model_validate(instance)
