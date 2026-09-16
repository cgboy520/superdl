from fastapi import APIRouter

from app.core.db import DbSession
from app.modules.account.deps import CurrentUser
from app.modules.metering import service
from app.modules.metering.schemas import InstanceMetricsOut, InstanceMetricsSummaryOut
from app.modules.orchestrator import (
    queries as orchestrator_queries,
    service as orchestrator_service,
    statemachine as sm_def,
)

router = APIRouter(tags=["metering"])


@router.get("/metrics/instances")
async def instances_metrics_summary(
    user: CurrentUser, session: DbSession
) -> InstanceMetricsSummaryOut:
    """Batch summary of the caller's running instances' gpu_util over the last hour (list
    sparklines); source down → available=false (200)."""
    instances = await orchestrator_service.list_instances(session, user.id)
    targets = [(i.uuid, i.k8s_namespace) for i in instances if i.status == sm_def.RUNNING]
    await session.commit()
    return await service.instances_gpu_summary(targets)


@router.get("/instances/{uuid}/metrics")
async def get_instance_metrics(
    uuid: str, user: CurrentUser, session: DbSession, range: str = "1h"
) -> InstanceMetricsOut:
    """Instance monitoring curves (Prometheus proxy, tenant-isolated). Source down → 503, billing
    unaffected."""
    instance = await orchestrator_queries.get_instance(session, user.id, uuid)
    pool_label = instance.spec.get("pool_label")
    await session.commit()
    return await service.instance_metrics(
        instance.k8s_namespace, instance.uuid, range, pool_label=pool_label
    )
