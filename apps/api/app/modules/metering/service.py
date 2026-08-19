"""用量服务:实例监控代理 + usage_hourly 聚合 + 事件计费 vs 指标估算对账。"""

import math
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError, ErrorCode
from app.core.locks import LockKey, try_advisory_lock
from app.core.logging import get_logger
from app.core.money import as_amount
from app.core.timeutil import now_utc, prev_hour_range
from app.modules.metering import prom
from app.modules.metering.models import UsageHourly
from app.modules.metering.schemas import InstanceGpuSeries, InstanceMetricsSummaryOut

logger = get_logger(__name__)

RANGES = {"1h": 3600, "6h": 6 * 3600, "24h": 24 * 3600}


async def instance_metrics(ns: str, pod: str, range_key: str) -> dict[str, Any]:
    """代理查询实例监控曲线。断源报 503(前端提示"监控暂不可用,不影响计费")。"""
    if range_key not in RANGES:
        raise AppError(ErrorCode.VALIDATION_ERROR, "range 须为 1h/6h/24h")
    end = now_utc().timestamp()
    start = end - RANGES[range_key]
    step = prom.RANGE_STEPS[range_key]
    series: dict[str, list[tuple[float, float]]] = {}
    try:
        for metric in prom.QUERIES:
            series[metric] = await prom.query_range(
                metric, ns, pod, start=start, end=end, step=step
            )
    except prom.PrometheusUnavailable as exc:
        raise AppError(
            ErrorCode.INTERNAL,
            "监控数据暂不可用,不影响计费(计费依据为实例事件流水)",
            http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    return {"range": range_key, "series": series}


SUMMARY_CAP = 20  # 列表 sparkline 最多取前 N 台 running,防批量放大 Prometheus 压力


async def instances_gpu_summary(targets: list[tuple[str, str]]) -> InstanceMetricsSummaryOut:
    """批量取各实例近 1h gpu_util 稀疏序列(targets: [(uuid, ns)])。

    Prometheus 断源返回 available=false 而非抛错 —— 实例列表页不能因监控毁掉;
    单实例查询失败仅跳过该台。
    """
    end = now_utc().timestamp()
    start = end - RANGES["1h"]
    items: list[InstanceGpuSeries] = []
    for uuid, ns in targets[:SUMMARY_CAP]:
        try:
            points = await prom.query_range("gpu_util", ns, uuid, start=start, end=end, step="300s")
        except prom.PrometheusUnavailable:
            return InstanceMetricsSummaryOut(available=False, items=[])
        except Exception:  # 单台异常不拖垮整批
            logger.warning("metrics_summary_instance_failed", uuid=uuid)
            continue
        items.append(
            InstanceGpuSeries(uuid=uuid, points=points, last=points[-1][1] if points else None)
        )
    return InstanceMetricsSummaryOut(available=True, items=items)


async def aggregate_previous_hour(
    sm: async_sessionmaker[AsyncSession], *, at: datetime | None = None
) -> int:
    """每小时 :05 聚合上一小时用量入 usage_hourly。幂等(UNIQUE DO NOTHING)。

    Prometheus 不可用时静默跳过 —— 计费完全不受影响。
    """
    from app.modules.orchestrator import service as orchestrator_service

    window_start, window_end = prev_hour_range(at or now_utc())
    written = 0
    async with sm() as lock_session:
        async with try_advisory_lock(lock_session, LockKey.USAGE_AGGREGATION) as got:
            if not got:
                return 0
            async with sm() as session:
                candidates = await orchestrator_service.billing_candidates(
                    session, window_start, window_end
                )
                instances = await orchestrator_service.admin_list_instances(session)
                loc = {i.id: (i.k8s_namespace, i.uuid) for i in instances}
            for inst_id, _user_id, _price, _gpus in candidates:
                ns_pod = loc.get(inst_id)
                if not ns_pod or not ns_pod[0]:
                    continue
                try:
                    values = await prom.query_range(
                        "gpu_util",
                        ns_pod[0],
                        ns_pod[1],
                        start=window_start.timestamp(),
                        end=window_end.timestamp(),
                        step="60s",
                    )
                    vram = await prom.query_range(
                        "vram_used_mb",
                        ns_pod[0],
                        ns_pod[1],
                        start=window_start.timestamp(),
                        end=window_end.timestamp(),
                        step="60s",
                    )
                except prom.PrometheusUnavailable:
                    logger.warning("usage_aggregation_prom_down", instance_id=inst_id)
                    return written
                utils = [v for _, v in values]
                async with sm() as session:
                    await session.execute(
                        pg_insert(UsageHourly)
                        .values(
                            instance_id=inst_id,
                            hour_start=window_start,
                            gpu_util_avg=(sum(utils) / len(utils)) if utils else None,
                            # p95 最近秩法:ceil(0.95n)-1
                            gpu_util_p95=(
                                sorted(utils)[max(0, math.ceil(len(utils) * 0.95) - 1)]
                                if utils
                                else None
                            ),
                            vram_max_mb=int(max((v for _, v in vram), default=0)) or None,
                        )
                        .on_conflict_do_nothing(index_elements=["instance_id", "hour_start"])
                    )
                    await session.commit()
                    written += 1
    return written


async def reconciliation_report(session: AsyncSession, day: datetime) -> dict[str, Any]:
    """日对账:事件计费合计 vs 指标估算合计 + diff%。diff>2% 列差异实例。

    指标估算 = usage_hourly 有数据的小时数 × 单价(存在性估算,粗对账抓大漏)。
    """
    from app.modules.billing import service as billing_service
    from app.modules.orchestrator import service as orchestrator_service

    day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    billed = await billing_service.billed_by_instance(session, day_start, day_end)
    usage_hours = (
        (
            await session.execute(
                select(UsageHourly.instance_id, func.count())
                .where(UsageHourly.hour_start >= day_start, UsageHourly.hour_start < day_end)
                .group_by(UsageHourly.instance_id)
            )
        )
        .tuples()
        .all()
    )
    usage_by_instance = dict(usage_hours)
    instances = await orchestrator_service.admin_list_instances(session)
    price_by_id = {i.id: as_amount(i.price_hourly * i.gpu_count) for i in instances}

    billed_total = sum((amount for _, amount in billed.items()), Decimal("0.00"))
    est_total = Decimal("0.00")
    diffs: list[dict[str, Any]] = []
    all_ids = set(billed) | set(usage_by_instance)
    for iid in all_ids:
        est = as_amount(price_by_id.get(iid, Decimal("0")) * usage_by_instance.get(iid, 0))
        est_total += est
        b = billed.get(iid, Decimal("0.00"))
        if b or est:
            base = max(b, est)
            diff_pct = float(abs(b - est) / base * 100) if base else 0.0
            if diff_pct > 2.0:
                diffs.append(
                    {
                        "instance_id": iid,
                        "billed": format(b, "f"),
                        "estimated": format(est, "f"),
                        "diff_pct": round(diff_pct, 1),
                    }
                )
    total_base = max(billed_total, est_total)
    total_diff_pct = float(abs(billed_total - est_total) / total_base * 100) if total_base else 0.0
    return {
        "day": day_start.date().isoformat(),
        "billed_total": format(billed_total, "f"),
        "estimated_total": format(est_total, "f"),
        "diff_pct": round(total_diff_pct, 1),
        "outliers": sorted(diffs, key=lambda d: -d["diff_pct"]),
    }
