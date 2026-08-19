from typing import Any

from fastapi import APIRouter

from app.core.db import DbSession
from app.modules.account.deps import CurrentUser
from app.modules.metering import service
from app.modules.orchestrator import service as orchestrator_service

router = APIRouter(tags=["metering"])


@router.get("/instances/{uuid}/metrics")
async def get_instance_metrics(
    uuid: str, user: CurrentUser, session: DbSession, range: str = "1h"
) -> dict[str, Any]:
    """实例监控曲线(代理 Prometheus,按租户隔离)。断源 503,不影响计费。"""
    instance = await orchestrator_service.get_instance(session, user.id, uuid)
    ns = instance.k8s_namespace or f"tenant-{user.id}"
    return await service.instance_metrics(ns, instance.uuid, range)
