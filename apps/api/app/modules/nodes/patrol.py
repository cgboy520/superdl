"""Node spec patrol: read K8s, converge the inventory in one transaction, isolate unenrolled nodes,
then converge labels and cordon.

Model priority: enrollment nvidia-smi > GFD label > existing; driver / CUDA versions prefer the GFD
label.
Desired pool priority: desired_pool > enrollment.
Unenrolled isolation: non-infra nodes without an enrollment row, desired pool or pool label get a
cordon request.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.gpu_adapter import pool_node_labels
from app.core.gpu_models import canonical_gpu_model, default_vram_gb
from app.core.k8s import K8sOrchestrator, get_orchestrator, health
from app.core.k8s.base import GPU_MODEL_NODE_LABEL, ClusterProbe, NodeInfo
from app.core.locks import LockKey, advisory_lock
from app.core.logging import get_logger
from app.core.metrics import (
    LIGHT_DISTRO_IN_PROD,
    NODE_POOL_LABEL_MISMATCH_TOTAL,
    NODE_UNENROLLED,
)
from app.core.timeutil import now_utc
from app.modules.metering import service as metering_service
from app.modules.nodes import service
from app.modules.nodes.models import NodeEnrollment, NodeSpec
from app.modules.nodes.service import pool_matches

logger = get_logger(__name__)

MISSING_RETENTION = timedelta(days=7)


def _gpu_entry_vram_gb(entry: dict[str, Any]) -> int:
    """VRAM GB of a gpu_info entry {name, memory_mib?}; no memory_mib → 0."""
    mib = entry.get("memory_mib")
    if isinstance(mib, (int, float)) and mib > 0:
        return round(float(mib) / 1024)
    return 0


async def _enrollment_specs(session: AsyncSession) -> dict[str, dict[str, Any]]:
    """Spec snapshot from the enrollment (model / VRAM / driver / CUDA), joined rows only; pool
    membership goes through _enrolled_pools."""
    rows = (
        await session.execute(select(NodeEnrollment).where(NodeEnrollment.status == "joined"))
    ).scalars()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        if not r.node_name:
            continue
        gpu_info = r.gpu_info or []
        os_info = r.os_info or {}
        out[r.node_name] = {
            "raw": str(gpu_info[0].get("name") or "") if gpu_info else "",
            "vram_gb": max((_gpu_entry_vram_gb(e) for e in gpu_info), default=0),
            "driver_version": str(os_info.get("driver_version") or "") or None,
            "cuda_version": str(os_info.get("cuda_version") or "") or None,
        }
    return out


POOL_AUTHORITY_STATUSES = ("joined", "failed")


async def _enrolled_pools(session: AsyncSession) -> dict[str, str]:
    """Node name → enrolled pool (source of truth for pool label reconciliation), covering joined
    and
    failed; with several rows per hostname the largest id wins."""
    rows = (
        await session.execute(
            select(NodeEnrollment)
            .where(NodeEnrollment.status.in_(POOL_AUTHORITY_STATUSES))
            .order_by(NodeEnrollment.id)
        )
    ).scalars()
    return {r.node_name: r.pool for r in rows if r.node_name}


async def _enrolled_node_names(session: AsyncSession) -> set[str]:
    """Node names with an enrollment row in any status; bootstrap already stores node_name, so nodes
    still installing are included."""
    rows = (
        await session.execute(
            select(NodeEnrollment.node_name).where(NodeEnrollment.node_name.is_not(None))
        )
    ).scalars()
    return {name for name in rows if name}


def _unlabeled(pool_label: str | None) -> bool:
    return pool_label in (None, "", "unknown")


UNENROLLED_CORDON_REASON = (
    "unenrolled node joined the cluster; scheduling stopped automatically pending review"
)


@dataclass
class _Plan:
    """Label work items are (node, model); pool work items are (node, desired pool, observed pool,
    tampered)."""

    labels: list[tuple[str, str]] = field(default_factory=list)
    pool_fixes: list[tuple[str, str, str, bool]] = field(default_factory=list)


async def node_spec_patrol(sm: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """One patrol round, returns action counts."""
    counts = {
        "upserted": 0,
        "missing": 0,
        "removed": 0,
        "labeled": 0,
        "label_failed": 0,
        "probe_ok": 0,
        "cordon_converged": 0,
        "pool_label_corrected": 0,
        "pool_mismatch_cordoned": 0,
        "unenrolled_cordoned": 0,
    }
    async with advisory_lock(sm, LockKey.NODE_SPEC_PATROL) as got:
        if not got:
            return counts
        orch = get_orchestrator()
        probe = await orch.probe_cluster()
        if not probe.api_reachable:
            async with sm() as session:
                await service.save_cluster_probe(session, probe)
                await session.commit()
            logger.warning("cluster_probe_unreachable", error=probe.error)
            return counts
        counts["probe_ok"] = 1
        probe = await _merge_prometheus_facts(probe)
        _report_light_distro(probe)
        nodes = await orch.list_nodes(include_unlabeled=True)
        plan = await _converge_ledger(sm, probe, nodes, counts)
        await _quarantine_unenrolled(sm, nodes, counts)
        await _sync_model_labels(sm, orch, plan.labels, counts)
        await _fix_pool_labels(sm, orch, plan.pool_fixes, counts)
        await _converge_cordon(sm, orch, nodes, counts)
    return counts


async def _merge_prometheus_facts(probe: ClusterProbe) -> ClusterProbe:
    """Merge Prometheus scrape health, DCGM freshness and firing count; component states are
    unchanged."""
    extra = await metering_service.cluster_component_metrics()
    if not extra:
        return probe
    facts = dict(probe.component_facts)
    for comp, keys in (("dcgm", ("dcgm",)), ("monitoring", ("monitoring", "monitoring_alerts"))):
        target = facts.get(comp)
        if target is None:
            continue
        added = [extra[k] for k in keys if k in extra]
        facts[comp] = health.merge_facts(target, added)
    return replace(probe, component_facts=facts)


def _report_light_distro(probe: ClusterProbe) -> None:
    """prod must not run on the light (k3s) tier; standing metric + log."""
    light_violation = get_settings().environment == "prod" and probe.distro == "k3s"
    LIGHT_DISTRO_IN_PROD.set(1 if light_violation else 0)
    if light_violation:
        logger.error(
            "light_distro_in_prod",
            hint="prod must not run on the k3s (light) tier: tenants share the host with the"
            " control plane, migrate to full (rke2)",
        )


async def _converge_ledger(
    sm: async_sessionmaker[AsyncSession],
    probe: ClusterProbe,
    nodes: list[NodeInfo],
    counts: dict[str, int],
) -> _Plan:
    """Converge the probe cache and inventory in one transaction, return the K8s write work items;
    vanished nodes become Missing or are deleted past retention."""
    plan = _Plan()
    now = now_utc()
    async with sm() as session:
        await service.save_cluster_probe(session, probe)
        enroll = await _enrollment_specs(session)
        enrolled_pools = await _enrolled_pools(session)
        rows = {r.node_name: r for r in (await session.execute(select(NodeSpec))).scalars()}
        seen: set[str] = set()
        for n in nodes:
            seen.add(n.name)
            row = rows.get(n.name)
            if row is None:
                row = NodeSpec(node_name=n.name, status=n.status, last_seen=now)
                session.add(row)
                rows[n.name] = row
            canonical = _upsert_node_spec(row, n, enroll.get(n.name, {}), now)
            counts["upserted"] += 1
            if canonical and n.model_label_current != canonical:
                row.label_synced = False
                plan.labels.append((n.name, canonical))
            else:
                row.label_synced = bool(canonical)
            wanted_pool = row.desired_pool or enrolled_pools.get(n.name)
            if wanted_pool and not pool_matches(wanted_pool, n.pool_label):
                observed = n.pool_label or ""
                tampered = observed not in ("", "unknown") and row.desired_unschedulable is not True
                if tampered:
                    NODE_POOL_LABEL_MISMATCH_TOTAL.inc()
                plan.pool_fixes.append((n.name, wanted_pool, observed, tampered))
        for name, row in list(rows.items()):
            if name in seen:
                continue
            if row.last_seen and now - row.last_seen > MISSING_RETENTION:
                await session.delete(row)
                counts["removed"] += 1
            elif row.status != "Missing":
                row.status = "Missing"
                counts["missing"] += 1
        await session.commit()
    return plan


async def _quarantine_unenrolled(
    sm: async_sessionmaker[AsyncSession],
    nodes: list[NodeInfo],
    counts: dict[str, int],
) -> None:
    """Unenrolled isolation: non-infra, no enrollment row, no desired pool and no pool label →
    request cordon (not repeated once requested);
    a pool label without an enrollment only warns (the protected prefix is writable by the platform
    /
    admins only). The metric is reset every round."""
    async with sm() as session:
        enrolled = await _enrolled_node_names(session)
        rows = {r.node_name: r for r in (await session.execute(select(NodeSpec))).scalars()}
        unenrolled: list[str] = []
        to_cordon: list[str] = []
        for n in nodes:
            row = rows.get(n.name)
            if n.infra or n.name in enrolled or (row is not None and row.desired_pool):
                continue
            if not _unlabeled(n.pool_label):
                logger.warning("node_pool_label_without_enrollment", node=n.name, pool=n.pool_label)
                continue
            unenrolled.append(n.name)
            if row is None or row.desired_unschedulable is not True:
                to_cordon.append(n.name)
        for name in to_cordon:
            await service.request_cordon(
                session, name, unschedulable=True, reason=UNENROLLED_CORDON_REASON
            )
            counts["unenrolled_cordoned"] += 1
    NODE_UNENROLLED.set(len(unenrolled))
    for name in unenrolled:
        logger.error(
            "node_unenrolled",
            node=name,
            hint="a node without an enrollment joined the cluster and was cordoned; verify its"
            " origin, then decommission or enroll it",
        )


def _upsert_node_spec(
    row: NodeSpec, n: NodeInfo, enrolled: dict[str, Any], now: datetime
) -> str | None:
    """Merge the K8s node view and the enrollment into the inventory row; returns the canonical
    model (None when unknown)."""
    raw = enrolled.get("raw") or n.gpu_model_label or row.gpu_model_raw
    canonical = canonical_gpu_model(raw)
    unlabeled = not n.pool_label or n.pool_label == "unknown"
    row.pool_label = None if unlabeled else n.pool_label
    row.unlabeled = unlabeled
    row.gpu_model_raw = raw or None
    row.gpu_model = canonical
    row.gpu_count = n.gpu_total
    row.gpu_used = n.gpu_used
    row.vram_gb = int(enrolled.get("vram_gb") or 0) or row.vram_gb or default_vram_gb(canonical)
    row.vcpu = n.vcpu
    row.mem_gb = n.mem_gb
    row.disk_gb = n.disk_gb
    row.driver_version = (
        n.driver_version_label or enrolled.get("driver_version") or row.driver_version
    )
    row.cuda_version = n.cuda_version_label or enrolled.get("cuda_version") or row.cuda_version
    row.status = n.status
    row.last_seen = now
    return canonical


async def _sync_model_labels(
    sm: async_sessionmaker[AsyncSession],
    orch: K8sOrchestrator,
    labels: list[tuple[str, str]],
    counts: dict[str, int],
) -> None:
    """Write the canonical model label per node, marking label_synced on success; a failure does not
    block the next node."""
    for name, canonical in labels:
        try:
            await orch.set_node_labels(name, {GPU_MODEL_NODE_LABEL: canonical})
        except Exception:
            counts["label_failed"] += 1
            logger.warning("node_label_sync_failed", node=name, model=canonical)
            continue
        async with sm() as session:
            row = (
                await session.execute(select(NodeSpec).where(NodeSpec.node_name == name))
            ).scalar_one_or_none()
            if row is not None:
                row.label_synced = True
                await session.commit()
        counts["labeled"] += 1


async def _fix_pool_labels(
    sm: async_sessionmaker[AsyncSession],
    orch: K8sOrchestrator,
    fixes: list[tuple[str, str, str, bool]],
    counts: dict[str, int],
) -> None:
    """Apply the desired pool and GPU operand labels per node; tampered nodes request cordon first,
    unlabeled nodes only get their labels."""
    for name, pool, observed, tampered in fixes:
        if tampered:
            async with sm() as session:
                row = (
                    await session.execute(select(NodeSpec).where(NodeSpec.node_name == name))
                ).scalar_one_or_none()
                if row is not None and row.desired_unschedulable is not True:
                    await service.request_cordon(
                        session,
                        name,
                        unschedulable=True,
                        reason=(
                            f"pool label changed manually (node has {observed}, platform"
                            f" expects {pool}), scheduling stopped automatically pending review"
                        ),
                    )
                    counts["pool_mismatch_cordoned"] += 1
        try:
            await orch.set_node_labels(name, pool_node_labels(pool))
        except Exception:
            logger.warning("node_pool_label_fix_failed", node=name, pool=pool)
            continue
        counts["pool_label_corrected"] += 1
        if not tampered:
            logger.info("node_pool_label_applied", node=name, pool=pool)
            continue
        logger.warning(
            "node_pool_label_corrected",
            node=name,
            pool=pool,
            observed=observed,
            hint="the pool label was changed by a writer other than the platform; reset to the"
            " desired pool and cordoned; find out who has Node write access",
        )


async def _converge_cordon(
    sm: async_sessionmaker[AsyncSession],
    orch: K8sOrchestrator,
    nodes: list[NodeInfo],
    counts: dict[str, int],
) -> None:
    """Converge the cordon desired state per node; a failure does not block the next node."""
    async with sm() as session:
        desired_rows = list(
            (
                await session.execute(
                    select(NodeSpec).where(NodeSpec.desired_unschedulable.is_not(None))
                )
            ).scalars()
        )
    actual = {n.name: n.status for n in nodes}
    for row in desired_rows:
        actual_cordoned = actual.get(row.node_name) == "Cordoned"
        if actual.get(row.node_name) is None or actual_cordoned == row.desired_unschedulable:
            continue
        try:
            await orch.set_node_unschedulable(row.node_name, bool(row.desired_unschedulable))
        except Exception:
            logger.warning("node_cordon_converge_failed", node=row.node_name)
            continue
        counts["cordon_converged"] += 1
        logger.info(
            "node_cordon_converged", node=row.node_name, unschedulable=row.desired_unschedulable
        )
