from typing import Any

from fastapi import APIRouter

from app.core.db import DbSession
from app.modules.account.deps import CurrentUser
from app.modules.metering import service
from app.modules.metering.schemas import InstanceMetricsSummaryOut
from app.modules.orchestrator import service as orchestrator_service

router = APIRouter(tags=["metering"])


# 路径前缀避开 /instances/*:orchestrator 的 GET /instances/{uuid} 先注册,会把子路径当 uuid 吞掉
@router.get("/metrics/instances")
async def instances_metrics_summary(
    user: CurrentUser, session: DbSession
) -> InstanceMetricsSummaryOut:
    """本人 running 实例近 1h gpu_util 批量摘要(列表 sparkline)。

    断源降级为 available=false(200),详情端点维持 503 语义。
    """
    instances = await orchestrator_service.list_instances(session, user.id)
    targets = [(i.uuid, i.k8s_namespace) for i in instances if i.status == "running"]
    return await service.instances_gpu_summary(targets)


@router.get("/instances/{uuid}/metrics")
async def get_instance_metrics(
    uuid: str, user: CurrentUser, session: DbSession, range: str = "1h"
) -> dict[str, Any]:
    """实例监控曲线(代理 Prometheus,按租户隔离)。断源 503,不影响计费。"""
    instance = await orchestrator_service.get_instance(session, user.id, uuid)
    pool_label = instance.spec.get("pool_label")
    return await service.instance_metrics(
        instance.k8s_namespace, instance.uuid, range, pool_label=pool_label
    )
