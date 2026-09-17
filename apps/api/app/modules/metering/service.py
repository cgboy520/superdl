"""Usage service: instance monitoring proxy + usage_hourly aggregation + event billing vs metric
estimate reconciliation."""

import asyncio
import re
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError, ErrorCode
from app.core.k8s.base import ComponentFact
from app.core.locks import LockKey, advisory_lock
from app.core.logging import get_logger
from app.core.money import as_amount, money_str
from app.core.timeutil import now_utc, prev_hour_range
from app.modules.billing import service as billing_service
from app.modules.metering import prom
from app.modules.metering.models import UsageHourly
from app.modules.metering.schemas import (
    InstanceGpuSeries,
    InstanceMetricsOut,
    InstanceMetricsSummaryOut,
    NodeGpuSeriesOut,
    NodeMetricsOut,
    ReconciliationOut,
    ReconciliationOutlier,
)
from app.modules.orchestrator import queries as orchestrator_queries

logger = get_logger(__name__)

RANGES = {"1h": 3600, "6h": 6 * 3600, "24h": 24 * 3600}


async def instance_metrics(
    ns: str, pod: str, range_key: str, *, pool_label: str | None = None
) -> InstanceMetricsOut:
    """Proxy the instance monitoring curves, 503 when the source is down. In the hami pool gpu_util
    /
    vram use HAMi container metrics and fall back to DCGM when empty."""
    if range_key not in RANGES:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="metering.badRange")
    end = now_utc().timestamp()
    start = end - RANGES[range_key]
    step = prom.RANGE_STEPS[range_key]
    series: dict[str, list[tuple[float, float]]] = {}
    try:
        for metric in prom.QUERIES:
            series[metric] = await prom.query_instance_metric(
                metric, ns, pod, pool_label=pool_label, start=start, end=end, step=step
            )
    except prom.PrometheusUnavailable as exc:
        raise AppError(
            ErrorCode.INTERNAL,
            key="metering.unavailable",
            http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    return InstanceMetricsOut(range=range_key, series=series)


SUMMARY_CAP = 20


async def instances_gpu_summary(targets: list[tuple[str, str]]) -> InstanceMetricsSummaryOut:
    """Query the last hour's GPU series of the first SUMMARY_CAP (uuid, ns); any source failure
    returns unavailable with an empty list."""
    end = now_utc().timestamp()
    start = end - RANGES["1h"]
    items: list[InstanceGpuSeries] = []
    for uuid, ns in targets[:SUMMARY_CAP]:
        try:
            points = await prom.query_range("gpu_util", ns, uuid, start=start, end=end, step="300s")
        except prom.PrometheusUnavailable:
            return InstanceMetricsSummaryOut(available=False, items=[])
        items.append(
            InstanceGpuSeries(uuid=uuid, points=points, last=points[-1][1] if points else None)
        )
    return InstanceMetricsSummaryOut(available=True, items=items)


async def aggregate_previous_hour(
    sm: async_sessionmaker[AsyncSession], *, at: datetime | None = None
) -> int:
    """Aggregate the previous hour's usage under the advisory lock, committing per instance without
    overwriting existing rows; a down source skips the instance."""
    window_start, window_end = prev_hour_range(at or now_utc())
    written = 0
    async with advisory_lock(sm, LockKey.USAGE_AGGREGATION) as got:
        if not got:
            return 0
        async with sm() as session:
            candidates = await orchestrator_queries.billing_candidates(session, window_start)
            loc = await orchestrator_queries.instance_locations(session, [c[0] for c in candidates])
        failed = 0
        for inst_id, _user_id, _price, _gpus in candidates:
            ns, pod, pool_label = loc[inst_id]
            try:
                values = await prom.query_instance_metric(
                    "gpu_util",
                    ns,
                    pod,
                    pool_label=pool_label,
                    start=window_start.timestamp(),
                    end=window_end.timestamp(),
                    step="60s",
                )
            except prom.PrometheusUnavailable:
                failed += 1
                logger.warning("usage_aggregation_prom_down", instance_id=inst_id)
                continue
            utils = [v for _, v in values]
            async with sm() as session:
                await session.execute(
                    pg_insert(UsageHourly)
                    .values(
                        instance_id=inst_id,
                        hour_start=window_start,
                        gpu_util_avg=(sum(utils) / len(utils)) if utils else None,
                    )
                    .on_conflict_do_nothing(index_elements=["instance_id", "hour_start"])
                )
                await session.commit()
                written += 1
        if failed:
            logger.warning(
                "usage_aggregation_partial",
                hour_start=window_start.isoformat(),
                written=written,
                failed=failed,
            )
    return written


async def reconciliation_report(session: AsyncSession, day: datetime) -> ReconciliationOut:
    """Daily reconciliation: event billing total vs metric estimate (usage_hourly hours with data ×
    unit price) + diff %;
    diff > 2 % lists the divergent instances."""
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

    billed_total = sum((amount for _, amount in billed.items()), Decimal("0.00"))
    est_total = Decimal("0.00")
    diffs: list[ReconciliationOutlier] = []
    all_ids = set(billed) | set(usage_by_instance)
    price_by_id = await orchestrator_queries.instance_hourly_prices(session, all_ids)
    for iid in all_ids:
        est = as_amount(price_by_id.get(iid, Decimal("0")) * usage_by_instance.get(iid, 0))
        est_total += est
        b = billed.get(iid, Decimal("0.00"))
        if b or est:
            base = max(b, est)
            diff_pct = float(abs(b - est) / base * 100) if base else 0.0
            if diff_pct > 2.0:
                diffs.append(
                    ReconciliationOutlier(
                        instance_id=iid,
                        billed=money_str(b),
                        estimated=money_str(est),
                        diff_pct=round(diff_pct, 1),
                    )
                )
    total_base = max(billed_total, est_total)
    total_diff_pct = float(abs(billed_total - est_total) / total_base * 100) if total_base else 0.0
    return ReconciliationOut(
        day=day_start.date().isoformat(),
        billed_total=money_str(billed_total),
        estimated_total=money_str(est_total),
        diff_pct=round(total_diff_pct, 1),
        outliers=sorted(diffs, key=lambda d: -d.diff_pct),
    )


async def gpu_util_last_24h_by_instance(session: AsyncSession) -> dict[int, tuple[float, int]]:
    """GPU utilisation aggregate per instance over the last 24 h: instance_id → (sum of hourly
    means,
    hours); empty dict without data."""
    since = now_utc() - timedelta(hours=24)
    rows = (
        await session.execute(
            select(UsageHourly.instance_id, func.sum(UsageHourly.gpu_util_avg), func.count())
            .where(UsageHourly.hour_start >= since, UsageHourly.gpu_util_avg.is_not(None))
            .group_by(UsageHourly.instance_id)
        )
    ).all()
    return {int(iid): (float(total), int(n)) for iid, total, n in rows}


NODE_METRIC_KEYS: tuple[Literal["util", "mem_used_mb", "temp"], ...] = (
    "util",
    "mem_used_mb",
    "temp",
)

_NODE_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")


async def node_gpu_metrics(node_name: str, range_key: str) -> NodeMetricsOut:
    """Validate range and node name, then run the PromQL per-card curves and 24 h XID count; a down
    source returns unavailable."""
    if range_key not in RANGES:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="metering.badRange")
    if not _NODE_NAME_RE.fullmatch(node_name):
        raise AppError(ErrorCode.VALIDATION_ERROR, key="metering.badNodeName")
    end = now_utc().timestamp()
    start = end - RANGES[range_key]
    step = prom.RANGE_STEPS[range_key]
    gpus: dict[str, NodeGpuSeriesOut] = {}
    try:
        for key in NODE_METRIC_KEYS:
            promql = prom.NODE_QUERIES[key].format(node=node_name)
            for gpu_index, points in await prom.query_range_multi(
                promql, start=start, end=end, step=step
            ):
                setattr(gpus.setdefault(gpu_index, NodeGpuSeriesOut(index=gpu_index)), key, points)
        xid = await prom.query_instant(prom.NODE_XID_QUERY.format(node=node_name))
    except prom.PrometheusUnavailable:
        return NodeMetricsOut(available=False, range=range_key, gpus=[], xid_count_24h=0)
    return NodeMetricsOut(
        available=True, range=range_key, gpus=list(gpus.values()), xid_count_24h=int(xid or 0)
    )


async def cluster_component_metrics() -> dict[str, ComponentFact]:
    """Component metric facts that have data; an empty dict when Prometheus is unavailable."""
    try:
        age, up, total, firing = await asyncio.gather(
            prom.query_instant(prom.COMPONENT_QUERIES["dcgm_sample_age"]),
            prom.query_instant(prom.COMPONENT_QUERIES["scrape_up"]),
            prom.query_instant(prom.COMPONENT_QUERIES["scrape_total"]),
            prom.query_instant(prom.COMPONENT_QUERIES["alerts_firing"]),
        )
    except prom.PrometheusUnavailable:
        return {}
    out: dict[str, ComponentFact] = {}
    if age is not None:
        out["dcgm"] = ComponentFact(
            key="sampleAgeSeconds", value=str(int(age)), tone="warn" if age > 120 else "normal"
        )
    if up is not None and total is not None:
        out["monitoring"] = ComponentFact(
            key="scrapeTargets",
            value=f"{int(up)}/{int(total)}",
            tone="warn" if up < total else "normal",
        )
    if firing is not None:
        out["monitoring_alerts"] = ComponentFact(
            key="alertsFiring", value=str(int(firing)), tone="warn" if firing else "normal"
        )
    return out
