"""管理端路由(全局实例/节点注册/节点与集群/超卖报表,自 router.py 拆分)。"""

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request, status
from pydantic import BaseModel, Field

from app.core.audit import set_audit_target
from app.core.config import get_settings
from app.core.db import DbSession
from app.core.errors import AppError, ErrorCode, not_found
from app.core.k8s import get_orchestrator
from app.core.k8s.base import ClusterProbe
from app.core.pagination import Page
from app.core.platform_config import get_effective_platform_config
from app.modules.adminapi.deps import CurrentAdmin, require_roles
from app.modules.adminapi.schemas import (
    REASON_MAX_LENGTH,
    ClusterComponentOut,
    ClusterConfigStateOut,
    ClusterStatusOut,
    GpuModelAggregateOut,
    NodeOut,
    OversellPoolOut,
)
from app.modules.metering import service as metering_service
from app.modules.metering.schemas import NodeMetricsOut
from app.modules.nodes import service as nodes_service
from app.modules.nodes.schemas import (
    EnrollmentCommandOut,
    EnrollmentCreate,
    NodeEnrollmentOut,
)
from app.modules.orchestrator import service as orchestrator_service
from app.modules.orchestrator.schemas import (
    AdminForceStopRequest,
    AdminInstanceOut,
    InstanceEventOut,
    InstanceOut,
    PortPoolStatsOut,
)

router = APIRouter(tags=["admin"])


# ---------- 全局实例(角色:admin / ops) ----------


@router.get("/instances", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_instances(
    session: DbSession,
    status: str | None = None,
    user_id: int | None = None,
    q: str | None = None,
    node_name: str | None = None,
    cursor: str | None = None,
    limit: int | None = Query(default=None, le=100),
) -> Page[AdminInstanceOut]:
    """q:实例名或 uuid 前缀。node_name:精确。游标分页(降序)。"""
    page = await orchestrator_service.admin_list_instances(
        session,
        status_filter=status,
        user_id=user_id,
        q=q,
        node_name=node_name,
        cursor=cursor,
        limit=limit,
    )
    return Page[AdminInstanceOut](
        items=[AdminInstanceOut.model_validate(i) for i in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("/instances/{uuid}/force-stop", dependencies=[require_roles("ops")])
async def admin_force_stop(
    uuid: str, body: AdminForceStopRequest, session: DbSession, request: Request
) -> InstanceOut:
    """强制停止(原因必填)。"""
    instance = await orchestrator_service.admin_force_stop(session, uuid, reason=body.reason)
    set_audit_target(request, f"instance:{uuid}", detail={"reason": body.reason})
    return InstanceOut.model_validate(instance)


@router.get("/instances/{uuid}/events", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_instance_events(
    uuid: str,
    session: DbSession,
    cursor: str | None = None,
    limit: int | None = Query(default=None, le=100),
) -> Page[InstanceEventOut]:
    """管理端实例事件时间线(排障):与用户端同一实现,降序游标分页;不限租户。"""
    instance = await orchestrator_service.admin_get_instance(session, uuid)
    return await orchestrator_service.list_events(session, instance.id, cursor=cursor, limit=limit)


# ---------- 节点注册(读:ops/readonly,写:ops,admin 恒许) ----------


class EnrollmentRevokeRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=REASON_MAX_LENGTH)


class EnrollmentRegenerateRequest(BaseModel):
    ttl_hours: int = Field(default=24, ge=1, le=168)


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
    """注册记录列表(永不含 token)。active=true 过滤陈旧终态行。"""
    rows = await nodes_service.list_enrollments(session, active_only=active)
    return [NodeEnrollmentOut.model_validate(r) for r in rows]


@router.post("/node-enrollments", dependencies=[require_roles("ops")], status_code=201)
async def admin_create_enrollment(
    body: EnrollmentCreate,
    session: DbSession,
    request: Request,
    admin: CurrentAdmin,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> EnrollmentCommandOut:
    """生成节点注册命令。token 明文仅本响应出现一次;审计不落 token。
    Idempotency-Key 重放不建新行(轮换该行 token 后返回)。"""
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
    """换新令牌(仅 待执行/已过期/已失败),状态回 pending。"""
    enrollment, token = await nodes_service.regenerate_enrollment(
        session, enrollment_id, ttl_hours=body.ttl_hours
    )
    set_audit_target(request, f"node_enrollment:{enrollment_id}", detail={"action": "regenerate"})
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


# ---------- 节点与超卖报表(角色:admin / ops / readonly) ----------


@router.get("/nodes/{node_name}/metrics", dependencies=[require_roles("ops", "readonly")])
async def admin_node_metrics(
    node_name: str, session: DbSession, range: str = "1h"
) -> NodeMetricsOut:
    """节点每卡曲线(DCGM per-GPU)+ 24h XID 计数;断源 available=false(200)。

    节点存在性不做强校验,不存在的节点返回空序列。响应附 grafana_url(可选深挖外链)。
    """
    out = await metering_service.node_gpu_metrics(node_name, range)
    cfg = await get_effective_platform_config(session)
    return NodeMetricsOut(**out, grafana_url=cfg.get("grafana_url") or None)


@router.get("/nodes", dependencies=[require_roles("ops", "readonly")])
async def admin_list_nodes(session: DbSession) -> list[NodeOut]:
    """节点视图(台账口径,60s 巡检刷新):含 Missing/未打池标签节点。首轮巡检前为空列表。"""
    rows = await nodes_service.list_node_specs(session)
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
        )
        for r in rows
    ]


@router.get("/nodes/port-pool", dependencies=[require_roles("ops", "readonly")])
async def admin_port_pool_stats(session: DbSession) -> PortPoolStatsOut:
    """SSH 端口池水位:blocked=被集群对象撞占(周期复检自动放回),计数持续上涨要查孤儿端点。"""
    return await orchestrator_service.port_pool_stats(session)


HELMFILE = "helmfile -f deploy/cluster/helmfile.yaml.gotmpl -e <full|light>"


def _cluster_components(row: Any) -> list[ClusterComponentOut]:  # nodes.ClusterStatus 行或 None
    hami_ok = bool(row and row.hami_ready)
    kps_ok = bool(row and row.kps_present)
    dcgm_ok = bool(row and row.dcgm_present)
    gpu_op_ok = bool(row and row.gpu_operator_present)
    kata_ok = bool(row and row.kata_runtimeclass)
    scs = list(row.storage_classes or []) if row else []
    return [
        ClusterComponentOut(
            key="hami",
            ok=hami_ok,
            detail=None if hami_ok else "hami-scheduler Deployment 未就绪",
            fix_hint=None if hami_ok else f"{HELMFILE} -l name=hami apply",
        ),
        ClusterComponentOut(
            key="monitoring",
            ok=kps_ok,
            fix_hint=None if kps_ok else f"{HELMFILE} -l name=kube-prometheus-stack apply",
        ),
        ClusterComponentOut(
            key="dcgm",
            ok=dcgm_ok,
            detail=None if dcgm_ok else "dcgm-exporter DaemonSet 未发现(GPU 指标不可用)",
            fix_hint=None if dcgm_ok else f"{HELMFILE} -l name=gpu-operator apply",
        ),
        ClusterComponentOut(
            key="gpu_operator",
            ok=gpu_op_ok,
            detail=None if gpu_op_ok else "gpu-operator 未发现(light/k3s 集群预期如此)",
            fix_hint=None if gpu_op_ok else f"{HELMFILE} -l name=gpu-operator apply",
        ),
        ClusterComponentOut(
            key="kata_runtimeclass",
            ok=kata_ok,
            detail=None if kata_ok else "RuntimeClass kata-qemu 不存在(dedicated 档不可用)",
            fix_hint=(
                None if kata_ok else "kubectl apply -f deploy/cluster/kata/kata-runtimeclass.yaml"
            ),
        ),
        ClusterComponentOut(
            key="storage",
            ok=bool(scs),
            detail=", ".join(scs) if scs else "无 StorageClass(数据盘/JuiceFS 不可用)",
            fix_hint=None if scs else f"{HELMFILE} -l name=juicefs-csi-driver apply",
        ),
    ]


async def _cluster_status_out(session: DbSession) -> ClusterStatusOut:
    row = await nodes_service.get_cluster_status(session)
    cfg = await get_effective_platform_config(session)
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
        components=_cluster_components(row),
        config=ClusterConfigStateOut(
            server_url_set=bool(cfg.get("cluster_server_url")),
            join_token_set=bool(cfg.get("cluster_join_token")),
            prometheus_url_set=prom_set,
            grafana_url=cfg.get("grafana_url") or None,
        ),
        error=row.error if row else None,
    )


@router.get("/cluster/status", dependencies=[require_roles("ops", "readonly")])
async def admin_cluster_status(session: DbSession) -> ClusterStatusOut:
    """集群页数据:纯读能力缓存(worker 巡检 60s 刷新),不实时探测。"""
    return await _cluster_status_out(session)


@router.post("/cluster/test-connection", dependencies=[require_roles("ops")])
async def admin_cluster_test_connection(session: DbSession, request: Request) -> ClusterStatusOut:
    """同步只读探测并落缓存(对齐 SmsTestCard 先例);不可达/超时 → 502。"""
    set_audit_target(request, "cluster:test-connection")
    try:
        probe = await asyncio.wait_for(get_orchestrator().probe_cluster(), timeout=5.0)
    except TimeoutError:
        probe = ClusterProbe(api_reachable=False, error="探测超时(5s)")
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
    """台账按 canonical×池聚合(SKU 表单「从集群资源创建」下拉;None 型号=未识别桶)。"""
    aggs = await nodes_service.gpu_model_aggregates(session)
    return [GpuModelAggregateOut(**vars(a)) for a in aggs]


class NodeCordonRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=REASON_MAX_LENGTH)


class NodeCordonOut(BaseModel):
    node_name: str
    unschedulable: bool
    queued: bool = True  # 经 outbox 异步执行,列表轮询看生效


async def _cordon(
    node_name: str, body: NodeCordonRequest, session: DbSession, request: Request, on: bool
) -> NodeCordonOut:
    # 读台账(node_specs,巡检 60s 粒度)而非请求路径直连 K8s:
    # 集群不稳时恰是运维最需要 cordon 的时刻,不能硬 500
    names = {n.node_name for n in await nodes_service.list_node_specs(session)}
    if node_name not in names:
        raise not_found("节点不存在或未打池标签")
    await nodes_service.request_cordon(session, node_name, unschedulable=on, reason=body.reason)
    set_audit_target(
        request, f"node:{node_name}", detail={"unschedulable": on, "reason": body.reason}
    )
    return NodeCordonOut(node_name=node_name, unschedulable=on)


@router.post("/nodes/{node_name}/cordon", dependencies=[require_roles("ops")])
async def admin_cordon_node(
    node_name: str, body: NodeCordonRequest, session: DbSession, request: Request
) -> NodeCordonOut:
    """停止调度(reason 必填;经 outbox 执行,请求路径不动 K8s)。"""
    return await _cordon(node_name, body, session, request, on=True)


@router.post("/nodes/{node_name}/uncordon", dependencies=[require_roles("ops")])
async def admin_uncordon_node(
    node_name: str, body: NodeCordonRequest, session: DbSession, request: Request
) -> NodeCordonOut:
    """恢复调度(reason 必填)。"""
    return await _cordon(node_name, body, session, request, on=False)


@router.get("/reports/oversell", dependencies=[require_roles("ops", "finance", "readonly")])
async def oversell_report(session: DbSession) -> list[OversellPoolOut]:
    """超卖报表:各池 已售份额 / 实际超卖率(已售 ÷ Ready 物理卡数)/ 近 24h 真实利用率。"""
    # 台账口径(node_specs,Ready 节点),不直连 K8s:集群不可达时报表仍可用
    nodes = await nodes_service.list_node_specs(session)
    physical: dict[str, int] = {}
    for n in nodes:
        if n.status != "Ready" or not n.pool_label:
            continue
        physical[n.pool_label] = physical.get(n.pool_label, 0) + n.gpu_count
    sold = await orchestrator_service.running_gpu_share_by_pool(session)
    # 按池加权平均:实例小时数据在 metering,池归属在 orchestrator,此处组装
    util_by_instance = await metering_service.gpu_util_last_24h_by_instance(session)
    pool_of = await orchestrator_service.pool_by_instance(session, util_by_instance.keys())
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
