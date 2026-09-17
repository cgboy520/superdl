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
    """Instance list (dev boxes only; service revision instances go through /services): descending
    cursor pagination, status exact,
    name fuzzy (uuid prefix included)."""
    return await service.list_instances_page(
        session, user.id, status=status, name=name, cursor=cursor, limit=limit
    )


@router.get("/instances/expiring")
async def list_expiring_instances(
    user: CurrentUser,
    session: DbSession,
    within_days: int = Query(default=7, ge=1, le=90),
) -> list[InstanceOut]:
    """Expiring subscription instances: active subscription with expiry ≤ now+within_days,
    ascending,
    no pagination."""
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
    """Subscription renewal: re-quoted at the new period's discount and charged at once (402/400
    when
    short); renewing while frozen unfreezes back to stopped."""
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
    """On-demand → subscription: settle the on-demand bill up to now, then prepay once at the period
    discount; request and response as `/renew`, counting from now."""
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
    """Spot → on-demand; already on-demand returns unchanged. The current clock hour is settled
    entirely at the on-demand price."""
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
    """Release the instance (instance disk erased, data disks unaffected)."""
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
    """Status timeline (billing basis), descending cursor pagination."""
    instance = await service.get_instance(session, user.id, uuid)
    return await service.list_events(session, instance.id, cursor=cursor, limit=limit)


@router.get("/instances/{uuid}/access")
async def get_instance_access(
    uuid: str, user: CurrentUser, session: DbSession
) -> InstanceAccessOut:
    """Access information, fields by form: dev gets SSH + Jupyter, service revision instances the
    endpoint URL (SSH whenever enabled)."""
    return await service.get_access(session, user.id, uuid)


@router.get("/instances/{uuid}/logs")
async def get_instance_logs(
    uuid: str,
    user: CurrentUser,
    session: DbSession,
    tail_lines: int = Query(default=200, ge=1),
) -> InstanceLogsOut:
    """Container log (read-only, not audited): non-owner 404; rate limit 20/h/user; running/stopping
    only, otherwise 409;
    tail_lines default 200, truncated above 2000."""
    return await service.read_instance_logs(session, user.id, uuid, tail_lines=tail_lines)


@router.post("/instances/{uuid}/reset-jupyter-token")
async def reset_jupyter_token(
    uuid: str, user: CurrentUser, session: DbSession, request: Request
) -> InstanceOut:
    instance = await service.reset_jupyter_token(session, user.id, uuid)
    set_audit_target(request, f"instance:{uuid}")
    return await service.instance_view(session, instance)
