"""Gateway patrol (30 s): tunnel state from Pod readiness, re-apply on a missing Deployment or an
image other than SUPERDL_NETGW_IMAGE, gateways of deleted accounts torn down, Deployments without
a row deleted."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.k8s import get_orchestrator
from app.core.k8s.base import NetgwStatus
from app.core.locks import LockKey, advisory_lock
from app.core.logging import get_logger
from app.core.outbox import enqueue
from app.core.timeutil import now_utc
from app.modules.account import service as account_service
from app.modules.netgw import service
from app.modules.netgw.models import STATUS_DELETING, NetGateway

logger = get_logger(__name__)


def _needs_apply(gw: NetGateway, obs: NetgwStatus | None, image: str) -> bool:
    """Only when nothing is pending: a missing Deployment, or an image drift with the feature on."""
    if gw.applied_generation < gw.generation:
        return False
    return obs is None or bool(image and obs.image != image)


async def _converge(session: AsyncSession, observed: dict[str, NetgwStatus]) -> list[NetgwStatus]:
    """Update every row not locked by a running task; returns the Deployments without a row."""
    known = set((await session.scalars(select(NetGateway.uuid))).all())
    orphans = [o for uuid, o in observed.items() if uuid not in known]
    rows = list((await session.scalars(select(NetGateway).with_for_update(skip_locked=True))).all())
    statuses = await account_service.user_statuses(session, {g.user_id for g in rows})
    image = get_settings().netgw_image
    now = now_utc()
    for gw in rows:
        obs = observed.get(gw.uuid)
        if gw.status == STATUS_DELETING:
            continue
        if statuses.get(gw.user_id) == "deleted":
            gw.status = STATUS_DELETING
            enqueue(session, service.DELETE_TASK, {"gateway_id": gw.id})
            continue
        if _needs_apply(gw, obs, image):
            gw.generation += 1
            enqueue(session, service.APPLY_TASK, {"gateway_id": gw.id})
        up = obs is not None and obs.ready
        if up != gw.tunnel_up:
            gw.tunnel_up = up
            gw.tunnel_changed_at = now
            logger.info("netgw_tunnel_changed", gateway_uuid=gw.uuid, up=up)
        gw.pod_phase = obs.phase if obs is not None else "Missing"
        gw.checked_at = now
    await session.commit()
    return orphans


async def netgw_patrol(sm: async_sessionmaker[AsyncSession]) -> None:
    async with advisory_lock(sm, LockKey.NETGW_PATROL) as got:
        if not got:
            return
        orch = get_orchestrator()
        try:
            observed = {s.uuid: s for s in await orch.list_netgw()}
        except Exception:
            logger.exception("netgw_patrol_list_failed")
            return
        async with sm() as session:
            orphans = await _converge(session, observed)
        for orphan in orphans:
            logger.error("netgw_orphan_reclaimed", gateway_uuid=orphan.uuid)
            await orch.delete_netgw(orphan.uuid, orphan.tenant_namespace)
