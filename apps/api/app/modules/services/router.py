from fastapi import APIRouter, Query, Request, Response, status

from app.core.audit import set_audit_target
from app.core.db import DbSession
from app.core.http import mark_idempotent_replay
from app.core.pagination import Page
from app.core.params import Cursor, IdempotencyKey, Limit
from app.core.ratelimit import check_rate_limit
from app.modules.account import service as account_service
from app.modules.account.deps import CurrentUser
from app.modules.billing.schemas import BillHourlyOut
from app.modules.orchestrator.schemas import InstanceLogsOut, InstanceOut
from app.modules.services import service
from app.modules.services.schemas import (
    ApiKeyCreate,
    ApiKeyCreateOut,
    ApiKeyOut,
    ServiceCreate,
    ServiceEventOut,
    ServiceOut,
    ServicePatch,
    ServiceRevisionCreate,
)

router = APIRouter(tags=["services"])


@router.post("/services", status_code=status.HTTP_202_ACCEPTED)
async def create_service(
    body: ServiceCreate,
    user: CurrentUser,
    session: DbSession,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> ServiceOut:
    """部署服务:同事务落 services 行 + 第 1 版实例(creating)+ 事件 + outbox,202 异步。
    幂等键重放回 200 + X-Idempotent-Replay。"""
    # 实名闸门与资源创建限流:与创建实例同口径(服务的第一版就是一台实例)
    await account_service.require_real_name_if_required(
        session, user, key="orchestrator.realNameRequired"
    )
    await check_rate_limit(f"instance-create:{user.id}", max_attempts=30, window_seconds=3600.0)
    svc, created = await service.create_service(
        session, user.id, spec=body, idempotency_key=idempotency_key
    )
    if not created:
        mark_idempotent_replay(response)
    set_audit_target(request, f"service:{svc.public_slug}")
    return await service.service_view(session, svc)


@router.get("/services")
async def list_services(
    user: CurrentUser,
    session: DbSession,
    status: str | None = None,
    name: str | None = None,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
) -> Page[ServiceOut]:
    """服务列表:降序游标分页;name 模糊匹配(含 slug 前缀),status 按派生状态过滤;已删除的不列。"""
    return await service.list_services_page(
        session, user.id, status=status, name=name, cursor=cursor, limit=limit
    )


@router.get("/services/{slug}")
async def get_service(slug: str, user: CurrentUser, session: DbSession) -> ServiceOut:
    return await service.service_view(session, await service.get_service(session, user.id, slug))


@router.patch("/services/{slug}")
async def patch_service(
    slug: str, body: ServicePatch, user: CurrentUser, session: DbSession, request: Request
) -> ServiceOut:
    """改名 / 访问鉴权开关。开关只改网关回调的判定,几秒内生效,不重新部署。"""
    svc = await service.patch_service(
        session, user.id, slug, name=body.name, require_api_key=body.require_api_key
    )
    set_audit_target(request, f"service:{slug}")
    return await service.service_view(session, svc)


@router.post("/services/{slug}/stop")
async def stop_service(
    slug: str, user: CurrentUser, session: DbSession, request: Request
) -> ServiceOut:
    """停止:当前实例关机,端点随之 503;服务端点与密钥保留。"""
    svc = await service.stop_service(session, user.id, slug)
    set_audit_target(request, f"service:{slug}")
    return await service.service_view(session, svc)


@router.post("/services/{slug}/start")
async def start_service(
    slug: str, user: CurrentUser, session: DbSession, request: Request
) -> ServiceOut:
    await account_service.require_real_name_if_required(
        session, user, key="orchestrator.realNameRequired"
    )
    svc = await service.start_service(session, user.id, slug)
    set_audit_target(request, f"service:{slug}")
    return await service.service_view(session, svc)


@router.delete("/services/{slug}")
async def delete_service(
    slug: str, user: CurrentUser, session: DbSession, request: Request
) -> ServiceOut:
    """删除服务:释放当前实例并吊销全部密钥;运行中须先停止。数据盘不受影响。"""
    svc = await service.delete_service(session, user.id, slug)
    set_audit_target(request, f"service:{slug}")
    return await service.service_view(session, svc)


@router.post("/services/{slug}/revisions", status_code=status.HTTP_202_ACCEPTED)
async def create_revision(
    slug: str,
    body: ServiceRevisionCreate,
    user: CurrentUser,
    session: DbSession,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> ServiceOut:
    """版本更新(重建):新版本实例 creating,旧版本先关机;新版本就绪前端点返回 503;
    服务端点与 API Key 不变。包周期服务、更新在途、旧版本变更中一律 409。幂等键重放回 200。"""
    await account_service.require_real_name_if_required(
        session, user, key="orchestrator.realNameRequired"
    )
    await check_rate_limit(f"instance-create:{user.id}", max_attempts=30, window_seconds=3600.0)
    svc, created = await service.create_revision(
        session, user.id, slug, spec=body, idempotency_key=idempotency_key
    )
    if not created:
        mark_idempotent_replay(response)
    set_audit_target(request, f"service:{slug}", detail={"revision": svc.revision})
    return await service.service_view(session, svc)


@router.get("/services/{slug}/events")
async def list_service_events(
    slug: str,
    user: CurrentUser,
    session: DbSession,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
) -> Page[ServiceEventOut]:
    """状态时间线(计费依据):全部版本实例的事件并集,降序游标分页。"""
    svc = await service.get_service(session, user.id, slug)
    return await service.list_service_events(session, svc, cursor=cursor, limit=limit)


@router.get("/services/{slug}/revisions")
async def list_revisions(
    slug: str,
    user: CurrentUser,
    session: DbSession,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
) -> Page[InstanceOut]:
    """版本历史 = 该服务下全部实例(含已释放),按版本号降序。"""
    svc = await service.get_service(session, user.id, slug)
    return await service.list_revisions(session, user.id, svc, cursor=cursor, limit=limit)


@router.get("/services/{slug}/logs")
async def get_service_logs(
    slug: str,
    user: CurrentUser,
    session: DbSession,
    tail_lines: int = Query(default=200, ge=1),
) -> InstanceLogsOut:
    """当前版本的容器日志:只读、限流 20/h/user、K8s 读 5s 超时;非运行中 409。不记审计。"""
    return await service.read_service_logs(session, user.id, slug, tail_lines=tail_lines)


@router.get("/services/{slug}/bills")
async def list_service_bills(
    slug: str,
    user: CurrentUser,
    session: DbSession,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
) -> Page[BillHourlyOut]:
    """小时账单:该服务下全部版本实例的并集(账单主体仍是实例)。"""
    svc = await service.get_service(session, user.id, slug)
    return await service.service_bills_page(session, user.id, svc, cursor=cursor, limit=limit)


@router.get("/services/{slug}/api-keys")
async def list_api_keys(slug: str, user: CurrentUser, session: DbSession) -> list[ApiKeyOut]:
    """访问密钥列表(含已吊销)。不含明文——库里就没有明文。"""
    rows = await service.list_api_keys(session, user.id, slug)
    return [ApiKeyOut.model_validate(r) for r in rows]


@router.post("/services/{slug}/api-keys", status_code=status.HTTP_201_CREATED)
async def create_api_key(
    slug: str, body: ApiKeyCreate, user: CurrentUser, session: DbSession, request: Request
) -> ApiKeyCreateOut:
    """新建访问密钥。响应里的 key 是明文,且只在这一次出现。

    不收 Idempotency-Key:重放要回同一份明文就得把明文留在库里,与「只存摘要」冲突。
    """
    row, plaintext = await service.create_api_key(session, user.id, slug, name=body.name)
    # 审计只落 id 与名字,明文绝不进 detail
    set_audit_target(request, f"service:{slug}", {"api_key_id": row.id, "name": row.name})
    return ApiKeyCreateOut(**ApiKeyOut.model_validate(row).model_dump(), key=plaintext)


@router.delete("/services/{slug}/api-keys/{key_id}")
async def revoke_api_key(
    slug: str, key_id: int, user: CurrentUser, session: DbSession, request: Request
) -> ApiKeyOut:
    """吊销访问密钥(写 revoked_at,不删行)。重复吊销幂等。"""
    row = await service.revoke_api_key(session, user.id, slug, key_id)
    set_audit_target(request, f"service:{slug}", {"api_key_id": key_id})
    return ApiKeyOut.model_validate(row)
