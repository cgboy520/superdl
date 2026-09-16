"""Admin routes (global instances / node enrollment / nodes and cluster / oversell report)."""

import asyncio

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

from app.core.audit import set_audit_target
from app.core.config import get_settings
from app.core.db import DbSession
from app.core.errors import AppError, ErrorCode, not_found
from app.core.gpu_models import supports_mig, supports_passthrough
from app.core.k8s import get_orchestrator
from app.core.k8s.base import (
    ClusterProbe,
)
from app.core.pagination import Page
from app.core.params import Cursor, IdempotencyKey, Limit
from app.core.platform_config import get_runtime_config
from app.modules.adminapi.deps import CurrentAdmin, require_roles
from app.modules.adminapi.schemas import (
    ReasonBody,
)
from app.modules.metering import service as metering_service
from app.modules.metering.schemas import NodeMetricsOut
from app.modules.nodes import service as nodes_service
from app.modules.nodes.schemas import (
    ClusterConfigStateOut,
    ClusterStatusOut,
    ComponentProbeOut,
    EnrollmentCommandOut,
    EnrollmentCreate,
    GpuModelAggregateOut,
    NodeDecommissionOut,
    NodeDecommissionRequest,
    NodeEnrollmentOut,
    NodeOut,
    OversellPoolOut,
    SwitchablePool,
)
from app.modules.orchestrator import (
    ports as orchestrator_ports,
    queries as orchestrator_queries,
    service as orchestrator_service,
)
from app.modules.orchestrator.schemas import (
    AdminForceStopRequest,
    AdminInstanceOut,
    InstanceEventOut,
    InstanceOut,
    PortPoolStatsOut,
)
from app.modules.services import service as services_service
from app.modules.services.schemas import AdminServiceOut

router = APIRouter(tags=["admin"])


@router.get("/instances", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_instances(
    session: DbSession,
    status: str | None = None,
    user_id: int | None = None,
    q: str | None = None,
    node_name: str | None = None,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
) -> Page[AdminInstanceOut]:
    """q: instance name or uuid prefix. node_name: exact. Cursor pagination (descending)."""
    page = await orchestrator_service.admin_list_instances(
        session,
        status_filter=status,
        user_id=user_id,
        q=q,
        node_name=node_name,
        cursor=cursor,
        limit=limit,
    )
    items = [AdminInstanceOut.model_validate(i) for i in page.items]
    await orchestrator_service.attach_instance_details(session, items)
    return Page[AdminInstanceOut](items=items, next_cursor=page.next_cursor, total=page.total)


@router.get("/services", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_services(
    session: DbSession,
    user_id: int | None = None,
    q: str | None = None,
    include_released: bool = False,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
) -> Page[AdminServiceOut]:
    """Global online services (any tenant). q: service name or slug prefix; deleted services not
    listed by default. Cursor pagination (descending).
    Read-only: the action is force-stop on the current revision instance."""
    return await services_service.admin_list_services_page(
        session,
        user_id=user_id,
        q=q,
        include_released=include_released,
        cursor=cursor,
        limit=limit,
    )


@router.post("/instances/{uuid}/force-stop", dependencies=[require_roles("ops")])
async def admin_force_stop(
    uuid: str, body: AdminForceStopRequest, session: DbSession, request: Request
) -> InstanceOut:
    """Force stop (reason required)."""
    instance = await orchestrator_service.admin_force_stop(session, uuid, reason=body.reason)
    set_audit_target(request, f"instance:{uuid}", detail={"reason": body.reason})
    return await orchestrator_service.instance_view(session, instance)


@router.post("/instances/{uuid}/preempt", dependencies=[require_roles("ops")])
async def admin_preempt(
    uuid: str, body: AdminForceStopRequest, session: DbSession, request: Request
) -> InstanceOut:
    """Force-reclaim one spot instance (reason required). Same path as automatic preemption: grace
    window + notification + tail bill by actual seconds."""
    instance = await orchestrator_service.admin_preempt(session, uuid, reason=body.reason)
    set_audit_target(request, f"instance:{uuid}", detail={"reason": body.reason})
    return await orchestrator_service.instance_view(session, instance)


@router.get("/instances/{uuid}/events", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_instance_events(
    uuid: str,
    session: DbSession,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
) -> Page[InstanceEventOut]:
    """Admin instance event timeline: the same implementation as the user side, descending cursor
    pagination; any tenant."""
    instance = await orchestrator_service.admin_get_instance(session, uuid)
    return await orchestrator_service.list_events(session, instance.id, cursor=cursor, limit=limit)


class EnrollmentRevokeRequest(ReasonBody):
    pass


class EnrollmentRegenerateRequest(BaseModel):
    ttl_hours: int = Field(default=24, ge=1, le=168)
    reason: str | None = Field(default=None, max_length=200)


def _command_out(enrollment, token: str) -> EnrollmentCommandOut:
    curl_command, wget_command = nodes_service.enrollment_commands(token)
    return EnrollmentCommandOut(
        enrollment=NodeEnrollmentOut.model_validate(enrollment),
        token=token,
        curl_command=curl_command,
        wget_command=wget_command,
    )


@router.get("/node-enrollments", dependencies=[require_roles("ops", "readonly")])
async def admin_list_enrollments(
    session: DbSession, active: bool = False
) -> list[NodeEnrollmentOut]:
    """Enrollment list (never includes tokens). active=true filters out stale terminal rows."""
    rows = await nodes_service.list_enrollments(session, active_only=active)
    return [NodeEnrollmentOut.model_validate(r) for r in rows]


@router.post("/node-enrollments", dependencies=[require_roles("ops")], status_code=201)
async def admin_create_enrollment(
    body: EnrollmentCreate,
    session: DbSession,
    request: Request,
    admin: CurrentAdmin,
    idempotency_key: IdempotencyKey = None,
) -> EnrollmentCommandOut:
    """Generate a node enrollment command. The token plaintext appears in this response only; the
    audit never records it.
    An Idempotency-Key replay creates no new row (the row's token is rotated and returned)."""
    enrollment, token = await nodes_service.create_enrollment(
        session, body, created_by=admin.id, idempotency_key=idempotency_key
    )
    set_audit_target(
        request,
        f"node_enrollment:{enrollment.id}",
        detail={"pool": enrollment.pool, "hostname": enrollment.hostname},
    )
    return _command_out(enrollment, token)


@router.post("/node-enrollments/{enrollment_id}/regenerate", dependencies=[require_roles("ops")])
async def admin_regenerate_enrollment(
    enrollment_id: int,
    body: EnrollmentRegenerateRequest,
    session: DbSession,
    request: Request,
) -> EnrollmentCommandOut:
    """Issue a new token (pending / expired / failed only), status back to pending."""
    enrollment, token = await nodes_service.regenerate_enrollment(
        session, enrollment_id, ttl_hours=body.ttl_hours
    )
    set_audit_target(
        request,
        f"node_enrollment:{enrollment_id}",
        detail={"action": "regenerate", **({"reason": body.reason} if body.reason else {})},
    )
    return _command_out(enrollment, token)


@router.post("/node-enrollments/{enrollment_id}/revoke", dependencies=[require_roles("ops")])
async def admin_revoke_enrollment(
    enrollment_id: int,
    body: EnrollmentRevokeRequest,
    session: DbSession,
    request: Request,
) -> NodeEnrollmentOut:
    enrollment = await nodes_service.revoke_enrollment(session, enrollment_id)
    set_audit_target(request, f"node_enrollment:{enrollment_id}", detail={"reason": body.reason})
    return NodeEnrollmentOut.model_validate(enrollment)


@router.get("/nodes/{node_name}/metrics", dependencies=[require_roles("ops", "readonly")])
async def admin_node_metrics(
    node_name: str, session: DbSession, range: str = "1h"
) -> NodeMetricsOut:
    """Per-card node curves (DCGM per-GPU) + 24 h XID count; source down → available=false (200).
    Unknown nodes return empty series. The response carries grafana_url.
    """
    out = await metering_service.node_gpu_metrics(node_name, range)
    cfg = await get_runtime_config(session)
    return out.model_copy(update={"grafana_url": cfg.grafana_url or None})


@router.get("/nodes", dependencies=[require_roles("ops", "readonly")])
async def admin_list_nodes(session: DbSession) -> list[NodeOut]:
    """Node view (inventory, refreshed by the 60 s patrol): Missing / unlabeled nodes included."""
    rows = await nodes_service.list_node_specs(session)
    active = await orchestrator_queries.count_active_instances_by_node(session)
    return [
        NodeOut(
            name=r.node_name,
            pool_label=r.pool_label or "",
            gpu_model=r.gpu_model or "GPU",
            gpu_total=r.gpu_count,
            gpu_used=r.gpu_used,
            status=r.status,
            vcpu=r.vcpu,
            mem_gb=r.mem_gb,
            disk_gb=r.disk_gb,
            driver_version=r.driver_version or "",
            cuda_version=r.cuda_version or "",
            gpu_model_raw=r.gpu_model_raw or "",
            vram_gb=r.vram_gb,
            unlabeled=r.unlabeled,
            label_synced=r.label_synced,
            last_seen=r.last_seen.isoformat() if r.last_seen else "",
            desired_pool=r.desired_pool or "",
            active_instances=active.get(r.node_name, 0),
            supports_mig=supports_mig(r.gpu_model),
            supports_passthrough=supports_passthrough(r.gpu_model),
        )
        for r in rows
    ]


@router.get("/nodes/port-pool", dependencies=[require_roles("ops", "readonly")])
async def admin_port_pool_stats(session: DbSession) -> PortPoolStatsOut:
    """SSH port pool level: blocked = held by other cluster objects (the periodic re-check returns
    them)."""
    return await orchestrator_ports.port_pool_stats(session)


async def _cluster_status_out(session: DbSession) -> ClusterStatusOut:
    row = await nodes_service.get_cluster_status(session)
    cfg = await get_runtime_config(session)
    settings = get_settings()
    prom_set = not (
        "localhost" in settings.prometheus_url or "127.0.0.1" in settings.prometheus_url
    )
    return ClusterStatusOut(
        api_reachable=bool(row and row.api_reachable),
        k8s_version=row.k8s_version if row else None,
        distro=row.distro if row else None,
        probed_at=row.probed_at if row else None,
        pools=dict(row.pools or {}) if row else {},
        pools_ready=dict(row.pools_ready or {}) if row else {},
        components=nodes_service.cluster_components(row),
        config=ClusterConfigStateOut(
            server_url_set=bool(cfg.cluster_server_url),
            join_token_set=bool(cfg.cluster_join_token),
            prometheus_url_set=prom_set,
            grafana_url=cfg.grafana_url or None,
            registry_host=cfg.registry_host or None,
            registry_project=cfg.registry_project or None,
        ),
        error=row.error if row else None,
    )


@router.get("/cluster/status", dependencies=[require_roles("ops", "readonly")])
async def admin_cluster_status(session: DbSession) -> ClusterStatusOut:
    """Cluster page data: pure read of the capability cache (refreshed by the worker patrol every
    60 s), no live probe."""
    return await _cluster_status_out(session)


@router.get(
    "/cluster/components/{component_key}/probe", dependencies=[require_roles("ops", "readonly")]
)
async def admin_component_probe(component_key: str, admin: CurrentAdmin) -> ComponentProbeOut:
    """Live deep probe of a health-check item: direct read-only K8s call on the request path (the
    rule's second exception, see docs/decisions.md).

    Hard timeout 5 s + 120 per admin per hour; failure → 503, the frontend degrades to the patrol
    snapshot.
    """
    return await nodes_service.probe_component_detail(admin.id, component_key)


@router.post("/cluster/test-connection", dependencies=[require_roles("ops")])
async def admin_cluster_test_connection(session: DbSession, request: Request) -> ClusterStatusOut:
    """Synchronous read-only probe written to the cache; unreachable / timeout → 502."""
    set_audit_target(request, "cluster:test-connection")
    try:
        probe = await asyncio.wait_for(get_orchestrator().probe_cluster(), timeout=5.0)
    except TimeoutError:
        probe = ClusterProbe(api_reachable=False, error="probe timed out (5s)")
    await nodes_service.save_cluster_probe(session, probe)
    await session.commit()
    if not probe.api_reachable:
        raise AppError(
            ErrorCode.CLUSTER_NOT_READY,
            key="nodes.clusterProbeFailed",
            params={"error": probe.error or "unknown"},
            http_status=status.HTTP_502_BAD_GATEWAY,
        )
    return await _cluster_status_out(session)


@router.get("/cluster/gpu-models", dependencies=[require_roles("ops", "readonly")])
async def admin_gpu_model_aggregates(session: DbSession) -> list[GpuModelAggregateOut]:
    """Inventory aggregated by canonical × pool (None model = unrecognised bucket)."""
    aggs = await nodes_service.gpu_model_aggregates(session)
    return [GpuModelAggregateOut.model_validate(a, from_attributes=True) for a in aggs]


class NodeCordonRequest(ReasonBody):
    pass


class NodeCordonOut(BaseModel):
    node_name: str
    unschedulable: bool
    queued: bool = True


async def _cordon(
    node_name: str, body: NodeCordonRequest, session: DbSession, request: Request, on: bool
) -> NodeCordonOut:
    names = {n.node_name for n in await nodes_service.list_node_specs(session)}
    if node_name not in names:
        raise not_found(key="nodes.nodeNotFound")
    await nodes_service.request_cordon(session, node_name, unschedulable=on, reason=body.reason)
    set_audit_target(
        request, f"node:{node_name}", detail={"unschedulable": on, "reason": body.reason}
    )
    return NodeCordonOut(node_name=node_name, unschedulable=on)


@router.post("/nodes/{node_name}/cordon", dependencies=[require_roles("ops")])
async def admin_cordon_node(
    node_name: str, body: NodeCordonRequest, session: DbSession, request: Request
) -> NodeCordonOut:
    """Stop scheduling (reason required; runs via outbox, the request path never touches K8s)."""
    return await _cordon(node_name, body, session, request, on=True)


@router.post("/nodes/{node_name}/uncordon", dependencies=[require_roles("ops")])
async def admin_uncordon_node(
    node_name: str, body: NodeCordonRequest, session: DbSession, request: Request
) -> NodeCordonOut:
    """Resume scheduling (reason required)."""
    return await _cordon(node_name, body, session, request, on=False)


class NodeSwitchPoolRequest(ReasonBody):
    pool: SwitchablePool


class NodeSwitchPoolOut(BaseModel):
    """Pool-switch acceptance receipt: cordon and desired pool are in the inventory; label
    convergence goes through the outbox, no node-side action needed."""

    node_name: str
    from_pool: str
    to_pool: str
    queued: bool = True


@router.post("/nodes/{node_name}/switch-pool", dependencies=[require_roles("ops")])
async def admin_switch_node_pool(
    node_name: str, body: NodeSwitchPoolRequest, session: DbSession, request: Request
) -> NodeSwitchPoolOut:
    """Switch the node pool (kata / hami / mig). Preconditions: no unreleased instance on the node,
    GPU model matches the target pool,
    target pool runtime ready. Once accepted the node stops scheduling and the pool and GPU operand
    labels change through the outbox;
    the node-side software differences between pools are delivered by DaemonSets keyed on labels -
    no login to the node, no reboot.
    """
    _, from_pool = await nodes_service.switch_node_pool(
        session, node_name, pool=body.pool, reason=body.reason
    )
    set_audit_target(
        request,
        f"node:{node_name}",
        detail={
            "action": "switch_pool",
            "from_pool": from_pool,
            "to_pool": body.pool,
            "reason": body.reason,
        },
    )
    return NodeSwitchPoolOut(node_name=node_name, from_pool=from_pool, to_pool=body.pool)


@router.post("/nodes/{node_name}/decommission", dependencies=[require_roles("ops")])
async def admin_decommission_node(
    node_name: str,
    body: NodeDecommissionRequest,
    session: DbSession,
    request: Request,
    force: bool = False,
) -> NodeDecommissionOut:
    """Node decommissioning (irreversible): stop scheduling + void every enrollment token of the
    machine + delete the Node object via outbox.
    Unreleased instances on the node → 409, `force=true` skips that gate (for machines that cannot
    be recovered).
    Cluster join-token rotation and kubelet certificate revocation are outside this endpoint.
    """
    revoked = await nodes_service.decommission_node(
        session, node_name, reason=body.reason, force=force
    )
    set_audit_target(
        request,
        f"node:{node_name}",
        detail={
            "action": "decommission",
            "reason": body.reason,
            "revoked_enrollments": revoked,
            "force": force,
        },
    )
    return NodeDecommissionOut(node_name=node_name, revoked_enrollments=revoked)


@router.get("/reports/oversell", dependencies=[require_roles("ops", "finance", "readonly")])
async def oversell_report(session: DbSession) -> list[OversellPoolOut]:
    """Oversell report: sold share / actual oversell ratio (sold ÷ Ready physical cards) / real
    utilisation over the last 24 h per pool."""
    nodes = await nodes_service.list_node_specs(session)
    physical: dict[str, int] = {}
    for n in nodes:
        if n.status != "Ready" or not n.pool_label:
            continue
        physical[n.pool_label] = physical.get(n.pool_label, 0) + n.gpu_count
    sold = await orchestrator_queries.running_gpu_share_by_pool(session)
    util_by_instance = await metering_service.gpu_util_last_24h_by_instance(session)
    pool_of = await orchestrator_queries.pool_by_instance(session, util_by_instance.keys())
    util_sum: dict[str, float] = {}
    util_hours: dict[str, int] = {}
    for iid, (total_util, hours) in util_by_instance.items():
        pool = pool_of.get(iid, "unknown")
        util_sum[pool] = util_sum.get(pool, 0.0) + total_util
        util_hours[pool] = util_hours.get(pool, 0) + hours

    out = []
    for pool, total in sorted(physical.items()):
        sold_share = round(sold.get(pool, 0.0), 2)
        hours = util_hours.get(pool, 0)
        out.append(
            OversellPoolOut(
                pool=pool,
                sold_share=sold_share,
                oversell_ratio=round(sold_share / total, 3) if total else 0.0,
                util_avg_24h=round(util_sum[pool] / hours, 1) if hours else None,
            )
        )
    return out
