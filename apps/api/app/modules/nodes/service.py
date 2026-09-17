"""Node enrollment, pool management and the cluster capability view.

Enrollment and progress tokens are stored as HMAC digests only; bootstrap requires pending, progress
reports use their own token.
Anonymous resolution returns 404 for invalid, expired or terminal tokens alike.
"""

import asyncio
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.config import get_settings
from app.core.crypto import hash_node_token, hash_node_token_candidates
from app.core.errors import AppError, ErrorCode, conflict, not_found
from app.core.gpu_adapter import POOL_CPU, POOL_HAMI, POOL_KATA, POOL_MIG, SWITCHABLE_POOLS
from app.core.gpu_models import model_matches, supports_mig, supports_passthrough
from app.core.idempotency import find_replay
from app.core.k8s import get_orchestrator
from app.core.k8s.base import (
    DATA_DISK_STORAGE_CLASS,
    INSTANCE_DISK_STORAGE_CLASS,
    POOL_NODE_LABEL,
    ClusterProbe,
    ComponentFact,
    ComponentFacts,
    component_facts_from_json,
    component_facts_to_json,
    derive_distro,
)
from app.core.logging import get_logger
from app.core.outbox import enqueue
from app.core.platform_config import RuntimeConfig, get_runtime_config
from app.core.ratelimit import check_rate_limit
from app.core.registry import parse_proxy_projects
from app.core.timeutil import now_utc
from app.modules.nodes.models import ClusterStatus, NodeEnrollment, NodeSpec
from app.modules.nodes.schemas import (
    ClusterComponentOut,
    ComponentFactOut,
    ComponentKey,
    ComponentObjectOut,
    ComponentProbeOut,
    ComponentStateOut,
    EnrollmentCreate,
)
from app.modules.orchestrator import queries as orchestrator_queries

logger = get_logger(__name__)

TOKEN_PREFIX = "sdln_"
PROGRESS_TOKEN_PREFIX = "sdlp_"
TERMINAL_STATUSES = frozenset({"joined", "failed", "expired", "revoked"})
REGENERATABLE_STATUSES = frozenset({"pending", "expired", "failed"})

_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"installing", "expired", "revoked", "failed"}),
    "installing": frozenset({"rebooting", "joining", "failed", "revoked", "joined", "expired"}),
    "rebooting": frozenset({"installing", "joining", "failed", "revoked", "joined", "expired"}),
    "joining": frozenset({"joined", "failed", "revoked", "expired"}),
    "joined": frozenset({"revoked"}),
    "failed": frozenset({"revoked"}),
    "expired": frozenset({"revoked"}),
}


def transition_enrollment(
    enrollment: NodeEnrollment,
    new_status: str,
    *,
    phase: str | None = None,
    error: str | None = None,
) -> None:
    """Move the enrollment status along an allowed edge, 409 on an illegal edge; same status = no
    update; no commit."""
    if enrollment.status == new_status:
        return
    allowed = _ALLOWED_TRANSITIONS.get(enrollment.status, frozenset())
    if new_status not in allowed:
        raise conflict(
            key="nodes.enrollTransition",
            params={"from": enrollment.status, "to": new_status},
        )
    enrollment.status = new_status
    if phase is not None:
        enrollment.phase = phase
    if error is not None:
        enrollment.error = error
    if new_status == "joined":
        enrollment.joined_at = now_utc()
    logger.info(
        "node_enrollment_transition",
        enrollment_id=enrollment.id,
        to=new_status,
        phase=enrollment.phase,
    )


def _new_token(prefix: str = TOKEN_PREFIX) -> tuple[str, str]:
    token = prefix + secrets.token_urlsafe(32)
    return token, hash_node_token(token)


def enrollment_commands(token: str) -> tuple[str, str]:
    """The two enrollment command forms: piped / download-then-review. The token goes through stdin
    into a 0600 file, never into argv."""
    base = get_settings().public_base_url.rstrip("/")
    script_url = f"{base}/api/v1/node-enroll/script"
    token_file = "/run/superdl-join.token"
    load = f"echo '{token}' | sudo sh -c 'umask 077; cat > {token_file}; "
    cleanup = f"; s=$?; rm -f {token_file}; exit $s'"
    curl_cmd = f"{load}curl -fsSL {script_url} | bash -s -- --token-file {token_file}{cleanup}"
    wget_cmd = (
        f"wget -qO node-join.sh {script_url} && "
        f"{load}bash node-join.sh --token-file {token_file}{cleanup}"
    )
    return curl_cmd, wget_cmd


def pool_matches(enrolled_pool: str, observed_pool: str | None) -> bool:
    """Whether the pool label matches; unlabeled or unknown returns False."""
    return bool(observed_pool) and observed_pool != "unknown" and observed_pool == enrolled_pool


async def require_cluster_config(session: AsyncSession) -> RuntimeConfig:
    """Precondition for creating an enrollment token: the cluster group must be configured,
    otherwise 409."""
    cfg = await get_runtime_config(session)
    if not cfg.cluster_server_url or not cfg.cluster_join_token:
        raise conflict(key="nodes.clusterNotConfigured")
    return cfg


async def create_enrollment(
    session: AsyncSession,
    data: EnrollmentCreate,
    *,
    created_by: int,
    idempotency_key: str | None,
) -> tuple[NodeEnrollment, str]:
    """Create an enrollment token. Idempotency-Key replay: no new row, the row's token is rotated
    and
    returned."""
    await require_cluster_config(session)
    if idempotency_key:
        existing = await find_replay(
            session,
            NodeEnrollment,
            owner_col=NodeEnrollment.created_by,
            owner_id=created_by,
            key=idempotency_key,
        )
        if existing is not None:
            if existing.status not in REGENERATABLE_STATUSES:
                raise conflict(key="nodes.regenerateNotAllowed", params={"status": existing.status})
            token, existing.token_hash = _new_token()
            existing.progress_token_hash = None
            await session.commit()
            await session.refresh(existing)
            return existing, token

    enrollment, token = _add_enrollment(
        session, data, created_by=created_by, idempotency_key=idempotency_key
    )
    await session.commit()
    await session.refresh(enrollment)
    return enrollment, token


def _add_enrollment(
    session: AsyncSession,
    data: EnrollmentCreate,
    *,
    created_by: int,
    idempotency_key: str | None,
) -> tuple[NodeEnrollment, str]:
    """Add an enrollment row, returning the row and the token plaintext visible this once; no
    commit."""
    token, token_hash = _new_token()
    enrollment = NodeEnrollment(
        token_hash=token_hash,
        pool=data.pool,
        hostname=data.hostname,
        note=data.note,
        nvme_devices=data.nvme_devices,
        expires_at=now_utc() + timedelta(hours=data.ttl_hours),
        created_by=created_by,
        idempotency_key=idempotency_key,
    )
    session.add(enrollment)
    return enrollment, token


async def list_enrollments(
    session: AsyncSession, *, active_only: bool = False
) -> list[NodeEnrollment]:
    rows = list(
        (await session.execute(select(NodeEnrollment).order_by(NodeEnrollment.id.desc()))).scalars()
    )
    if not active_only:
        return rows
    now = now_utc()
    out: list[NodeEnrollment] = []
    for r in rows:
        if r.status == "revoked":
            continue
        if r.status == "joined":
            continue
        if r.status == "expired" and r.updated_at < now - timedelta(days=7):
            continue
        out.append(r)
    return out


async def get_enrollment(session: AsyncSession, enrollment_id: int) -> NodeEnrollment:
    row = await session.get(NodeEnrollment, enrollment_id)
    if row is None:
        raise not_found(key="nodes.enrollmentNotFound")
    return row


async def regenerate_enrollment(
    session: AsyncSession, enrollment_id: int, *, ttl_hours: int = 24
) -> tuple[NodeEnrollment, str]:
    """Issue a new token: pending/expired/failed only."""
    await require_cluster_config(session)
    enrollment = await get_enrollment(session, enrollment_id)
    if enrollment.status not in REGENERATABLE_STATUSES:
        raise conflict(key="nodes.regenerateNotAllowed", params={"status": enrollment.status})
    token, enrollment.token_hash = _new_token()
    enrollment.progress_token_hash = None
    enrollment.status = "pending"
    enrollment.phase = None
    enrollment.error = None
    enrollment.expires_at = now_utc() + timedelta(hours=ttl_hours)
    await session.commit()
    await session.refresh(enrollment)
    return enrollment, token


async def revoke_enrollment(session: AsyncSession, enrollment_id: int) -> NodeEnrollment:
    enrollment = await get_enrollment(session, enrollment_id)
    if enrollment.status in TERMINAL_STATUSES:
        raise conflict(key="nodes.alreadyTerminal", params={"status": enrollment.status})
    transition_enrollment(enrollment, "revoked")
    await session.commit()
    await session.refresh(enrollment)
    return enrollment


async def _assert_node_empty(session: AsyncSession, node_name: str) -> None:
    """409 when the node has unreleased instances, stopped/frozen/failed included."""
    active = await orchestrator_queries.count_active_instances_on_node(session, node_name)
    if active:
        raise conflict(key="nodes.nodeHasInstances", params={"count": active})


async def switch_node_pool(
    session: AsyncSession,
    node_name: str,
    *,
    pool: str,
    reason: str,
) -> tuple[NodeSpec, str]:
    """Under the inventory row lock, check the node is empty and the target pool and runtime are
    valid, write cordon and desired pool in one transaction, enqueue the switch and commit.

    Returns (inventory row, previous desired or observed pool); only GPU pools within
    SWITCHABLE_POOLS.
    """
    row = (
        await session.execute(
            select(NodeSpec).where(NodeSpec.node_name == node_name).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise not_found(key="nodes.nodeNotFound")
    if pool not in SWITCHABLE_POOLS:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="nodes.poolNotSwitchable",
            params={"pools": " / ".join(SWITCHABLE_POOLS)},
        )
    current = row.desired_pool or row.pool_label
    if current == pool:
        raise conflict(key="nodes.poolUnchanged", params={"pool": pool})
    # the observed card count drops to 0 when the target pool's components fail to start, so a node
    # already in a GPU pool must keep a way back
    if current == POOL_CPU or (row.gpu_count <= 0 and current not in SWITCHABLE_POOLS):
        raise conflict(key="nodes.poolIncompatible")
    if pool == POOL_MIG and not supports_mig(row.gpu_model):
        raise conflict(key="nodes.poolMigUnsupported", params={"model": row.gpu_model or "unknown"})
    if pool == POOL_KATA and not supports_passthrough(row.gpu_model):
        raise conflict(
            key="nodes.poolPassthroughUnsupported", params={"model": row.gpu_model or "unknown"}
        )
    await _assert_node_empty(session, node_name)
    await require_pool_runtime(session, pool)

    row.desired_unschedulable = True
    row.desired_pool = pool
    enqueue(
        session,
        "node.switch_pool",
        {"node_name": node_name, "from_pool": current, "to_pool": pool, "reason": reason},
    )
    await session.commit()
    await session.refresh(row)
    logger.warning(
        "node_pool_switch_requested",
        node=node_name,
        from_pool=current,
        to_pool=pool,
        reason=reason,
    )
    return row, current or ""


async def decommission_node(
    session: AsyncSession, node_name: str, *, reason: str, force: bool = False
) -> int:
    """Under the inventory row lock: cordon, revoke the node's enrollments and enqueue the
    decommission in one transaction, commit, return the revoked count.

    Unreleased instances block decommissioning, force=True skips that check; the cluster join token
    and kubelet certificate must be revoked separately.
    """
    row = (
        await session.execute(
            select(NodeSpec).where(NodeSpec.node_name == node_name).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise not_found(key="nodes.nodeNotFound")
    if not force:
        await _assert_node_empty(session, node_name)
    row.desired_unschedulable = True
    enrollments = list(
        (
            await session.execute(
                select(NodeEnrollment).where(
                    NodeEnrollment.node_name == node_name,
                    NodeEnrollment.status != "revoked",
                )
            )
        ).scalars()
    )
    for enrollment in enrollments:
        transition_enrollment(enrollment, "revoked", error=f"node decommissioned: {reason}")
    enqueue(session, "node.decommission", {"node_name": node_name, "reason": reason})
    await session.commit()
    logger.warning(
        "node_decommissioned",
        node=node_name,
        revoked_enrollments=len(enrollments),
        reason=reason,
        force=force,
    )
    return len(enrollments)


async def request_cordon(
    session: AsyncSession, node_name: str, *, unschedulable: bool, reason: str
) -> None:
    """Update the schedulable desired state of an existing node and commit a node.cordon task."""
    row = (
        await session.execute(select(NodeSpec).where(NodeSpec.node_name == node_name))
    ).scalar_one_or_none()
    if row is not None:
        row.desired_unschedulable = unschedulable
    enqueue(
        session,
        "node.cordon",
        {"node_name": node_name, "unschedulable": unschedulable, "reason": reason},
    )
    await session.commit()


def _check_usable(row: NodeEnrollment | None) -> NodeEnrollment:
    """Shared gate: invalid / terminal / expired → 404; the request path only rejects, never
    transitions; the reconciler marks expired."""
    if row is None or row.status in TERMINAL_STATUSES or row.expires_at < now_utc():
        raise not_found()
    return row


async def _resolve_by_hash(
    session: AsyncSession,
    column: InstrumentedAttribute[str | None],
    token: str,
    *,
    for_update: bool = False,
) -> NodeEnrollment | None:
    """Fetch the row by HMAC candidates (crypto.py); for_update takes the row lock."""
    stmt = select(NodeEnrollment).where(column.in_(hash_node_token_candidates(token))).limit(1)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def _resolve_token(session: AsyncSession, token: str) -> NodeEnrollment:
    """Enrollment token (for bootstrap): fetch by hash with FOR UPDATE, so of concurrent bootstraps
    only one consumes it."""
    row = await _resolve_by_hash(session, NodeEnrollment.token_hash, token, for_update=True)
    return _check_usable(row)


async def _resolve_progress_token(session: AsyncSession, token: str) -> NodeEnrollment:
    """Progress token (for progress reports): fetched by progress_token_hash only, the enrollment
    token cannot report."""
    row = await _resolve_by_hash(session, NodeEnrollment.progress_token_hash, token)
    return _check_usable(row)


async def bootstrap(
    session: AsyncSession,
    token: str,
    *,
    hostname: str,
    os_info: dict[str, Any],
    gpu_details: list[dict[str, Any]],
    client_ip: str | None,
) -> tuple[NodeEnrollment, RuntimeConfig, str]:
    """Check the pending enrollment and hostname, move to installing and commit; return the
    enrollment, runtime configuration and progress token.

    A hostname mismatch commits failed and returns 409; the caller must restrict the configuration
    fields handed out.
    """
    row = await _resolve_token(session, token)
    if row.status != "pending":
        raise not_found()
    if row.hostname != hostname:
        transition_enrollment(
            row,
            "failed",
            error=f"hostname mismatch: expected {row.hostname}, reported {hostname}"
            " (token misuse guard)",
        )
        await session.commit()
        raise conflict(key="nodes.hostnameMismatch")
    row.node_name = hostname
    row.reported_ip = client_ip
    row.os_info = os_info
    row.gpu_info = gpu_details
    row.last_report_at = now_utc()
    progress_token, row.progress_token_hash = _new_token(PROGRESS_TOKEN_PREFIX)
    transition_enrollment(row, "installing", phase="bootstrap")
    cfg = await get_runtime_config(session)
    await session.commit()
    await session.refresh(row)
    return row, cfg, progress_token


async def report_progress(
    session: AsyncSession,
    token: str,
    *,
    phase: str,
    state: str,
    message: str | None,
    driver_version: str | None = None,
    cuda_version: str | None = None,
) -> NodeEnrollment:
    row = await _resolve_progress_token(session, token)
    if row.status == "pending":
        raise not_found()
    row.phase = phase
    row.last_report_at = now_utc()
    versions = {
        k: v for k, v in (("driver_version", driver_version), ("cuda_version", cuda_version)) if v
    }
    if versions:
        row.os_info = {**(row.os_info or {}), **versions}
    if state == "failed":
        transition_enrollment(row, "failed", phase=phase, error=message or f"{phase} failed")
    elif state == "rebooting":
        transition_enrollment(row, "rebooting", phase=phase)
    elif row.status == "rebooting" and state in ("running", "ok"):
        transition_enrollment(row, "installing", phase=phase)
    if phase == "agent_start" and state == "ok" and row.status == "installing":
        transition_enrollment(row, "joining", phase=phase)
    await session.commit()
    await session.refresh(row)
    return row


async def list_node_specs(session: AsyncSession) -> list[NodeSpec]:
    """The full inventory (Missing / unlabeled included), data source of the admin nodes page."""
    rows = (await session.execute(select(NodeSpec).order_by(NodeSpec.node_name))).scalars()
    return list(rows)


async def get_node_spec(session: AsyncSession, node_name: str) -> NodeSpec | None:
    rows = (
        await session.execute(select(NodeSpec).where(NodeSpec.node_name == node_name))
    ).scalars()
    return next(iter(rows), None)


async def ready_specs(session: AsyncSession) -> list[NodeSpec]:
    """Ready nodes (listing check / capacity column basis)."""
    rows = (await session.execute(select(NodeSpec).where(NodeSpec.status == "Ready"))).scalars()
    return list(rows)


def matching_specs(
    specs: Iterable[NodeSpec], pool_label: str, wanted_model: str | None
) -> list[NodeSpec]:
    """Inventory rows matching "pool × canonical model" regardless of status (the single place
    deciding "same physical pool");
    wanted_model None never matches."""
    return [
        s for s in specs if s.pool_label == pool_label and model_matches(wanted_model, s.gpu_model)
    ]


def pool_specs(specs: Iterable[NodeSpec], pool_label: str) -> list[NodeSpec]:
    """Inventory rows matching the pool label, not filtered by model or status."""
    return [s for s in specs if s.pool_label == pool_label]


async def gpu_model_aggregates(session: AsyncSession) -> list["GpuModelAggregate"]:
    """Inventory aggregated by canonical × pool (data source of "create SKU from cluster
    resources");
    unrecognised models go into the gpu_model=None bucket."""
    rows = await list_node_specs(session)
    agg: dict[tuple[str | None, str | None], GpuModelAggregate] = {}
    for r in rows:
        key = (r.gpu_model, r.pool_label)
        item = agg.get(key)
        if item is None:
            item = GpuModelAggregate(
                gpu_model=r.gpu_model, gpu_model_raw=r.gpu_model_raw, pool_label=r.pool_label
            )
            agg[key] = item
        item.node_count += 1
        item.gpu_total += r.gpu_count
        if r.status == "Ready":
            item.ready_gpu_total += r.gpu_count
            item.ready_gpu_free += max(0, r.gpu_count - r.gpu_used)
            if r.gpu_count > 0:
                per_vcpu, per_mem = r.vcpu // r.gpu_count, r.mem_gb // r.gpu_count
                item.vcpu_per_gpu = min(item.vcpu_per_gpu or per_vcpu, per_vcpu)
                item.mem_gb_per_gpu = min(item.mem_gb_per_gpu or per_mem, per_mem)
        if r.vram_gb:
            item.vram_gb = max(item.vram_gb, r.vram_gb)
    return sorted(
        agg.values(), key=lambda x: (x.gpu_model is None, str(x.gpu_model), str(x.pool_label))
    )


@dataclass
class GpuModelAggregate:
    gpu_model: str | None
    gpu_model_raw: str | None
    pool_label: str | None
    node_count: int = 0
    gpu_total: int = 0
    ready_gpu_total: int = 0
    ready_gpu_free: int = 0
    vram_gb: int = 0
    vcpu_per_gpu: int = 0
    mem_gb_per_gpu: int = 0


async def save_cluster_probe(session: AsyncSession, probe: ClusterProbe) -> ClusterStatus:
    """Upsert the probe result into the single row (id=1); the caller commits."""
    row = await session.get(ClusterStatus, 1)
    if row is None:
        row = ClusterStatus(id=1)
        session.add(row)
    row.api_reachable = probe.api_reachable
    row.k8s_version = probe.k8s_version
    row.distro = probe.distro
    row.hami_ready = probe.hami_ready
    row.dcgm_present = probe.dcgm_present
    row.kps_present = probe.kps_present
    row.gpu_operator_present = probe.gpu_operator_present
    row.kata_runtimeclass = probe.kata_runtimeclass
    row.nvidia_runtimeclass = probe.nvidia_runtimeclass
    row.gateway_ready = probe.gateway_ready
    row.cert_manager_ready = probe.cert_manager_ready
    row.nodes_ready = probe.nodes_ready
    row.nodes_total = probe.nodes_total
    row.storage_classes = list(probe.storage_classes)
    row.pools = dict(probe.pools)
    row.pools_ready = dict(probe.pools_ready)
    row.component_facts = component_facts_to_json(probe.component_facts)
    row.error = probe.error
    row.probed_at = now_utc()
    return row


async def get_cluster_status(session: AsyncSession) -> ClusterStatus | None:
    return await session.get(ClusterStatus, 1)


REGISTRY_CA_PATH_TEMPLATE = "__RANCHER_DIR__/harbor-ca.crt"


def render_registries_yaml(cfg: RuntimeConfig) -> str:
    """Generate the node registries.yaml (same format for RKE2 / k3s): `mirrors "*"` Spegel P2P;
    one <upstream>=<Harbor proxy project> per line of `registry_proxy_projects`; ca_file when
    `registry_ca_pem` is set.
    No auth; a non-empty `node_registries_yaml` is handed out verbatim.
    """
    override = cfg.node_registries_yaml.strip()
    if override:
        return override
    host = cfg.registry_host.strip()
    lines = ["mirrors:", '  "*": {}']
    if host:
        proxies = parse_proxy_projects(cfg.registry_proxy_projects)
        for upstream, project in proxies.items():
            lines += [
                f"  {upstream}:",
                "    endpoint:",
                f'      - "https://{host}"',
                "    rewrite:",
                f'      "^(.*)$": "{project}/$1"',
            ]
        if cfg.registry_ca_pem.strip():
            lines += [
                "configs:",
                f'  "{host}":',
                "    tls:",
                f'      ca_file: "{REGISTRY_CA_PATH_TEMPLATE}"',
            ]
    return "\n".join(lines) + "\n"


async def derive_node_distro(session: AsyncSession, cfg: RuntimeConfig) -> str:
    """Install distribution: probe cache > agent version suffix > rke2."""
    row = await get_cluster_status(session)
    if row and row.distro:
        return row.distro
    return derive_distro(cfg.cluster_agent_version) or "rke2"


HAMI_GATE_MAX_AGE = timedelta(minutes=10)


async def _fresh_cluster_status(session: AsyncSession) -> ClusterStatus:
    """Capability cache read shared by the dispatch gates: missing / stale → 409."""
    row = await get_cluster_status(session)
    if row is None or now_utc() - row.probed_at > HAMI_GATE_MAX_AGE:
        raise AppError(
            ErrorCode.CLUSTER_NOT_READY,
            key="nodes.clusterNotReady",
            http_status=http_status.HTTP_409_CONFLICT,
            detail={"reason": "probe_stale" if row else "no_probe"},
        )
    return row


async def require_hami_ready(session: AsyncSession) -> None:
    """Shared-tier dispatch gate: 409 when the scheduler is missing."""
    row = await _fresh_cluster_status(session)
    if not row.hami_ready:
        raise AppError(
            ErrorCode.CLUSTER_NOT_READY,
            key="nodes.clusterNotReady",
            http_status=http_status.HTTP_409_CONFLICT,
            detail={"reason": "hami_not_ready"},
        )


async def require_kata_runtimeclass(session: AsyncSession) -> None:
    """Dedicated-tier dispatch gate: 409 when RuntimeClass kata-qemu is missing."""
    row = await _fresh_cluster_status(session)
    if not row.kata_runtimeclass:
        raise AppError(
            ErrorCode.CLUSTER_NOT_READY,
            key="nodes.clusterNotReady",
            http_status=http_status.HTTP_409_CONFLICT,
            detail={"reason": "kata_runtimeclass_missing"},
        )


async def require_pool_runtime(session: AsyncSession, pool_label: str) -> None:
    """Check the target GPU pool runtime; hami checks HAMi, kata the RuntimeClass, mig the GPU
    Operator."""
    if pool_label == POOL_HAMI:
        await require_hami_ready(session)
        return
    if pool_label == POOL_KATA:
        await require_kata_runtimeclass(session)
        return
    if pool_label == POOL_MIG:
        row = await _fresh_cluster_status(session)
        if not row.gpu_operator_present:
            raise AppError(
                ErrorCode.CLUSTER_NOT_READY,
                key="nodes.clusterNotReady",
                http_status=http_status.HTTP_409_CONFLICT,
                detail={"reason": "gpu_operator_missing"},
            )


async def require_storage_classes(session: AsyncSession, *, with_data_disk: bool) -> None:
    """Storage dispatch gate: 409 when the instance-disk / data-disk StorageClass is missing."""
    row = await _fresh_cluster_status(session)
    present = set(row.storage_classes or ())
    required = [INSTANCE_DISK_STORAGE_CLASS]
    if with_data_disk:
        required.append(DATA_DISK_STORAGE_CLASS)
    missing = [sc for sc in required if sc not in present]
    if missing:
        raise AppError(
            ErrorCode.CLUSTER_NOT_READY,
            key="nodes.storageClassMissing",
            params={"names": ", ".join(missing)},
            http_status=http_status.HTTP_409_CONFLICT,
            detail={"reason": "storage_class_missing", "missing": missing},
        )


def _helmfile(distro: str | None, release: str) -> str:
    """apply.sh repair command by distribution."""
    env = {"k3s": "light", "rke2": "full"}.get(distro or "", "<full|light>")
    return f"deploy/cluster/apply.sh {env} -l name={release}"


_NVIDIA_RC_FIX = "install nvidia-container-toolkit on the node, then restart k3s/rke2"

_COMPONENT_META: tuple[tuple[ComponentKey, str | None, str], ...] = (
    ("nodes", None, f"kubectl get node -o wide -L {POOL_NODE_LABEL}"),
    ("hami", "hami", "kubectl -n kube-system get pod -l app=hami-scheduler -o wide"),
    ("gpu_operator", "gpu-operator", "kubectl -n gpu-operator get ds"),
    ("dcgm", "gpu-operator", "kubectl -n gpu-operator get ds | grep dcgm"),
    ("nvidia_runtimeclass", None, "kubectl get runtimeclass"),
    (
        "kata_runtimeclass",
        "kata-deploy",
        f"kubectl get runtimeclass kata-qemu; kubectl get node -l {POOL_NODE_LABEL}=kata",
    ),
    ("storage", "topolvm", "kubectl get sc"),
    ("gateway", "envoy-gateway", "kubectl -n superdl get gateway superdl -o yaml"),
    ("cert_manager", "cert-manager", "kubectl -n superdl get certificate"),
    ("monitoring", "kube-prometheus-stack", "kubectl -n monitoring get sts,ds"),
)


def cluster_components(row: ClusterStatus | None) -> list[ClusterComponentOut]:
    """Assemble component facts from the patrol snapshot; missing, stale or API-unreachable
    snapshots yield unknown."""
    facts = component_facts_from_json(row.component_facts if row else None)
    unknown = _probe_unknown(row)
    distro = row.distro if row else None
    return [
        _component_out(
            key,
            facts.get(key),
            release,
            diag,
            unknown=unknown,
            never_probed=row is None,
            distro=distro,
        )
        for key, release, diag in _COMPONENT_META
    ]


def _probe_unknown(row: ClusterStatus | None) -> bool:
    """Never probed / probe older than the freshness window / API unreachable: no fact is
    trustworthy; the same window as the dispatch gate."""
    if row is None or not row.api_reachable:
        return True
    return now_utc() - row.probed_at > HAMI_GATE_MAX_AGE


def _component_out(
    key: ComponentKey,
    cf: ComponentFacts | None,
    release: str | None,
    diag: str,
    *,
    unknown: bool,
    never_probed: bool,
    distro: str | None,
) -> ClusterComponentOut:
    state: ComponentStateOut = "unknown" if (unknown or cf is None) else cf.state
    show_fix = state in ("down", "degraded") or never_probed
    return ClusterComponentOut(
        key=key,
        state=state,
        headline=_fact_out(cf.headline) if cf and cf.headline else None,
        facts=[_fact_out(f) for f in cf.facts if f.value] if cf else [],
        objects=[ComponentObjectOut(name=o.name, fields=o.fields) for o in cf.objects]
        if cf
        else [],
        fix_hint=_fix_hint(key, release, distro) if show_fix else None,
        diag_hint=diag,
    )


def _fix_hint(key: ComponentKey, release: str | None, distro: str | None) -> str | None:
    if key == "nvidia_runtimeclass":
        return _NVIDIA_RC_FIX
    return _helmfile(distro, release) if release else None


def _fact_out(f: ComponentFact) -> ComponentFactOut:
    return ComponentFactOut(key=f.key, value=f.value, tone=f.tone)


COMPONENT_PROBE_TIMEOUT = 5.0
COMPONENT_PROBE_MAX_PER_HOUR = 120

_COMPONENT_KEYS = frozenset(key for key, _r, _d in _COMPONENT_META)


async def probe_component_detail(admin_id: int, key: str) -> ComponentProbeOut:
    """Rate-limited, time-boxed read-only deep probe straight against K8s on the request path; the
    result is not stored, the rate-limit count commits independently, no audit.

    Unknown key → 404, probe failure or timeout → 503.
    """
    if key not in _COMPONENT_KEYS:
        raise not_found(key="nodes.nodeNotFound")
    await check_rate_limit(
        f"component-probe:{admin_id}",
        max_attempts=COMPONENT_PROBE_MAX_PER_HOUR,
        window_seconds=3600.0,
    )
    try:
        detail = await asyncio.wait_for(
            get_orchestrator().probe_component_detail(key), timeout=COMPONENT_PROBE_TIMEOUT
        )
    except Exception as exc:
        logger.warning("component_probe_failed", component=key, error=str(exc))
        raise AppError(
            ErrorCode.INTERNAL,
            key="nodes.componentProbeFailed",
            http_status=http_status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    return ComponentProbeOut(
        key=cast(ComponentKey, key),
        probed_at=now_utc(),
        facts=[_fact_out(f) for f in detail.facts],
        pods=[ComponentObjectOut(name=o.name, fields=o.fields) for o in detail.pods],
        events=[ComponentObjectOut(name=o.name, fields=o.fields) for o in detail.events],
    )
