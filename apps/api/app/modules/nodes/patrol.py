"""节点规格台账巡检(60s):K8s 实况 + 装机登记 → node_specs 单一事实源。

三阶段(worker 收敛环,不走 outbox):
  A 纯 K8s 读:list_nodes(全量含未打标);
  B 单事务 DB 收敛:upsert 全字段;消失节点置 Missing,超保留期删行;
  C label 收敛:canonical 写 superdl.io/gpu-model(逐节点独立 try,失败下轮自愈)。

数据源优先级(型号 raw):装机登记 nvidia-smi > GFD label(nvidia.com/gpu.product)> 存量。
"""

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.gpu_models import canonical_gpu_model, default_vram_gb
from app.core.k8s import get_orchestrator
from app.core.locks import LockKey, try_advisory_lock
from app.core.logging import get_logger
from app.core.timeutil import now_utc
from app.modules.nodes import service
from app.modules.nodes.models import NodeEnrollment, NodeSpec

logger = get_logger(__name__)

MISSING_RETENTION = timedelta(days=7)  # Missing 超此时长删行


def _gpu_entry_name(entry: Any) -> str:
    """gpu_info 条目兼容两种形态:旧 str(型号名)/ 新 dict{name, memory_mib}。"""
    if isinstance(entry, dict):
        return str(entry.get("name") or "")
    return str(entry or "")


def _gpu_entry_vram_gb(entry: Any) -> int:
    if isinstance(entry, dict):
        mib = entry.get("memory_mib")
        if isinstance(mib, (int, float)) and mib > 0:
            return round(float(mib) / 1024)
    return 0


async def _enrollment_specs(session: AsyncSession) -> dict[str, dict[str, Any]]:
    rows = (
        await session.execute(select(NodeEnrollment).where(NodeEnrollment.status == "joined"))
    ).scalars()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        if not r.node_name:
            continue
        gpu_info = r.gpu_info or []
        first = gpu_info[0] if gpu_info else None
        os_info = r.os_info or {}
        out[r.node_name] = {
            "raw": _gpu_entry_name(first),
            "vram_gb": max((_gpu_entry_vram_gb(e) for e in gpu_info), default=0),
            "driver_version": str(os_info.get("driver_version") or "") or None,
            "cuda_version": str(os_info.get("cuda_version") or "") or None,
        }
    return out


async def node_spec_patrol(sm: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """单轮巡检。返回动作计数(测试/日志用)。"""
    counts = {
        "upserted": 0,
        "missing": 0,
        "removed": 0,
        "labeled": 0,
        "label_failed": 0,
        "probe_ok": 0,
    }
    async with (
        sm() as lock_session,
        try_advisory_lock(lock_session, LockKey.NODE_SPEC_PATROL) as got,
    ):
        if not got:
            return counts
        orch = get_orchestrator()

        # ---- A:纯 K8s 读(锁内、事务外):能力探测 + 节点清单 ----
        probe = await orch.probe_cluster()
        if not probe.api_reachable:
            # API 不可达也要落缓存(集群页红牌/门禁据此拒绝),节点收敛本轮跳过
            async with sm() as session:
                await service.save_cluster_probe(session, probe)
                await session.commit()
            logger.warning("cluster_probe_unreachable", error=probe.error)
            return counts
        counts["probe_ok"] = 1
        nodes = await orch.list_nodes(include_unlabeled=True)

        desired_labels: list[tuple[str, str]] = []
        now = now_utc()
        # ---- B:单事务 DB 收敛 ----
        async with sm() as session:
            await service.save_cluster_probe(session, probe)
            enroll = await _enrollment_specs(session)
            rows = {r.node_name: r for r in (await session.execute(select(NodeSpec))).scalars()}
            seen: set[str] = set()
            for n in nodes:
                seen.add(n.name)
                e = enroll.get(n.name, {})
                raw = (
                    e.get("raw")
                    or n.gpu_model_label
                    or (n.gpu_model if n.gpu_model and n.gpu_model != "GPU" else None)
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
                row.driver_version = e.get("driver_version") or row.driver_version
                row.cuda_version = e.get("cuda_version") or row.cuda_version
                row.status = n.status
                row.last_seen = now
                counts["upserted"] += 1
                if canonical and n.model_label_current != canonical:
                    row.label_synced = False
                    desired_labels.append((n.name, canonical))
                elif canonical:
                    row.label_synced = True
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

        # ---- C:label 收敛(K8s 写,逐节点独立 try;失败下轮自愈) ----
        for name, canonical in desired_labels:
            try:
                await orch.set_node_labels(name, {"superdl.io/gpu-model": canonical})
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
    return counts
