from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, Response, status

from app.core.audit import set_audit_target
from app.core.db import DbSession
from app.core.errors import AppError, ErrorCode
from app.core.http import mark_idempotent_replay
from app.core.pagination import Page
from app.core.platform_config import get_effective_platform_config
from app.modules.account.deps import CurrentUser
from app.modules.billing.schemas import SubscriptionQuoteOut
from app.modules.orchestrator import service
from app.modules.orchestrator.schemas import (
    ApiKeyCreate,
    ApiKeyCreateOut,
    ApiKeyOut,
    InstanceAccessOut,
    InstanceAutoRenew,
    InstanceCreate,
    InstanceEventOut,
    InstanceLogsOut,
    InstanceOut,
    InstanceRename,
    InstanceRenew,
    RenewOut,
    ServiceEndpointOut,
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
        market=body.market,
        period=body.period,
        period_count=body.period_count,
        workload_type=body.workload_type,
        container_command=body.container_command,
        container_args=body.container_args,
        env=body.env,
        env_secret_keys=body.env_secret_keys,
        service_port=body.service_port,
        health_path=body.health_path,
        require_api_key=body.require_api_key,
        with_ssh=body.with_ssh,
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
    cursor: str | None = None,
    limit: int | None = Query(default=None, le=100),
) -> Page[InstanceOut]:
    """实例列表:降序游标分页;status 精确过滤,name 模糊匹配(含 uuid 前缀)。"""
    return await service.list_instances_page(
        session, user.id, status=status, name=name, cursor=cursor, limit=limit
    )


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
    instance = await service.stop_instance(session, user.id, uuid)
    set_audit_target(request, f"instance:{uuid}")
    return await service.instance_view(session, instance)


@router.post("/instances/{uuid}/start")
async def start_instance(
    uuid: str, user: CurrentUser, session: DbSession, request: Request
) -> InstanceOut:
    instance = await service.start_instance(session, user.id, uuid)
    set_audit_target(request, f"instance:{uuid}")
    return await service.instance_view(session, instance)


@router.post("/instances/{uuid}/restart")
async def restart_instance(
    uuid: str, user: CurrentUser, session: DbSession, request: Request
) -> InstanceOut:
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
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RenewOut:
    """包周期续费:按新周期的折扣重新报价并即时扣款(不足即 402/400,不进欠费)。

    挂在 instances 下而不是 billing 下:用户的心智是「给这台机器续费」,
    而实例状态(冻结中续费即解冻)也只能由 orchestrator 这一侧改。
    """
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
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RenewOut:
    """按量转包周期:结清转换前的按量账,再按周期折扣一次性预扣。

    与 `/renew` 同一个入参与响应形态(都是「给这台机器买一段周期」),区别只在起点:
    这里从现在起算,续费从老周期到期时刻接上。
    """
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
    """竞价实例转按量(免被回收)。已经是按量则原样返回,重试不报错。

    当前整点小时会整体改按按量价结算(一小时一价,以结算时的实例单价为准),
    这一条必须在确认弹窗里写清楚。
    """
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
    """释放实例(清除实例盘,数据盘不受影响)。前端多级确认后调用。"""
    instance = await service.release_instance(session, user.id, uuid)
    set_audit_target(request, f"instance:{uuid}")
    return await service.instance_view(session, instance)


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
    """接入信息。字段按形态出现:dev 给 SSH + Jupyter,service 给端点 URL(开了 SSH 就都有)。"""
    return InstanceAccessOut.model_validate(await service.get_access(session, user.id, uuid))


@router.get("/instances/{uuid}/service")
async def get_service_endpoint(
    uuid: str, user: CurrentUser, session: DbSession
) -> ServiceEndpointOut:
    """服务端点(仅服务型实例;dev 实例 404)。"""
    return await service.service_endpoint_view(session, user.id, uuid)


@router.get("/instances/{uuid}/api-keys")
async def list_api_keys(uuid: str, user: CurrentUser, session: DbSession) -> list[ApiKeyOut]:
    """访问密钥列表(含已吊销)。不含明文——库里就没有明文。"""
    rows = await service.list_api_keys(session, user.id, uuid)
    return [ApiKeyOut.model_validate(r) for r in rows]


@router.post("/instances/{uuid}/api-keys", status_code=status.HTTP_201_CREATED)
async def create_api_key(
    uuid: str, body: ApiKeyCreate, user: CurrentUser, session: DbSession, request: Request
) -> ApiKeyCreateOut:
    """新建访问密钥。响应里的 key 是明文,且只在这一次出现。

    不收 Idempotency-Key:重放要回同一份明文就得把明文留在库里,与「只存摘要」冲突。
    """
    row, plaintext = await service.create_api_key(session, user.id, uuid, name=body.name)
    # 审计只落 id 与名字,明文绝不进 detail
    set_audit_target(request, f"instance:{uuid}", {"api_key_id": row.id, "name": row.name})
    return ApiKeyCreateOut(**ApiKeyOut.model_validate(row).model_dump(), key=plaintext)


@router.delete("/instances/{uuid}/api-keys/{key_id}")
async def revoke_api_key(
    uuid: str, key_id: int, user: CurrentUser, session: DbSession, request: Request
) -> ApiKeyOut:
    """吊销访问密钥(写 revoked_at,不删行)。重复吊销幂等。"""
    row = await service.revoke_api_key(session, user.id, uuid, key_id)
    set_audit_target(request, f"instance:{uuid}", {"api_key_id": key_id})
    return ApiKeyOut.model_validate(row)


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
    return await service.instance_view(session, instance)
