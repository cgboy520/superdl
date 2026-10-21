"""Admin routes of private-network gateways: global list, forced deletion."""

from fastapi import APIRouter, Request

from app.core.audit import set_audit_target
from app.core.db import DbSession
from app.modules.adminapi.deps import require_roles
from app.modules.netgw import service as netgw_service
from app.modules.netgw.schemas import AdminGatewayDelete, AdminGatewayOut

router = APIRouter(tags=["admin-netgw"])


@router.get("/netgw/gateways", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_gateways(
    session: DbSession, user_id: int | None = None, org_uuid: str | None = None
) -> list[AdminGatewayOut]:
    """Every tenant's gateways, personal and organization (newest first, at most 500); user_id is
    the owner or the creator, org_uuid narrows to one organization; secrets never included."""
    return await netgw_service.admin_list_gateways(session, user_id=user_id, org_uuid=org_uuid)


@router.post("/netgw/gateways/{uuid}/delete", dependencies=[require_roles("ops")])
async def admin_delete_gateway(
    uuid: str, body: AdminGatewayDelete, session: DbSession, request: Request
) -> AdminGatewayOut:
    """Tear down a tenant's gateway (reason required)."""
    gw = await netgw_service.admin_delete_gateway(session, uuid)
    set_audit_target(request, f"netgw:{uuid}", detail={"reason": body.reason})
    return (await netgw_service.admin_list_gateways(session, uuid=gw.uuid))[0]
