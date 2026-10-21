from fastapi import APIRouter, Query, Request, Response, status

from app.core.audit import set_audit_target
from app.core.db import DbSession
from app.core.http import mark_idempotent_replay
from app.core.params import IdempotencyKey
from app.core.ratelimit import check_rate_limit
from app.modules.account import service as account_service
from app.modules.account.deps import CurrentUser
from app.modules.netgw import service
from app.modules.netgw.schemas import (
    AttachmentIn,
    ForwardIn,
    GatewayIn,
    GatewayOut,
    NetgwConfigOut,
)
from app.modules.orchestrator.schemas import InstanceLogsOut

router = APIRouter(prefix="/netgw", tags=["netgw"])


async def _write_limit(user_id: int) -> None:
    await check_rate_limit(f"netgw-write:{user_id}", max_attempts=60, window_seconds=3600.0)


@router.get("/config")
async def get_config(_user: CurrentUser) -> NetgwConfigOut:
    """Whether gateways are offered here, limits, and the platform egress addresses to allow on
    the tenant side."""
    return service.netgw_config()


@router.get("/gateways")
async def list_gateways(user: CurrentUser, session: DbSession) -> list[GatewayOut]:
    return await service.gateway_views(session, await service.list_gateways(session, user.id))


@router.post("/gateways", status_code=status.HTTP_201_CREATED)
async def create_gateway(
    body: GatewayIn,
    user: CurrentUser,
    session: DbSession,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> GatewayOut:
    """Create a gateway; the Pod is converged asynchronously (state `applying`). An
    idempotency-key replay returns 200 + X-Idempotent-Replay."""
    await account_service.require_real_name_if_required(session, user, key="netgw.realNameRequired")
    await check_rate_limit(f"netgw-create:{user.id}", max_attempts=10, window_seconds=3600.0)
    gw, created = await service.create_gateway(session, user.id, body, idempotency_key)
    if not created:
        mark_idempotent_replay(response)
    set_audit_target(request, f"netgw:{gw.uuid}")
    return await service.gateway_view(session, gw)


@router.get("/gateways/{uuid}")
async def get_gateway(uuid: str, user: CurrentUser, session: DbSession) -> GatewayOut:
    return await service.gateway_view(session, await service.get_gateway(session, user.id, uuid))


@router.put("/gateways/{uuid}")
async def update_gateway(
    uuid: str, body: GatewayIn, user: CurrentUser, session: DbSession, request: Request
) -> GatewayOut:
    """Replace the configuration (protocol fixed; null secrets keep the stored ones). The gateway
    Pod restarts with the new spec."""
    await _write_limit(user.id)
    gw = await service.update_gateway(session, user.id, uuid, body)
    set_audit_target(request, f"netgw:{uuid}")
    return await service.gateway_view(session, gw)


@router.delete("/gateways/{uuid}", status_code=status.HTTP_202_ACCEPTED)
async def delete_gateway(
    uuid: str, user: CurrentUser, session: DbSession, request: Request
) -> GatewayOut:
    await _write_limit(user.id)
    gw = await service.delete_gateway(session, user.id, uuid)
    set_audit_target(request, f"netgw:{uuid}")
    return await service.gateway_view(session, gw)


@router.post("/gateways/{uuid}/forwards", status_code=status.HTTP_201_CREATED)
async def add_forward(
    uuid: str, body: ForwardIn, user: CurrentUser, session: DbSession, request: Request
) -> GatewayOut:
    await _write_limit(user.id)
    gw = await service.add_forward(session, user.id, uuid, body)
    set_audit_target(request, f"netgw:{uuid}", detail={"forward": body.name})
    return await service.gateway_view(session, gw)


@router.delete("/gateways/{uuid}/forwards/{forward_id}")
async def delete_forward(
    uuid: str, forward_id: int, user: CurrentUser, session: DbSession, request: Request
) -> GatewayOut:
    await _write_limit(user.id)
    gw = await service.delete_forward(session, user.id, uuid, forward_id)
    set_audit_target(request, f"netgw:{uuid}", detail={"forward_id": forward_id})
    return await service.gateway_view(session, gw)


@router.post("/gateways/{uuid}/attachments", status_code=status.HTTP_201_CREATED)
async def attach_instance(
    uuid: str, body: AttachmentIn, user: CurrentUser, session: DbSession, request: Request
) -> GatewayOut:
    """Attach a dev instance to the gateway's whole-subnet access (L3 must be on). The gateway
    picks it up without restarting; the instance gets its tunnel interface on its next start.
    Attaching an already attached instance again is a no-op."""
    await _write_limit(user.id)
    gw = await service.attach_instance(session, user.id, uuid, body)
    set_audit_target(request, f"netgw:{uuid}", detail={"instance": body.instance_uuid})
    return await service.gateway_view(session, gw)


@router.delete("/gateways/{uuid}/attachments/{instance_uuid}")
async def detach_instance(
    uuid: str, instance_uuid: str, user: CurrentUser, session: DbSession, request: Request
) -> GatewayOut:
    """Detach an instance: the gateway drops it at once; its tunnel interface goes with its next
    restart."""
    await _write_limit(user.id)
    gw = await service.detach_instance(session, user.id, uuid, instance_uuid)
    set_audit_target(request, f"netgw:{uuid}", detail={"instance": instance_uuid})
    return await service.gateway_view(session, gw)


@router.get("/gateways/{uuid}/logs")
async def gateway_logs(
    uuid: str,
    user: CurrentUser,
    session: DbSession,
    tail_lines: int = Query(default=200, ge=1, le=service.LOGS_MAX_TAIL_LINES),
) -> InstanceLogsOut:
    """Gateway container log: owner only, 20/h per user, not audited."""
    return await service.read_gateway_logs(session, user.id, uuid, tail_lines=tail_lines)
