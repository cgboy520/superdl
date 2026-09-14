"""节点规格台账巡检(60s):K8s 实况 + 装机登记 → node_specs。

  A 纯 K8s 读:能力探测 + list_nodes(含未打标);
  B 单事务 DB 收敛:upsert;消失节点置 Missing,超保留期删行;顺带算出 C / C2 的待办;
  C label 收敛:canonical 写 superdl.io/gpu-model(逐节点独立 try);
  C2 池标签收敛:自声明与期望池不符 → 按期望池整套下发标签;期望池来自管理端切池(desired_pool)
     或注册登记,后者不符属冒名,另加 critical 指标并先 cordon;
  D cordon 期望态收敛。

型号优先级:装机登记 nvidia-smi > GFD label > 存量;驱动/CUDA 版本 GFD label 优先。
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
from app.core.metrics import LIGHT_DISTRO_IN_PROD, NODE_POOL_LABEL_MISMATCH_TOTAL
from app.core.timeutil import now_utc
from app.modules.metering import service as metering_service
from app.modules.nodes import service
from app.modules.nodes.models import NodeEnrollment, NodeSpec
from app.modules.nodes.service import pool_matches

logger = get_logger(__name__)

MISSING_RETENTION = timedelta(days=7)  # Missing 超此时长删行


def _gpu_entry_vram_gb(entry: dict[str, Any]) -> int:
    """gpu_info 条目 {name, memory_mib?} 的显存 GB;无 memory_mib → 0。"""
    mib = entry.get("memory_mib")
    if isinstance(mib, (int, float)) and mib > 0:
        return round(float(mib) / 1024)
    return 0


async def _enrollment_specs(session: AsyncSession) -> dict[str, dict[str, Any]]:
    """装机登记的规格快照(型号/显存/驱动/CUDA),只认 joined;池归属另走 _enrolled_pools。"""
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


# 池归属的事实源状态:joined + failed(对账器判「标签与登记不符」后落的状态,纠偏必须覆盖)
POOL_AUTHORITY_STATUSES = ("joined", "failed")


async def _enrolled_pools(session: AsyncSession) -> dict[str, str]:
    """节点名 → 登记池(池标签对账的事实源),覆盖 joined 与 failed;同一主机名取 id 最大的行。"""
    rows = (
        await session.execute(
            select(NodeEnrollment)
            .where(NodeEnrollment.status.in_(POOL_AUTHORITY_STATUSES))
            .order_by(NodeEnrollment.id)
        )
    ).scalars()
    return {r.node_name: r.pool for r in rows if r.node_name}


@dataclass
class _Plan:
    """B 阶段产出、C / C2 阶段消费的 K8s 写待办。"""

    labels: list[tuple[str, str]] = field(default_factory=list)  # (节点, canonical 型号)
    # (节点, 期望池, 自声明池, 是否管理端切池);切池是运维动作,冒名是入侵事件,两者处置不同
    pool_fixes: list[tuple[str, str, str, bool]] = field(default_factory=list)


async def node_spec_patrol(sm: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """单轮巡检,返回动作计数。"""
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
    }
    async with advisory_lock(sm, LockKey.NODE_SPEC_PATROL) as got:
        if not got:
            return counts
        orch = get_orchestrator()
        # ---- A:纯 K8s 读(锁内、事务外) ----
        probe = await orch.probe_cluster()
        if not probe.api_reachable:
            # API 不可达也落缓存;节点收敛本轮跳过
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
        await _sync_model_labels(sm, orch, plan.labels, counts)
        await _fix_pool_labels(sm, orch, plan.pool_fixes, counts)
        await _converge_cordon(sm, orch, nodes, counts)
    return counts


async def _merge_prometheus_facts(probe: ClusterProbe) -> ClusterProbe:
    """把 Prometheus 侧事实并进体检快照(抓取健康、DCGM 样本新鲜度、firing 数)。

    在巡检里取而不在请求路径取:集群页保持纯 DB 读。Prometheus 挂了只是少几条事实,
    不改任何组件的状态位。
    """
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
    """prod 不得运行在 light(k3s)档;常驻指标 + 日志。"""
    light_violation = get_settings().environment == "prod" and probe.distro == "k3s"
    LIGHT_DISTRO_IN_PROD.set(1 if light_violation else 0)
    if light_violation:
        logger.error(
            "light_distro_in_prod",
            hint="prod 环境不得运行在 k3s(light)档:租户与控制面同宿主,应迁移 full(rke2)",
        )


async def _converge_ledger(
    sm: async_sessionmaker[AsyncSession],
    probe: ClusterProbe,
    nodes: list[NodeInfo],
    counts: dict[str, int],
) -> _Plan:
    """B:单事务写台账(探测缓存、逐节点 upsert、消失节点 Missing / 删行),返回 K8s 写待办。"""
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
                # 型号未知不沿用上一轮真值
                row.label_synced = bool(canonical)
            # 池标签对账:期望池是事实源,优先级 desired_pool(管理端切池)> 注册登记。
            # 平台是池标签唯一写入方,所以「未打标」与「标签不符」都由这里补齐:
            # 未打标 = 装机中或 Node 对象被删重建(kubelet 重注册不带池标签),补上即可。
            wanted_pool = row.desired_pool or enrolled_pools.get(n.name)
            if wanted_pool and not pool_matches(wanted_pool, n.pool_label):
                observed = n.pool_label or ""
                # 标签不符且节点仍可调度 = 平台以外的写入方改过它(切池必先 cordon,收敛窗口内
                # 节点一定是停止调度的;cordon 中的节点不接实例,漂移也不产生后果)
                tampered = observed not in ("", "unknown") and row.desired_unschedulable is not True
                if tampered:
                    NODE_POOL_LABEL_MISMATCH_TOTAL.inc()  # 指标在发现时计数
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


def _upsert_node_spec(
    row: NodeSpec, n: NodeInfo, enrolled: dict[str, Any], now: datetime
) -> str | None:
    """把 K8s 节点视图与装机登记合并进台账行;返回 canonical 型号(未知为 None)。"""
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
    """C:canonical 型号写节点标签(逐节点独立 try),成功即回写 label_synced。"""
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
    """C2:池标签收敛(期望池 > 节点实况;逐节点独立 try),整套下发含 GPU operand 标签。
    未打标(装机中 / Node 对象重建)只补标签;标签被手工改过才先 cordon
    (service.request_cordon,与管理端同路径)再改回,并已在发现时计了 critical 指标。"""
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
                            f"池标签被手工改动(节点上是 {observed},平台期望 {pool}),"
                            "已自动停止调度待人工核查"
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
            hint="池标签被平台以外的写入方改过,已按期望池改回并停止调度;需排查谁有 Node 写权限",
        )


async def _converge_cordon(
    sm: async_sessionmaker[AsyncSession],
    orch: K8sOrchestrator,
    nodes: list[NodeInfo],
    counts: dict[str, int],
) -> None:
    """D:cordon 期望态收敛(逐节点独立 try)。"""
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
