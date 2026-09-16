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
from app.modules.orchestrator import service as orchestrator_service
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
    """Deploy a service: services row + revision-1 instance (creating) + event + outbox in one
    transaction, 202 asynchronous.
    An idempotency-key replay returns 200 + X-Idempotent-Replay."""
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
    """Service list: descending cursor pagination; name fuzzy (slug prefix included), status filters
    by derived status; deleted services are not listed."""
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
    """Rename / access-auth switch; no redeployment."""
    svc = await service.patch_service(
        session, user.id, slug, name=body.name, require_api_key=body.require_api_key
    )
    set_audit_target(request, f"service:{slug}")
    return await service.service_view(session, svc)


@router.post("/services/{slug}/stop")
async def stop_service(
    slug: str, user: CurrentUser, session: DbSession, request: Request
) -> ServiceOut:
    """Stop: the current instance shuts down and the endpoint returns 503; endpoint and keys are
    kept. Shares the rate-limit bucket with instance start / stop."""
    await orchestrator_service.check_lifecycle_rate_limit(user.id)
    svc = await service.stop_service(session, user.id, slug)
    set_audit_target(request, f"service:{slug}")
    return await service.service_view(session, svc)


@router.post("/services/{slug}/start")
async def start_service(
    slug: str, user: CurrentUser, session: DbSession, request: Request
) -> ServiceOut:
    """Start: start the current instance. Shares the rate-limit bucket with instance start /
    stop."""
    await account_service.require_real_name_if_required(
        session, user, key="orchestrator.realNameRequired"
    )
    await orchestrator_service.check_lifecycle_rate_limit(user.id)
    svc = await service.start_service(session, user.id, slug)
    set_audit_target(request, f"service:{slug}")
    return await service.service_view(session, svc)


@router.delete("/services/{slug}")
async def delete_service(
    slug: str, user: CurrentUser, session: DbSession, request: Request
) -> ServiceOut:
    """Delete the service: release the current instance and revoke every key; must be stopped first.
    Data disks are unaffected."""
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
    """Revision update (recreate): the new revision instance is creating, the old revision stops
    first; the endpoint returns 503 until the new revision is ready;
    endpoint and API keys do not change. Subscription services, an update in flight or an old
    revision mid-transition are 409. An idempotency-key replay returns 200."""
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
    """Status timeline (billing basis): union of every revision instance's events, descending cursor
    pagination."""
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
    """Paginated revision instances of the service (released included), by instance ID
    descending."""
    svc = await service.get_service(session, user.id, slug)
    return await service.list_revisions(session, user.id, svc, cursor=cursor, limit=limit)


@router.get("/services/{slug}/logs")
async def get_service_logs(
    slug: str,
    user: CurrentUser,
    session: DbSession,
    tail_lines: int = Query(default=200, ge=1),
) -> InstanceLogsOut:
    """Read the rollout revision's container log first, the current revision without a rollout.

    running/stopping only, otherwise 409; shares the rate limit with instance logs, not audited.
    """
    return await service.read_service_logs(session, user.id, slug, tail_lines=tail_lines)


@router.get("/services/{slug}/bills")
async def list_service_bills(
    slug: str,
    user: CurrentUser,
    session: DbSession,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
) -> Page[BillHourlyOut]:
    """Hourly bills: union over every revision instance of the service (bills still belong to
    instances)."""
    svc = await service.get_service(session, user.id, slug)
    return await service.service_bills_page(session, user.id, svc, cursor=cursor, limit=limit)


@router.get("/services/{slug}/api-keys")
async def list_api_keys(slug: str, user: CurrentUser, session: DbSession) -> list[ApiKeyOut]:
    """Access key list (revoked included), no plaintext."""
    rows = await service.list_api_keys(session, user.id, slug)
    return [ApiKeyOut.model_validate(r) for r in rows]


@router.post("/services/{slug}/api-keys", status_code=status.HTTP_201_CREATED)
async def create_api_key(
    slug: str, body: ApiKeyCreate, user: CurrentUser, session: DbSession, request: Request
) -> ApiKeyCreateOut:
    """Create an access key; the key in the response is the plaintext and appears only this once. No
    Idempotency-Key."""
    row, plaintext = await service.create_api_key(session, user.id, slug, name=body.name)
    set_audit_target(request, f"service:{slug}", {"api_key_id": row.id, "name": row.name})
    return ApiKeyCreateOut(**ApiKeyOut.model_validate(row).model_dump(), key=plaintext)


@router.delete("/services/{slug}/api-keys/{key_id}")
async def revoke_api_key(
    slug: str, key_id: int, user: CurrentUser, session: DbSession, request: Request
) -> ApiKeyOut:
    """Revoke an access key (writes revoked_at, keeps the row). Repeated revocation is
    idempotent."""
    row = await service.revoke_api_key(session, user.id, slug, key_id)
    set_audit_target(request, f"service:{slug}", {"api_key_id": key_id})
    return ApiKeyOut.model_validate(row)
