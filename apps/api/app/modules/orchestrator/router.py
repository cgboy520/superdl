from fastapi import APIRouter, Query, Request, Response, status

from app.core.audit import set_audit_target
from app.core.db import DbSession
from app.core.http import mark_idempotent_replay
from app.core.pagination import Page
from app.core.params import Cursor, IdempotencyKey, Limit
from app.core.ratelimit import check_rate_limit
from app.modules.account import service as account_service
from app.modules.account.deps import CurrentUser
from app.modules.billing.schemas import SubscriptionQuoteOut
from app.modules.orchestrator import service
from app.modules.orchestrator.schemas import (
    InstanceAccessOut,
    InstanceAutoRenew,
    InstanceCreate,
    InstanceEventOut,
    InstanceLogsOut,
    InstanceOut,
    InstanceRename,
    InstanceRenew,
    RenewOut,
)

router = APIRouter(tags=["instances"])


@router.post("/instances", status_code=status.HTTP_202_ACCEPTED)
async def create_instance(
    body: InstanceCreate,
    user: CurrentUser,
    session: DbSession,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> InstanceOut:
    await account_service.require_real_name_if_required(
        session, user, key="orchestrator.realNameRequired"
    )
    await check_rate_limit(f"instance-create:{user.id}", max_attempts=30, window_seconds=3600.0)
    instance, created = await service.create_instance(
        session,
        user.id,
        service.InstanceRequest(
            sku_id=body.sku_id,
            gpu_count=body.gpu_count,
            image_ref=body.image_ref,
            ssh_key_ids=tuple(body.ssh_key_ids),
            name=body.name,
            data_disk_id=body.data_disk_id,
            market=body.market,
            period=body.period,
            period_count=body.period_count,
        ),
        idempotency_key=idempotency_key,
    )
    if not created:
        mark_idempotent_replay(response)
    set_audit_target(request, f"instance:{instance.uuid}")
    return await service.instance_view(session, instance)


@router.get("/instances")
async def list_instances(
    user: CurrentUser,
    session: DbSession,
    status: str | None = None,
    name: str | None = None,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
) -> Page[InstanceOut]:
    """实例列表(只列开发机;服务版本实例走 /services):降序游标分页,status 精确,
    name 模糊(含 uuid 前缀)。"""
    return await service.list_instances_page(
        session, user.id, status=status, name=name, cursor=cursor, limit=limit
    )


@router.get("/instances/expiring")
async def list_expiring_instances(
    user: CurrentUser,
    session: DbSession,
    within_days: int = Query(default=7, ge=1, le=90),
) -> list[InstanceOut]:
    """临期包周期实例:active 订阅且到期时刻 ≤ now+within_days,升序,不分页。"""
    return await service.list_expiring_instances(session, user.id, within_days=within_days)


@router.get("/instances/{uuid}")
async def get_instance(uuid: str, user: CurrentUser, session: DbSession) -> InstanceOut:
    return await service.instance_view(session, await service.get_instance(session, user.id, uuid))


@router.patch("/instances/{uuid}")
async def rename_instance(
    uuid: str, body: InstanceRename, user: CurrentUser, session: DbSession, request: Request
) -> InstanceOut:
    instance = await service.rename_instance(session, user.id, uuid, body.name)
    set_audit_target(request, f"instance:{uuid}")
    return await service.instance_view(session, instance)


@router.post("/instances/{uuid}/stop")
async def stop_instance(
    uuid: str, user: CurrentUser, session: DbSession, request: Request
) -> InstanceOut:
    await service.check_lifecycle_rate_limit(user.id)
    instance = await service.stop_instance(session, user.id, uuid)
    set_audit_target(request, f"instance:{uuid}")
    return await service.instance_view(session, instance)


@router.post("/instances/{uuid}/start")
async def start_instance(
    uuid: str, user: CurrentUser, session: DbSession, request: Request
) -> InstanceOut:
    await account_service.require_real_name_if_required(
        session, user, key="orchestrator.realNameRequired"
    )
    await service.check_lifecycle_rate_limit(user.id)
    instance = await service.start_instance(session, user.id, uuid)
    set_audit_target(request, f"instance:{uuid}")
    return await service.instance_view(session, instance)


@router.post("/instances/{uuid}/restart")
async def restart_instance(
    uuid: str, user: CurrentUser, session: DbSession, request: Request
) -> InstanceOut:
    await service.check_lifecycle_rate_limit(user.id)
    instance = await service.restart_instance(session, user.id, uuid)
    set_audit_target(request, f"instance:{uuid}")
    return await service.instance_view(session, instance)


@router.post("/instances/{uuid}/renew")
async def renew_instance(
    uuid: str,
    body: InstanceRenew,
    user: CurrentUser,
    session: DbSession,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> RenewOut:
    """包周期续费:按新周期折扣重新报价并即时扣款(不足即 402/400);冻结中续费即解冻回 stopped。"""
    await account_service.require_real_name_if_required(
        session, user, key="orchestrator.realNameRequired"
    )
    await check_rate_limit(f"instance-renew:{user.id}", max_attempts=20, window_seconds=3600.0)
    instance, quoted, created = await service.renew_instance(
        session,
        user.id,
        uuid,
        period=body.period,
        period_count=body.period_count,
        idempotency_key=idempotency_key,
    )
    if not created:
        mark_idempotent_replay(response)
    set_audit_target(request, f"instance:{uuid}")
    return RenewOut(
        instance=await service.instance_view(session, instance),
        quote=SubscriptionQuoteOut.model_validate(quoted, from_attributes=True),
    )


@router.post("/instances/{uuid}/subscribe")
async def subscribe_instance(
    uuid: str,
    body: InstanceRenew,
    user: CurrentUser,
    session: DbSession,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> RenewOut:
    """按量转包周期:结清转换前的按量账,再按周期折扣一次性预扣;入参与响应同 `/renew`,从现在起算。"""
    await account_service.require_real_name_if_required(
        session, user, key="orchestrator.realNameRequired"
    )
    await check_rate_limit(f"instance-subscribe:{user.id}", max_attempts=20, window_seconds=3600.0)
    instance, quoted, created = await service.subscribe_instance(
        session,
        user.id,
        uuid,
        period=body.period,
        period_count=body.period_count,
        idempotency_key=idempotency_key,
    )
    if not created:
        mark_idempotent_replay(response)
    set_audit_target(request, f"instance:{uuid}")
    return RenewOut(
        instance=await service.instance_view(session, instance),
        quote=SubscriptionQuoteOut.model_validate(quoted, from_attributes=True),
    )


@router.post("/instances/{uuid}/to-on-demand")
async def convert_to_on_demand(
    uuid: str, user: CurrentUser, session: DbSession, request: Request
) -> InstanceOut:
    """竞价实例转按量;已是按量则原样返回。当前整点小时整体改按按量价结算。"""
    instance = await service.convert_to_on_demand(session, user.id, uuid)
    set_audit_target(request, f"instance:{uuid}")
    return await service.instance_view(session, instance)


@router.post("/instances/{uuid}/auto-renew")
async def set_auto_renew(
    uuid: str, body: InstanceAutoRenew, user: CurrentUser, session: DbSession, request: Request
) -> InstanceOut:
    instance = await service.set_instance_auto_renew(session, user.id, uuid, enabled=body.enabled)
    set_audit_target(request, f"instance:{uuid}")
    return await service.instance_view(session, instance)


@router.delete("/instances/{uuid}")
async def release_instance(
    uuid: str, user: CurrentUser, session: DbSession, request: Request
) -> InstanceOut:
    """释放实例(清除实例盘,数据盘不受影响)。"""
    instance = await service.release_instance(session, user.id, uuid)
    set_audit_target(request, f"instance:{uuid}")
    return await service.instance_view(session, instance)


@router.get("/instances/{uuid}/events")
async def list_instance_events(
    uuid: str,
    user: CurrentUser,
    session: DbSession,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
) -> Page[InstanceEventOut]:
    """状态时间线(计费依据),降序游标分页。"""
    instance = await service.get_instance(session, user.id, uuid)
    return await service.list_events(session, instance.id, cursor=cursor, limit=limit)


@router.get("/instances/{uuid}/access")
async def get_instance_access(
    uuid: str, user: CurrentUser, session: DbSession
) -> InstanceAccessOut:
    """接入信息,字段按形态出现:dev 给 SSH + Jupyter,服务版本实例给端点 URL(开了 SSH 都有)。"""
    return await service.get_access(session, user.id, uuid)


@router.get("/instances/{uuid}/logs")
async def get_instance_logs(
    uuid: str,
    user: CurrentUser,
    session: DbSession,
    tail_lines: int = Query(default=200, ge=1),
) -> InstanceLogsOut:
    """容器日志(只读,不记审计):非属主 404;限流 20/h/user;仅 running/stopping,否则 409;
    tail_lines 默认 200,超 2000 截断。"""
    return await service.read_instance_logs(session, user.id, uuid, tail_lines=tail_lines)


@router.post("/instances/{uuid}/reset-jupyter-token")
async def reset_jupyter_token(
    uuid: str, user: CurrentUser, session: DbSession, request: Request
) -> InstanceOut:
    instance = await service.reset_jupyter_token(session, user.id, uuid)
    set_audit_target(request, f"instance:{uuid}")
    return await service.instance_view(session, instance)
