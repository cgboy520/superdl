import asyncio
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request, status
from pydantic import BaseModel, Field

from app.core.audit import set_audit_target
from app.core.config import get_settings
from app.core.db import DbSession
from app.core.errors import AppError, ErrorCode, not_found
from app.core.gpu_models import canonical_gpu_model, model_matches
from app.core.k8s import get_orchestrator
from app.core.k8s.base import ClusterProbe
from app.core.platform_config import get_effective_platform_config
from app.modules.adminapi import service
from app.modules.adminapi.deps import CurrentAdmin, require_roles
from app.modules.adminapi.models import AdminUser
from app.modules.adminapi.schemas import (
    AdjustmentOut,
    AdjustmentStatusOut,
    AdminAlertOut,
    AdminImageOut,
    AdminLoginRequest,
    AdminOrderOut,
    AdminOut,
    AdminToken,
    AnnouncementResultOut,
    AuditLogOut,
    CapacityPreviewOut,
    CapacityWarningOut,
    ClusterComponentOut,
    ClusterConfigStateOut,
    ClusterStatusOut,
    DeadTaskOut,
    GpuModelAggregateOut,
    ImageCoverageOut,
    ImageNodeCacheOut,
    NodeOut,
    OrderBackfillOut,
    OrderVerifyOut,
    OutboxTaskStatusOut,
    OversellPoolOut,
    PaymentAnomalyOut,
    PlatformConfigItemOut,
    PlatformConfigOut,
    PoliciesAdminOut,
    PrewarmEnqueuedOut,
    ReconciliationOut,
    RevenueReportOut,
    SmsTestOut,
    TenantOut,
    TenantStatusOut,
    UpdatedKeysOut,
)
from app.modules.catalog import service as catalog_service
from app.modules.catalog.schemas import (
    ImageCreate,
    ImageUpdate,
    SkuAdminOut,
    SkuCreate,
    SkuUpdate,
)
from app.modules.metering import service as metering_service
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
    InstanceOut,
)

router = APIRouter(tags=["admin"])


@router.post("/auth/login")
async def admin_login(body: AdminLoginRequest, session: DbSession, request: Request) -> AdminToken:
    client_ip = request.client.host if request.client else None
    token, admin = await service.login(session, body.username, body.password, client_ip=client_ip)
    set_audit_target(request, f"admin:{admin.id}")
    return AdminToken(access_token=token, admin=AdminOut.model_validate(admin))


@router.get("/me")
async def admin_me(admin: CurrentAdmin) -> AdminOut:
    return AdminOut.model_validate(admin)


# ---------- SKU 管理(角色:admin / ops) ----------


@router.get("/skus", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_skus(session: DbSession) -> list[SkuAdminOut]:
    """SKU 列表,组装台账容量与占用列(catalog+nodes+orchestrator 三 service 汇合点)。"""
    skus = await catalog_service.admin_list_skus(session)
    specs = await nodes_service.ready_specs(session)
    sold = await orchestrator_service.active_gpu_counts_by_sku(session)
    out: list[SkuAdminOut] = []
    for sku in skus:
        item = SkuAdminOut.model_validate(sku)
        wanted = canonical_gpu_model(sku.gpu_model)
        item.capacity_gpus = sum(
            sp.gpu_count
            for sp in specs
            if sp.pool_label == sku.pool_label and model_matches(wanted, sp.gpu_model)
        )
        if item.capacity_gpus:
            # 已售名义算力(卡×pct/100)对物理与对可售(×超卖)的两个比值,2 位小数
            nominal = Decimal(sold.get(sku.id, 0)) * Decimal(sku.gpu_cores_pct) / Decimal(100)
            cap = Decimal(item.capacity_gpus)
            item.actual_oversell = str(
                (nominal / cap).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
            )
            item.sold_share = str(
                (nominal / (cap * sku.oversell_cores)).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_EVEN
                )
            )
        out.append(item)
    return out


@router.get("/skus/capacity-preview", dependencies=[require_roles("ops", "readonly")])
async def sku_capacity_preview(
    session: DbSession,
    gpu_model: str,
    pool_label: str,
    tier: str,
    gpu_cores_pct: int = 100,
    oversell_cores: Decimal = Decimal("1.00"),
    vram_gb: int | None = None,
) -> CapacityPreviewOut:
    """SKU 表单实时容量预览(纯台账;创建仍软校验,上架才硬校验)。"""
    warnings: list[CapacityWarningOut] = []
    wanted = canonical_gpu_model(gpu_model)
    if wanted is None:
        warnings.append(CapacityWarningOut(code="unrecognized_model", params={"model": gpu_model}))
    specs = [
        sp
        for sp in await nodes_service.list_node_specs(session)
        if sp.pool_label == pool_label and model_matches(wanted, sp.gpu_model)
    ]
    ready = [sp for sp in specs if sp.status == "Ready"]
    ready_gpus = sum(sp.gpu_count for sp in ready)
    if not ready:
        warnings.append(
            CapacityWarningOut(
                code="no_ready_node", params={"model": wanted or gpu_model, "pool": pool_label}
            )
        )
    max_vram = max((sp.vram_gb for sp in ready), default=0)
    if vram_gb is not None and ready and vram_gb > max_vram:
        warnings.append(
            CapacityWarningOut(
                code="vram_exceeds_node", params={"vram_gb": vram_gb, "node_vram_gb": max_vram}
            )
        )
    if tier in ("shared_std", "shared_eco") and gpu_cores_pct > 0:
        per_gpu = int(Decimal(100) * oversell_cores // Decimal(gpu_cores_pct))
        est = ready_gpus * per_gpu
    else:
        est = ready_gpus
    return CapacityPreviewOut(
        matching_nodes=len(specs),
        ready_gpus=ready_gpus,
        total_gpus=sum(sp.gpu_count for sp in specs),
        est_instances=est,
        warnings=warnings,
    )


@router.post("/skus", dependencies=[require_roles("ops")], status_code=201)
async def admin_create_sku(body: SkuCreate, session: DbSession, request: Request) -> SkuAdminOut:
    sku = await catalog_service.admin_create_sku(session, body)
    set_audit_target(request, f"sku:{sku.id}", detail={"name": sku.name})
    return SkuAdminOut.model_validate(sku)


@router.patch("/skus/{sku_id}", dependencies=[require_roles("ops")])
async def admin_update_sku(
    sku_id: int, body: SkuUpdate, session: DbSession, request: Request, force: bool = False
) -> SkuAdminOut:
    sku = await catalog_service.admin_update_sku(session, sku_id, body, force=force)
    set_audit_target(
        request, f"sku:{sku.id}", detail=body.model_dump(exclude_unset=True, mode="json")
    )
    return SkuAdminOut.model_validate(sku)


# ---------- 镜像与预热(读:ops/readonly,写:ops,admin 恒许) ----------


class ImageDeleteRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=256)


def _admin_image_out(img, coverage: dict[int, tuple[int, int, int]]) -> AdminImageOut:
    cached, total, failed = coverage.get(img.id, (0, 0, 0))
    return AdminImageOut(
        id=img.id,
        framework=img.framework,
        framework_version=img.framework_version,
        python_version=img.python_version,
        cuda_version=img.cuda_version,
        image_ref=img.image_ref,
        prewarm_enabled=img.prewarm_enabled,
        sort=img.sort,
        coverage=ImageCoverageOut(
            cached=cached, total=total, pct=(cached * 100 // total) if total else 0
        ),
        failed_nodes=failed,
    )


@router.get("/images", dependencies=[require_roles("ops", "readonly")])
async def admin_list_images(session: DbSession) -> list[AdminImageOut]:
    """镜像目录 + 每镜像预热覆盖率(纯 DB 聚合,不调 K8s)。"""
    images = await catalog_service.list_images(session)
    coverage = await catalog_service.image_coverage(session)
    return [_admin_image_out(img, coverage) for img in images]


@router.post("/images", dependencies=[require_roles("ops")], status_code=201)
async def admin_create_image(
    body: ImageCreate, session: DbSession, request: Request
) -> AdminImageOut:
    img = await catalog_service.admin_create_image(session, body)
    set_audit_target(request, f"image:{img.id}", detail={"image_ref": img.image_ref})
    return _admin_image_out(img, {})


@router.patch("/images/{image_id}", dependencies=[require_roles("ops")])
async def admin_update_image(
    image_id: int, body: ImageUpdate, session: DbSession, request: Request
) -> AdminImageOut:
    img = await catalog_service.admin_update_image(session, image_id, body)
    set_audit_target(
        request, f"image:{img.id}", detail=body.model_dump(exclude_unset=True, mode="json")
    )
    coverage = await catalog_service.image_coverage(session)
    return _admin_image_out(img, coverage)


@router.delete("/images/{image_id}", dependencies=[require_roles("ops")], status_code=204)
async def admin_delete_image(
    image_id: int, body: ImageDeleteRequest, session: DbSession, request: Request
) -> None:
    """删除目录条目(cache 行 CASCADE;运行中实例存 image_ref 快照不受影响)。reason 必填。"""
    img = await catalog_service.get_image(session, image_id)
    set_audit_target(
        request, f"image:{image_id}", detail={"image_ref": img.image_ref, "reason": body.reason}
    )
    await catalog_service.admin_delete_image(session, image_id)


@router.post("/images/{image_id}/prewarm", dependencies=[require_roles("ops")])
async def admin_prewarm_image(
    image_id: int, session: DbSession, request: Request
) -> PrewarmEnqueuedOut:
    """立即预热:非 cached 行置 pending 并同事务入队(请求路径零 K8s 调用)。"""
    enqueued = await catalog_service.admin_prewarm_image(session, image_id)
    set_audit_target(request, f"image:{image_id}", detail={"enqueued": enqueued})
    return PrewarmEnqueuedOut(enqueued=enqueued)


@router.get("/images/{image_id}/nodes", dependencies=[require_roles("ops", "readonly")])
async def admin_image_nodes(image_id: int, session: DbSession) -> list[ImageNodeCacheOut]:
    """每节点缓存明细(failed 行含 last_error)。"""
    rows = await catalog_service.image_node_rows(session, image_id)
    return [ImageNodeCacheOut.model_validate(r) for r in rows]


# ---------- 全局实例(角色:admin / ops) ----------


@router.get("/instances", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_instances(
    session: DbSession, status: str | None = None, user_id: int | None = None
) -> list[AdminInstanceOut]:
    instances = await orchestrator_service.admin_list_instances(
        session, status_filter=status, user_id=user_id
    )
    return [AdminInstanceOut.model_validate(i) for i in instances]


@router.post("/instances/{uuid}/force-stop", dependencies=[require_roles("ops")])
async def admin_force_stop(
    uuid: str, body: AdminForceStopRequest, session: DbSession, request: Request
) -> InstanceOut:
    """强制停止(原因必填)。"""
    instance = await orchestrator_service.admin_force_stop(session, uuid, reason=body.reason)
    set_audit_target(request, f"instance:{uuid}", detail={"reason": body.reason})
    return InstanceOut.model_validate(instance)


# ---------- 财务对账(角色:finance / admin) ----------


@router.get("/reconciliation", dependencies=[require_roles("finance", "readonly")])
async def reconciliation(session: DbSession, day: str) -> ReconciliationOut:
    """日对账:事件计费 vs 指标估算 + diff%(>2% 列差异实例)。"""
    from datetime import UTC, datetime

    from app.core.errors import AppError, ErrorCode

    try:
        d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="adminapi.badDayFormat") from exc
    report = await metering_service.reconciliation_report(session, d)
    return ReconciliationOut.model_validate(report)


@router.get("/alerts", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_alerts(session: DbSession) -> list[AdminAlertOut]:
    """管理端告警流(总览右栏数据源)。"""
    from app.modules.notify import service as notify_service

    rows = await notify_service.admin_alert_stream(session)
    return [
        AdminAlertOut(
            id=r.id,
            type=r.type,
            title=r.title,
            content=r.content,
            severity=r.severity,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]


# ---------- 租户管理(角色:admin / ops) ----------


class TenantFreezeRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=256)


@router.get("/tenants", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_tenants(session: DbSession) -> list[TenantOut]:
    from app.modules.account import service as account_service
    from app.modules.billing import service as billing_service

    users = await account_service.admin_list_users(session)
    balances = await billing_service.balances_by_user(session)
    consumed = await billing_service.consumed_by_user(session)
    stats = await orchestrator_service.instance_disk_stats_by_user(session)
    out = []
    for u in users:
        st = stats.get(u.id, {"instances": 0, "disk_gb": 0})
        out.append(
            TenantOut(
                id=u.id,
                phone_masked=u.phone[:3] + "****" + u.phone[-4:],
                status=u.status,
                balance=format(balances.get(u.id, 0), "f"),
                total_consumed=format(consumed.get(u.id, 0), "f"),
                instances=st["instances"],
                disk_gb=st["disk_gb"],
                created_at=u.created_at.isoformat(),
            )
        )
    return out


@router.post("/tenants/{user_id}/freeze", dependencies=[require_roles("ops")])
async def admin_freeze_tenant(
    user_id: int, body: TenantFreezeRequest, session: DbSession, request: Request
) -> TenantStatusOut:
    from app.modules.account import service as account_service

    user = await account_service.admin_set_user_status(session, user_id, "frozen")
    set_audit_target(request, f"user:{user_id}", detail={"reason": body.reason})
    return TenantStatusOut(id=user.id, status=user.status)


@router.post("/tenants/{user_id}/unfreeze", dependencies=[require_roles("ops")])
async def admin_unfreeze_tenant(
    user_id: int, body: TenantFreezeRequest, session: DbSession, request: Request
) -> TenantStatusOut:
    from app.modules.account import service as account_service

    user = await account_service.admin_set_user_status(session, user_id, "active")
    set_audit_target(request, f"user:{user_id}", detail={"reason": body.reason})
    return TenantStatusOut(id=user.id, status=user.status)


# ---------- 节点注册(读:ops/readonly,写:ops,admin 恒许) ----------


class EnrollmentRevokeRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=256)


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
) -> dict[str, Any]:
    """节点每卡曲线(DCGM per-GPU)+ 24h XID 计数;断源 available=false(200)。

    节点存在性不做强校验:对不存在节点的查询自然返回空序列,无信息泄漏面
    (仅管理端角色可达)。响应附 grafana_url(可选深挖外链)。
    """

    out = await metering_service.node_gpu_metrics(node_name, range)
    cfg = await get_effective_platform_config(session)
    out["grafana_url"] = cfg.get("grafana_url") or None
    return out


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
    reason: str = Field(min_length=2, max_length=256)


class NodeCordonOut(BaseModel):
    node_name: str
    unschedulable: bool
    queued: bool = True  # 经 outbox 异步执行,列表轮询看生效


async def _cordon(
    node_name: str, body: NodeCordonRequest, session: DbSession, request: Request, on: bool
) -> NodeCordonOut:
    names = {n.name for n in await orchestrator_service.cluster_nodes()}  # 只读校验,非副作用
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
    """超卖报表:各池 物理容量 / 已售份额 / 实际超卖率 / 近 24h 真实利用率。"""
    nodes = await orchestrator_service.cluster_nodes()
    physical: dict[str, int] = {}
    for n in nodes:
        physical[n.pool_label] = physical.get(n.pool_label, 0) + n.gpu_total
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
                physical_gpus=total,
                sold_share=sold_share,
                oversell_ratio=round(sold_share / total, 3) if total else 0.0,
                util_avg_24h=round(util_sum[pool] / hours, 1) if hours else None,
            )
        )
    return out


# ---------- 调账(发起:finance;复核:另一名 finance/admin) ----------


class AdjustmentCreate(BaseModel):
    user_id: int
    amount: str  # 带符号金额字符串,如 "-10.00" / "25.50"
    reason: str = Field(min_length=2, max_length=256)


class AdjustmentReview(BaseModel):
    approve: bool
    comment: str | None = Field(default=None, max_length=256)


@router.get("/adjustments", dependencies=[require_roles("finance", "readonly")])
async def admin_list_adjustments(session: DbSession) -> list[AdjustmentOut]:
    rows = await service.list_adjustments(session)
    return [
        AdjustmentOut(
            id=r.id,
            user_id=r.user_id,
            amount=format(r.amount, "f"),
            reason=r.reason,
            status=r.status,
            created_by=r.created_by,
            reviewed_by=r.reviewed_by,
            review_comment=r.review_comment,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]


@router.post("/adjustments", status_code=201)
async def admin_create_adjustment(
    body: AdjustmentCreate,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles("finance"),
) -> AdjustmentStatusOut:
    adj = await service.create_adjustment(
        session,
        user_id=body.user_id,
        amount=body.amount,
        reason=body.reason,
        created_by=admin.id,
    )
    set_audit_target(request, f"adjustment:{adj.id}", detail={"amount": str(adj.amount)})
    return AdjustmentStatusOut(id=adj.id, status=adj.status)


@router.post("/adjustments/{adjustment_id}/review")
async def admin_review_adjustment(
    adjustment_id: int,
    body: AdjustmentReview,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles("finance"),
) -> AdjustmentStatusOut:
    adj = await service.review_adjustment(
        session,
        adjustment_id,
        approve=body.approve,
        reviewer_id=admin.id,
        comment=body.comment,
    )
    set_audit_target(request, f"adjustment:{adj.id}", detail={"approve": body.approve})
    return AdjustmentStatusOut(id=adj.id, status=adj.status)


# ---------- 审计检索(所有已认证管理角色可读) ----------


@router.get("/audit", dependencies=[require_roles("readonly", "ops", "finance")])
async def admin_audit_log(
    session: DbSession, actor_type: str | None = None, limit: int = 100
) -> list[AuditLogOut]:
    from sqlalchemy import select as sa_select

    from app.core.audit import AuditLog

    stmt = sa_select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 500))
    if actor_type:
        stmt = stmt.where(AuditLog.actor_type == actor_type)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        AuditLogOut(
            id=r.id,
            actor_type=r.actor_type,
            actor_id=r.actor_id,
            action=r.action,
            target=r.target,
            ip=str(r.ip) if r.ip else None,
            result=r.result,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]


# ---------- 财务流水(角色:finance) ----------


@router.get("/orders", dependencies=[require_roles("finance", "readonly")])
async def admin_list_orders(session: DbSession, status: str | None = None) -> list[AdminOrderOut]:
    from app.modules.billing import service as billing_service

    rows = await billing_service.admin_list_orders(session, status)
    return [AdminOrderOut.model_validate(r) for r in rows]


# ---------- 收入报表(总览 KPI) ----------


@router.get("/reports/revenue", dependencies=[require_roles("ops", "finance", "readonly")])
async def revenue_report(session: DbSession, tz_offset_minutes: int = 0) -> RevenueReportOut:
    """今日/本月消费额(营收口径 = ledger consume 绝对值)与新注册数。本地日界经 tz_offset。"""
    from app.modules.account import service as account_service
    from app.modules.billing import service as billing_service

    revenue = await billing_service.revenue_summary(session, tz_offset_minutes=tz_offset_minutes)
    signups = await account_service.signup_counts(session, tz_offset_minutes=tz_offset_minutes)
    return RevenueReportOut.model_validate({**revenue, **signups})


# ---------- 系统设置:策略参数在线调整(角色:ops) ----------


@router.get("/policies", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_get_policies(session: DbSession) -> PoliciesAdminOut:
    """当前生效策略 + 取值范围(供设置屏渲染)+ DB 覆盖项。"""
    from dataclasses import asdict

    from app.core.policies import POLICY_SPECS, get_effective_policies, list_policy_overrides

    effective = await get_effective_policies(session)
    return PoliciesAdminOut.model_validate(
        {
            "effective": {k: str(v) for k, v in asdict(effective).items()},
            "overrides": await list_policy_overrides(session),
            "specs": {
                k: {"kind": v[0], "min": str(v[1]), "max": str(v[2])}
                for k, v in POLICY_SPECS.items()
            },
        }
    )


class PolicyUpdateRequest(BaseModel):
    updates: dict[str, str] = Field(min_length=1)
    reason: str = Field(min_length=2, max_length=200)


@router.put("/policies", dependencies=[require_roles("ops")])
async def admin_update_policies(
    body: PolicyUpdateRequest, session: DbSession, request: Request
) -> UpdatedKeysOut:
    """在线调整策略参数(即时生效,GET /policies 与计费/回收同步跟随)。"""
    from app.core.errors import AppError, ErrorCode
    from app.core.policies import set_policy_overrides

    try:
        await set_policy_overrides(session, body.updates)
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, str(exc)) from exc
    await session.commit()
    set_audit_target(request, "policies", detail={"updates": body.updates, "reason": body.reason})
    return UpdatedKeysOut(updated=sorted(body.updates))


# ---------- 平台配置:支付/短信/实名/合规(角色:仅 admin —— 渠道凭据不下放 ops) ----------


@router.get("/platform-config", dependencies=[require_roles()])
async def admin_get_platform_config(session: DbSession) -> PlatformConfigOut:
    """分组配置项:生效值 + 来源(env 默认/DB 覆盖)。secret 永不回明文,只回尾 4 位预览。"""
    from app.core.platform_config import (
        SETTING_SPECS,
        get_effective_platform_config,
        list_platform_overrides,
        secret_preview,
    )

    eff = await get_effective_platform_config(session)
    overrides = await list_platform_overrides(session)
    items = []
    for key, spec in SETTING_SPECS.items():
        value = eff[key]
        row = overrides.get(key)
        items.append(
            PlatformConfigItemOut(
                key=key,
                group=spec.group,
                kind=spec.kind,
                choices=list(spec.choices),
                hint=spec.hint,
                source="override" if row is not None else ("env" if value else "unset"),
                configured=bool(value),
                value=None if spec.kind == "secret" else value,
                preview=secret_preview(value) if spec.kind == "secret" and value else None,
                updated_at=row.updated_at.isoformat() if row is not None else None,
            )
        )
    return PlatformConfigOut(items=items)


class PlatformConfigUpdateRequest(BaseModel):
    updates: dict[str, str] = Field(min_length=1)
    reason: str = Field(min_length=2, max_length=200)


@router.put("/platform-config")
async def admin_update_platform_config(
    body: PlatformConfigUpdateRequest,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles(),
) -> UpdatedKeysOut:
    """在线配置渠道凭据与合规信息(空串=清除覆盖,回退 env 默认)。审计只落键名不落值。"""
    from app.core.errors import AppError, ErrorCode
    from app.core.platform_config import set_platform_settings

    try:
        await set_platform_settings(session, body.updates, updated_by=admin.id)
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, str(exc)) from exc
    await session.commit()
    set_audit_target(
        request, "platform_config", detail={"keys": sorted(body.updates), "reason": body.reason}
    )
    return UpdatedKeysOut(updated=sorted(body.updates))


class SmsTestRequest(BaseModel):
    phone: str = Field(pattern=r"^1\d{10}$")


@router.post("/platform-config/test-sms", dependencies=[require_roles()])
async def admin_test_sms(body: SmsTestRequest, session: DbSession, request: Request) -> SmsTestOut:
    """按当前生效短信配置实发一条验证码短信(上线前联调用;有限流,过审计)。"""
    import secrets

    from app.core.errors import AppError, ErrorCode
    from app.core.platform_config import get_effective_platform_config
    from app.core.ratelimit import check_rate_limit
    from app.core.sms import SmsError, get_sms_channel

    await check_rate_limit("admin:test-sms", max_attempts=10, window_seconds=3600.0)
    cfg = await get_effective_platform_config(session)
    channel = await get_sms_channel(session)
    code = f"{secrets.randbelow(10**6):06d}"
    try:
        await channel.send(body.phone, cfg["sms_template_verify"] or "", {"code": code})
    except SmsError as exc:
        raise AppError(
            ErrorCode.SMS_SEND_FAILED,
            key="adminapi.smsTestFailed",
            params={"message": str(exc)},
            http_status=502,
        ) from exc
    set_audit_target(request, f"test-sms:{body.phone}")
    return SmsTestOut(ok=True, provider=cfg["sms_provider"])


# ---------- 公告发布(角色:ops) ----------


class AnnouncementCreate(BaseModel):
    title: str = Field(min_length=2, max_length=128)
    content: str = Field(min_length=2, max_length=2000)


@router.post("/announcements", dependencies=[require_roles("ops")], status_code=201)
async def admin_publish_announcement(
    body: AnnouncementCreate, session: DbSession, request: Request
) -> AnnouncementResultOut:
    """公告群发(站内信 announcement 类型,全部 active 用户)。"""
    from app.modules.notify import service as notify_service

    reached = await notify_service.publish_announcement(
        session, title=body.title, content=body.content
    )
    set_audit_target(request, "announcement", detail={"title": body.title, "reached": reached})
    return AnnouncementResultOut(reached=reached)


# ---------- outbox 死信(角色:ops) ----------


@router.get("/outbox/dead", dependencies=[require_roles("ops", "readonly")])
async def admin_list_dead_tasks(session: DbSession) -> list[DeadTaskOut]:
    """死信任务列表:重试耗尽的编排任务在此可见(同时有 outbox_dead_total 指标接告警)。"""
    from sqlalchemy import select as sa_select

    from app.core.outbox import OutboxTask

    rows = (
        (
            await session.execute(
                sa_select(OutboxTask)
                .where(OutboxTask.status == "dead")
                .order_by(OutboxTask.id.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return [
        DeadTaskOut(
            id=r.id,
            type=r.type,
            payload=r.payload,
            retries=r.retries,
            last_error=r.last_error,
            created_at=r.created_at.isoformat(),
            updated_at=r.updated_at.isoformat(),
        )
        for r in rows
    ]


class OutboxDiscardRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=200)


@router.post("/outbox/{task_id}/retry", dependencies=[require_roles("ops")])
async def admin_retry_dead_task(
    task_id: int, session: DbSession, request: Request
) -> OutboxTaskStatusOut:
    """重放死信:置回 pending 交还 worker(handler 幂等,重放安全)。"""
    from app.core.errors import AppError, ErrorCode
    from app.core.outbox import OutboxTask
    from app.core.timeutil import now_utc

    task = await session.get(OutboxTask, task_id)
    if task is None:
        raise AppError(ErrorCode.NOT_FOUND, key="adminapi.taskNotFound", http_status=404)
    if task.status != "dead":
        raise AppError(
            ErrorCode.CONFLICT,
            key="adminapi.taskStateNotReplayable",
            params={"status": task.status},
        )
    task.status = "pending"
    task.retries = 0
    task.next_retry_at = now_utc()
    task.locked_by = None
    task.locked_at = None
    await session.commit()
    set_audit_target(request, f"outbox:{task_id}", detail={"type": task.type})
    return OutboxTaskStatusOut(id=task.id, status=task.status)


@router.post("/outbox/{task_id}/discard", dependencies=[require_roles("ops")])
async def admin_discard_dead_task(
    task_id: int, body: OutboxDiscardRequest, session: DbSession, request: Request
) -> OutboxTaskStatusOut:
    """忽略死信(需原因):确认该任务不再需要执行(如实例已人工处理)。"""
    from app.core.errors import AppError, ErrorCode
    from app.core.outbox import OutboxTask

    task = await session.get(OutboxTask, task_id)
    if task is None:
        raise AppError(ErrorCode.NOT_FOUND, key="adminapi.taskNotFound", http_status=404)
    if task.status != "dead":
        raise AppError(
            ErrorCode.CONFLICT, key="adminapi.taskStateNotIgnorable", params={"status": task.status}
        )
    task.status = "discarded"
    await session.commit()
    set_audit_target(request, f"outbox:{task_id}", detail={"reason": body.reason})
    return OutboxTaskStatusOut(id=task.id, status=task.status)


@router.get("/finance/anomalies", dependencies=[require_roles("finance", "readonly")])
async def admin_payment_anomalies(session: DbSession) -> list[PaymentAnomalyOut]:
    """异常清单:疑似丢回调 / 近 48h 关单 / 负余额钱包。"""
    from app.modules.billing import service as billing_service

    rows = await billing_service.list_payment_anomalies(session)
    return [PaymentAnomalyOut.model_validate(r) for r in rows]


@router.post("/finance/orders/{order_no}/verify", dependencies=[require_roles("finance")])
async def admin_verify_order(order_no: str, session: DbSession, request: Request) -> OrderVerifyOut:
    """向渠道核验订单状态与金额(补单前置;渠道结果是唯一事实源)。"""
    from app.modules.billing import service as billing_service

    result = await billing_service.verify_order(session, order_no)
    set_audit_target(
        request, f"order:{order_no}", detail={"channel_status": result["channel_status"]}
    )
    return OrderVerifyOut.model_validate(result)


class OrderBackfillRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=200)


@router.post("/finance/orders/{order_no}/backfill", dependencies=[require_roles("finance")])
async def admin_backfill_order(
    order_no: str, body: OrderBackfillRequest, session: DbSession, request: Request
) -> OrderBackfillOut:
    """人工补单:服务端实时向渠道核验已支付且金额一致才入账。"""
    from app.modules.billing import service as billing_service

    order = await billing_service.backfill_order(session, order_no)
    set_audit_target(
        request, f"order:{order_no}", detail={"reason": body.reason, "amount": str(order.amount)}
    )
    return OrderBackfillOut(order_no=order.order_no, status=order.status)
