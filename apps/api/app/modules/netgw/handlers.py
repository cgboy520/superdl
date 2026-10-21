"""Outbox tasks of private-network gateways: converge (netgw.apply) and tear down (netgw.delete)."""

from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.k8s import ensure_registry_pull_secret, get_orchestrator
from app.core.k8s.base import NETGW_NAMESPACE
from app.core.logging import get_logger
from app.core.outbox import OutboxTask, RetryPolicy, outbox_handler
from app.modules.netgw import service
from app.modules.netgw.models import STATUS_DELETING, NetGateway
from app.modules.netgw.spec import k8s_spec

logger = get_logger(__name__)

_RETRY = RetryPolicy(max_retries=20, backoff_base_seconds=10, backoff_max_seconds=300)


async def _lock(session: AsyncSession, gateway_id: int) -> NetGateway | None:
    return await session.scalar(
        select(NetGateway).where(NetGateway.id == gateway_id).with_for_update()
    )


@outbox_handler(service.APPLY_TASK, retry=_RETRY)
async def handle_apply(session: AsyncSession, task: OutboxTask) -> None:
    """Render the current row (not the one at enqueue time) and converge it; a later generation
    is applied by its own task, so recording max(applied, rendered) is enough. The tenant side
    needs its namespaces: the owner's for a personal gateway, the attached instances' for an
    organization one (an instance that failed early may never have created its own)."""
    gw = await _lock(session, task.payload["gateway_id"])
    if gw is None or gw.status == STATUS_DELETING:
        return
    generation = gw.generation
    orch = get_orchestrator()
    inp = await service.render_input(session, gw, dns_upstream=await orch.cluster_dns_ip())
    spec = k8s_spec(inp)
    for namespace in (inp.tenant_namespace,) if inp.tenant_namespace else spec.l3_namespaces:
        await orch.ensure_namespace(namespace)
    pull_secret = await ensure_registry_pull_secret(session, NETGW_NAMESPACE)
    await orch.apply_netgw(replace(spec, image_pull_secret=pull_secret))
    gw.applied_generation = max(gw.applied_generation, generation)
    await session.commit()
    logger.info("netgw_applied", gateway_uuid=gw.uuid, generation=generation)


@outbox_handler(service.DELETE_TASK, retry=_RETRY)
async def handle_delete(session: AsyncSession, task: OutboxTask) -> None:
    gw = await _lock(session, task.payload["gateway_id"])
    if gw is None:
        return
    await get_orchestrator().delete_netgw(gw.uuid)
    await session.delete(gw)
    await session.commit()
    logger.info("netgw_deleted", gateway_uuid=gw.uuid)
