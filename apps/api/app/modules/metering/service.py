"""用量服务:实例监控代理 + usage_hourly 聚合 + 事件计费 vs 指标估算对账。"""

import math
import re
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


async def instance_metrics(
    ns: str, pod: str, range_key: str, *, pool_label: str | None = None
) -> dict[str, Any]:
    """代理查询实例监控曲线。断源报 503(前端提示"监控暂不可用,不影响计费")。

    hami 池的 gpu_util/vram 走 HAMi 容器维指标(软切分下 DCGM per-pod 归属不可靠),
    查空自动回落 DCGM;cpu/mem 恒 cAdvisor。
    """
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
    return {"range": range_key, "series": series}


SUMMARY_CAP = 20  # 列表 sparkline 最多取前 N 台 running,防批量放大 Prometheus 压力


async def instances_gpu_summary(targets: list[tuple[str, str]]) -> InstanceMetricsSummaryOut:
    """批量取各实例近 1h gpu_util 稀疏序列(targets: [(uuid, ns)])。

    Prometheus 断源返回 available=false 而非抛错;单实例查询失败仅跳过该台。
    """
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
    """每小时 :05 聚合上一小时用量入 usage_hourly。幂等(UNIQUE DO NOTHING)。

    Prometheus 不可用时静默跳过(指标不参与计费)。
    """
    from app.modules.orchestrator import service as orchestrator_service

    window_start, window_end = prev_hour_range(at or now_utc())
    written = 0
    async with (
        sm() as lock_session,
        try_advisory_lock(lock_session, LockKey.USAGE_AGGREGATION) as got,
    ):
        if not got:
            return 0
        async with sm() as session:
            candidates = await orchestrator_service.billing_candidates(
                session, window_start, window_end
            )
            loc = await orchestrator_service.instance_locations(session, [c[0] for c in candidates])
        failed = 0
        for inst_id, _user_id, _price, _gpus in candidates:
            located = loc.get(inst_id)
            if located is None:  # 实例在候选查询与定位查询之间被删:跳过该台,不作废整轮
                continue
            ns, pod, pool_label = located
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
                vram = await prom.query_instance_metric(
                    "vram_used_mb",
                    ns,
                    pod,
                    pool_label=pool_label,
                    start=window_start.timestamp(),
                    end=window_end.timestamp(),
                    step="60s",
                )
                cpu = await prom.query_range(
                    "cpu_pct",
                    ns,
                    pod,
                    start=window_start.timestamp(),
                    end=window_end.timestamp(),
                    step="60s",
                )
            except prom.PrometheusUnavailable:
                # 单次抖动只丢该实例该小时:continue 保住整轮其它实例
                failed += 1
                logger.warning("usage_aggregation_prom_down", instance_id=inst_id)
                continue
            utils = [v for _, v in values]
            cpus = [v for _, v in cpu]
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
                        # vram 查空才是 None;max(...)=0 是合法值,不能用 or None 吞掉
                        vram_max_mb=int(max((v for _, v in vram), default=0)) if vram else None,
                        cpu_avg_pct=(sum(cpus) / len(cpus)) if cpus else None,
                    )
                    .on_conflict_do_nothing(index_elements=["instance_id", "hour_start"])
                )
                await session.commit()
                written += 1
        if failed:
            # 记缺口:该小时对这些实例永久缺失(任务只聚合上一小时),不自动回填
            # (指标不参与计费),需要时按 hour_start 人工补跑
            logger.warning(
                "usage_aggregation_partial",
                hour_start=window_start.isoformat(),
                written=written,
                failed=failed,
            )
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

    billed_total = sum((amount for _, amount in billed.items()), Decimal("0.00"))
    est_total = Decimal("0.00")
    diffs: list[dict[str, Any]] = []
    all_ids = set(billed) | set(usage_by_instance)
    # 按 id 精确取价:走管理端 LIMIT 200 列表会把截断外的实例按单价 0 估算,凭空造出差异
    price_by_id = await orchestrator_service.instance_hourly_prices(session, all_ids)
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


async def gpu_util_last_24h_by_instance(session: AsyncSession) -> dict[int, tuple[float, int]]:
    """近 24h 各实例 GPU 利用率聚合:instance_id → (sum(小时均值), 小时数)。

    供超卖报表按池加权平均(池归属在 orchestrator 侧,此处不跨模块查表);
    无数据返回空 dict。
    """
    from datetime import timedelta

    from sqlalchemy import func, select

    from app.core.timeutil import now_utc
    from app.modules.metering.models import UsageHourly

    since = now_utc() - timedelta(hours=24)
    rows = (
        await session.execute(
            select(UsageHourly.instance_id, func.sum(UsageHourly.gpu_util_avg), func.count())
            .where(UsageHourly.hour_start >= since, UsageHourly.gpu_util_avg.is_not(None))
            .group_by(UsageHourly.instance_id)
        )
    ).all()
    return {int(iid): (float(total), int(n)) for iid, total, n in rows}


NODE_METRIC_KEYS = ("util", "mem_used_mb", "temp")

# K8s 节点名(RFC1123 子域)。node_name 是 format 进 PromQL 的,不校验则可注入标签选择器
_NODE_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")


async def node_gpu_metrics(node_name: str, range_key: str) -> dict[str, Any]:
    """管理端节点每卡曲线(DCGM per-GPU 多序列)+ 24h XID 计数。

    断源降级为 available=False(200),对齐列表 sparkline 端点语义。
    """
    if range_key not in RANGES:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="metering.badRange")
    if not _NODE_NAME_RE.match(node_name):
        raise AppError(ErrorCode.VALIDATION_ERROR, key="metering.badNodeName")
    end = now_utc().timestamp()
    start = end - RANGES[range_key]
    step = prom.RANGE_STEPS[range_key]
    gpus: dict[str, dict[str, Any]] = {}
    try:
        for key in NODE_METRIC_KEYS:
            promql = prom.NODE_QUERIES[key].format(node=node_name)
            for gpu_index, points in await prom.query_range_multi(
                promql, start=start, end=end, step=step
            ):
                gpus.setdefault(gpu_index, {"index": gpu_index})[key] = points
        xid = await prom.query_instant(prom.NODE_XID_QUERY.format(node=node_name))
    except prom.PrometheusUnavailable:
        return {"available": False, "range": range_key, "gpus": [], "xid_count_24h": 0}
    return {
        "available": True,
        "range": range_key,
        "gpus": list(gpus.values()),
        "xid_count_24h": int(xid or 0),
    }
