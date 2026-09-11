"""节点规格台账巡检(60s):K8s 实况 + 装机登记 → node_specs。

  A 纯 K8s 读:list_nodes(含未打标);
  B 单事务 DB 收敛:upsert;消失节点置 Missing,超保留期删行;
  C label 收敛:canonical 写 superdl.io/gpu-model(逐节点独立 try);
  C2 池标签纠偏:自声明与注册登记不符 → 先 cordon(经 outbox)再按登记改标签;
  D cordon 期望态收敛。

型号优先级:装机登记 nvidia-smi > GFD label > 存量;驱动/CUDA 版本 GFD label 优先。
"""

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.gpu_models import canonical_gpu_model, default_vram_gb
from app.core.k8s import get_orchestrator
from app.core.k8s.base import GPU_MODEL_NODE_LABEL, POOL_NODE_LABEL
from app.core.locks import LockKey, advisory_lock
from app.core.logging import get_logger
from app.core.metrics import LIGHT_DISTRO_IN_PROD, NODE_POOL_LABEL_MISMATCH_TOTAL
from app.core.timeutil import now_utc
from app.modules.nodes import service
from app.modules.nodes.models import NodeEnrollment, NodeSpec

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

        # ---- A:纯 K8s 读(锁内、事务外):能力探测 + 节点清单 ----
        probe = await orch.probe_cluster()
        if not probe.api_reachable:
            # API 不可达也落缓存;节点收敛本轮跳过
            async with sm() as session:
                await service.save_cluster_probe(session, probe)
                await session.commit()
            logger.warning("cluster_probe_unreachable", error=probe.error)
            return counts
        counts["probe_ok"] = 1
        # prod 不得运行在 light(k3s)档;常驻指标
        light_violation = get_settings().environment == "prod" and probe.distro == "k3s"
        LIGHT_DISTRO_IN_PROD.set(1 if light_violation else 0)
        if light_violation:
            logger.error(
                "light_distro_in_prod",
                hint="prod 环境不得运行在 k3s(light)档:租户与控制面同宿主,应迁移 full(rke2)",
            )
        nodes = await orch.list_nodes(include_unlabeled=True)

        desired_labels: list[tuple[str, str]] = []
        # (节点名, 登记池, 节点自声明的池)
        desired_pool_fix: list[tuple[str, str, str]] = []
        now = now_utc()
        # ---- B:单事务 DB 收敛 ----
        async with sm() as session:
            await service.save_cluster_probe(session, probe)
            enroll = await _enrollment_specs(session)
            enrolled_pools = await _enrolled_pools(session)
            rows = {r.node_name: r for r in (await session.execute(select(NodeSpec))).scalars()}
            seen: set[str] = set()
            for n in nodes:
                seen.add(n.name)
                e = enroll.get(n.name, {})
                raw = (
                    e.get("raw")
                    or n.gpu_model_label
                    or (rows[n.name].gpu_model_raw if n.name in rows else None)
                )
                canonical = canonical_gpu_model(raw)
                vram = (
                    int(e.get("vram_gb") or 0)
                    or (rows[n.name].vram_gb if n.name in rows else 0)
                    or default_vram_gb(canonical)
                )
                unlabeled = not n.pool_label or n.pool_label == "unknown"
                row = rows.get(n.name)
                if row is None:
                    row = NodeSpec(node_name=n.name, status=n.status, last_seen=now)
                    session.add(row)
                    rows[n.name] = row
                row.pool_label = None if unlabeled else n.pool_label
                row.unlabeled = unlabeled
                row.gpu_model_raw = raw or None
                row.gpu_model = canonical
                row.gpu_count = n.gpu_total
                row.gpu_used = n.gpu_used
                row.vram_gb = vram
                row.vcpu = n.vcpu
                row.mem_gb = n.mem_gb
                row.disk_gb = n.disk_gb
                row.driver_version = (
                    n.driver_version_label or e.get("driver_version") or row.driver_version
                )
                row.cuda_version = n.cuda_version_label or e.get("cuda_version") or row.cuda_version
                row.status = n.status
                row.last_seen = now
                counts["upserted"] += 1
                if canonical and n.model_label_current != canonical:
                    row.label_synced = False
                    desired_labels.append((n.name, canonical))
                elif canonical:
                    row.label_synced = True
                else:
                    # 型号未知不沿用上一轮真值
                    row.label_synced = False
                # 池标签对账:注册登记(node_enrollments.pool)是事实源,节点自声明不一致即纠正
                enrolled_pool = enrolled_pools.get(n.name)
                if (
                    enrolled_pool
                    and n.pool_label not in ("", "unknown")
                    and n.pool_label != enrolled_pool
                ):
                    # 指标在发现时计数
                    NODE_POOL_LABEL_MISMATCH_TOTAL.inc()
                    desired_pool_fix.append((n.name, enrolled_pool, n.pool_label))
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

        # ---- C:label 收敛(K8s 写,逐节点独立 try) ----
        for name, canonical in desired_labels:
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

        # ---- C2:池标签纠偏(注册登记 > 节点自声明;逐节点独立 try) ----
        # 先 cordon(service.request_cordon,与管理端同路径)再改标签
        for name, pool, observed in desired_pool_fix:
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
                            f"池标签与注册登记不符(节点自称 {observed},登记为 {pool}),"
                            "已自动停止调度待人工核查"
                        ),
                    )
                    counts["pool_mismatch_cordoned"] += 1
            try:
                await orch.set_node_labels(name, {POOL_NODE_LABEL: pool})
            except Exception:
                logger.warning("node_pool_label_fix_failed", node=name, pool=pool)
                continue
            counts["pool_label_corrected"] += 1
            logger.warning(
                "node_pool_label_corrected",
                node=name,
                pool=pool,
                observed=observed,
                hint="节点自声明池标签与注册登记不符,已按登记纠正并停止调度;需排查节点凭据",
            )

        # ---- D:cordon 期望态收敛(逐节点独立 try) ----
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
    return counts
