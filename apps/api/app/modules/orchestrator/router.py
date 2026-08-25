from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, Response, status

from app.core.audit import set_audit_target
from app.core.db import DbSession
from app.core.errors import AppError, ErrorCode
from app.core.http import mark_idempotent_replay
from app.core.pagination import Page
from app.core.platform_config import get_effective_platform_config
from app.modules.account.deps import CurrentUser
from app.modules.orchestrator import service
from app.modules.orchestrator.schemas import (
    InstanceAccessOut,
    InstanceCreate,
    InstanceEventOut,
    InstanceLogsOut,
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
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> InstanceOut:
    # 与充值同一条强制实名开关:开启时算力开通同样拦截(监管对「算力服务」的要求
    # 不低于「预收款」);此前只拦充值,不开通算力的匿名账号可绕过
    cfg = await get_effective_platform_config(session)
    if cfg["real_name_required_for_recharge"] == "true" and user.verification_status != "verified":
        raise AppError(
            ErrorCode.REAL_NAME_REQUIRED,
            key="orchestrator.realNameRequired",
            http_status=403,
        )
    instance, created = await service.create_instance(
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
    if not created:
        mark_idempotent_replay(response)
    set_audit_target(request, f"instance:{instance.uuid}")
    return InstanceOut.model_validate(instance)


@router.get("/instances")
async def list_instances(
    user: CurrentUser,
    session: DbSession,
    status: str | None = None,
    name: str | None = None,
    cursor: str | None = None,
    limit: int | None = Query(default=None, le=100),
) -> Page[InstanceOut]:
    """实例列表:降序游标分页;status 精确过滤,name 模糊匹配(含 uuid 前缀)。"""
    return await service.list_instances_page(
        session, user.id, status=status, name=name, cursor=cursor, limit=limit
    )


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
    uuid: str,
    user: CurrentUser,
    session: DbSession,
    cursor: str | None = None,
    limit: int | None = Query(default=None, le=100),
) -> Page[InstanceEventOut]:
    """状态时间线(计费依据)。降序(最新在前)游标分页。"""
    instance = await service.get_instance(session, user.id, uuid)
    return await service.list_events(session, instance.id, cursor=cursor, limit=limit)


@router.get("/instances/{uuid}/access")
async def get_instance_access(
    uuid: str, user: CurrentUser, session: DbSession
) -> InstanceAccessOut:
    instance = await service.get_instance(session, user.id, uuid)
    return InstanceAccessOut.model_validate(service.build_access(instance))


@router.get("/instances/{uuid}/logs")
async def get_instance_logs(
    uuid: str,
    user: CurrentUser,
    session: DbSession,
    tail_lines: int = Query(default=200, ge=1),
    since_seconds: int | None = Query(default=None, ge=1),
) -> InstanceLogsOut:
    """容器日志。四要素:只读、owner 校验(非属主 404)、限流 20/h/user、K8s 读 5s 超时。

    仅 running/stopping 状态的实例可取(其余状态 409);tail_lines 默认 200、超 2000 按
    2000 截断;since_seconds 可选、超 86400 按 86400 截断。不记审计。"""
    return await service.read_instance_logs(
        session, user.id, uuid, tail_lines=tail_lines, since_seconds=since_seconds
    )


@router.post("/instances/{uuid}/reset-jupyter-token")
async def reset_jupyter_token(
    uuid: str, user: CurrentUser, session: DbSession, request: Request
) -> InstanceOut:
    instance = await service.reset_jupyter_token(session, user.id, uuid)
    set_audit_target(request, f"instance:{uuid}")
    return InstanceOut.model_validate(instance)
