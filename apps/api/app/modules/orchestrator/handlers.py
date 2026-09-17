"""outbox task handlers: the actual K8s side effects happen here. All idempotent
(at-least-once)."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.errors import AppError, ErrorCode
from app.core.k8s import (
    NodePortTaken,
    ensure_registry_pull_secret,
    get_orchestrator,
)
from app.core.k8s.base import data_disk_pvc_name
from app.core.logging import get_logger
from app.core.money import hourly_cost
from app.core.outbox import OutboxTask, RetryPolicy, outbox_handler
from app.core.pricing import MARKET_SUBSCRIPTION
from app.core.servercopy import copy as server_copy
from app.modules.billing import service as billing_service
from app.modules.notify import service as notify_service
from app.modules.orchestrator import statemachine as sm_def
from app.modules.orchestrator.models import DataDisk, Instance
from app.modules.orchestrator.ports import block_port, ensure_port
from app.modules.orchestrator.queries import lock_instance
from app.modules.orchestrator.service import build_pod_spec_with_cluster
from app.modules.orchestrator.transitions import transition

logger = get_logger(__name__)


async def _load(session: AsyncSession, task: OutboxTask) -> Instance | None:
    instance = await session.get(Instance, task.payload["instance_id"])
    if instance is None:
        logger.warning("outbox_instance_missing", task_id=task.id, payload=task.payload)
    return instance


async def _create_with_port_recovery(session: AsyncSession, instance: Instance) -> None:
    """Create Pod / Services / route; when the NodePort is held by another cluster object, mark the
    port blocked and retry."""
    orch = get_orchestrator()
    if instance.with_ssh:
        instance.ssh_port = await ensure_port(session, instance)
    await orch.ensure_namespace(instance.k8s_namespace)
    pull_secret = await ensure_registry_pull_secret(session, instance.k8s_namespace)
    try:
        await orch.create_instance(
            await build_pod_spec_with_cluster(session, instance, image_pull_secret=pull_secret)
        )
    except NodePortTaken as exc:
        instance_id = instance.id
        await session.rollback()
        await block_port(
            get_sessionmaker(),
            exc.port,
            reason="node port taken by cluster object",
            expected_instance_id=instance_id,
        )
        raise


async def _provision(session: AsyncSession, task: OutboxTask, expected: str) -> None:
    """create/start share this: create Pod / Services / route; status advancement is left to the
    reconciler."""
    instance = await _load(session, task)
    if instance is None or instance.status != expected:
        return
    await _create_with_port_recovery(session, instance)
    fresh = await session.get(Instance, instance.id, with_for_update=True)
    if fresh is None or fresh.status != expected:
        await session.rollback()


@outbox_handler("instance.create")
async def handle_create(session: AsyncSession, task: OutboxTask) -> None:
    await _provision(session, task, sm_def.CREATING)


@outbox_handler("instance.start")
async def handle_start(session: AsyncSession, task: OutboxTask) -> None:
    await _provision(session, task, sm_def.STARTING)


async def _delete_pod(session: AsyncSession, task: OutboxTask, expected: str) -> None:
    """stop/release share this: delete Pod / Services / route; the following edge completes once the
    reconciler sees the Pod gone."""
    instance = await _load(session, task)
    if instance is None or instance.status != expected:
        return
    await get_orchestrator().delete_instance(instance.k8s_namespace, instance.uuid)


@outbox_handler("instance.stop", retry=RetryPolicy(timeout_seconds=180))
async def handle_stop(session: AsyncSession, task: OutboxTask) -> None:
    await _delete_pod(session, task, sm_def.STOPPING)


@outbox_handler("instance.restart", retry=RetryPolicy(max_retries=8))
async def handle_restart(session: AsyncSession, task: OutboxTask) -> None:
    """Restart: stopping → delete Pod → wait for the object to vanish → stopped (tail bill) →
    balance
    check → starting → create Pod;
    a crash retry resumes from the current status."""
    instance = await _load(session, task)
    if instance is None:
        return
    orch = get_orchestrator()
    if instance.status == sm_def.STOPPING:
        await orch.delete_instance(instance.k8s_namespace, instance.uuid)
        st = await orch.get_status(instance.k8s_namespace, instance.uuid)
        if st.exists:
            raise RuntimeError(f"pod {instance.uuid} still terminating; restart resumes on retry")
        await transition(session, instance, sm_def.STOPPED, reason="restart", actor="system")
        await session.commit()
    if instance.status == sm_def.STOPPED:
        fresh_stopped = await lock_instance(session, instance.id)
        if fresh_stopped is None or fresh_stopped.status != sm_def.STOPPED:
            return
        instance = fresh_stopped
        try:
            if instance.market == MARKET_SUBSCRIPTION:
                await billing_service.assert_subscription_active(session, instance.id)
            else:
                estimate = hourly_cost(instance.price_hourly, instance.gpu_count)
                await billing_service.assert_can_afford(
                    session, instance.user_id, additional_hourly=estimate
                )
        except AppError as exc:
            if exc.code is ErrorCode.INSUFFICIENT_BALANCE:
                logger.warning("restart_aborted_insufficient_balance", instance_id=instance.id)
                await notify_service.notify(
                    session,
                    instance.user_id,
                    type_="instance",
                    title=server_copy("orchestrator.restart_no_balance.title"),
                    content=server_copy(
                        "orchestrator.restart_no_balance.content", name=instance.name
                    ),
                    severity="warning",
                    dedup_key=f"restart_no_balance:{instance.id}",
                    target_id=instance.uuid,
                )
                return
            if exc.code is ErrorCode.SUBSCRIPTION_EXPIRED:
                logger.warning("restart_aborted_subscription_expired", instance_id=instance.id)
                await notify_service.notify(
                    session,
                    instance.user_id,
                    type_="instance",
                    title=server_copy("orchestrator.restart_subscription_expired.title"),
                    content=server_copy(
                        "orchestrator.restart_subscription_expired.content", name=instance.name
                    ),
                    severity="warning",
                    dedup_key=f"restart_subscription_expired:{instance.id}",
                    target_id=instance.uuid,
                )
                return
            raise
        await transition(session, instance, sm_def.STARTING, reason="restart", actor="system")
        instance.unready_since = None
        await session.commit()
        await _create_with_port_recovery(session, instance)
    elif instance.status == sm_def.STARTING:
        await _create_with_port_recovery(session, instance)


@outbox_handler("instance.release", retry=RetryPolicy(timeout_seconds=300))
async def handle_release(session: AsyncSession, task: OutboxTask) -> None:
    await _delete_pod(session, task, sm_def.RELEASING)


@outbox_handler("instance.disk_cleanup", retry=RetryPolicy(max_retries=12, backoff_base_seconds=30))
async def handle_instance_disk_cleanup(session: AsyncSession, task: OutboxTask) -> None:
    """Deferred instance-disk reclamation (FAILED after a first_boot failure / RELEASED wrap-up):
    raise for retry while the Pod still exists;
    dead letters are re-dispatched by the reconciler."""
    instance = await _load(session, task)
    if instance is None or instance.status not in (sm_def.FAILED, sm_def.RELEASED):
        return
    orch = get_orchestrator()
    st = await orch.get_status(instance.k8s_namespace, instance.uuid)
    if st.exists:
        raise RuntimeError(f"pod {instance.uuid} still exists; disk cleanup resumes on retry")
    await orch.delete_instance_disk(instance.k8s_namespace, instance.uuid)


@outbox_handler("disk.provision", retry=RetryPolicy(max_retries=8, backoff_base_seconds=30))
async def handle_disk_provision(session: AsyncSession, task: OutboxTask) -> None:
    """Create or grow the data-disk PVC (capacity is the hard quota), mark provisioned on success;
    dead letters are re-dispatched by the reconciler."""
    disk = await session.get(DataDisk, task.payload["disk_id"])
    if disk is None or disk.status in ("deleting", "deleted"):
        return
    namespace = f"{get_settings().k8s_namespace_prefix}{disk.user_id}"
    await get_orchestrator().ensure_data_disk(
        namespace, data_disk_pvc_name(disk.uuid), disk.size_gb
    )
    disk.provisioned = True
    logger.info("disk_provisioned", disk_id=disk.id, capacity_gb=disk.size_gb)


@outbox_handler(
    "disk.deprovision",
    retry=RetryPolicy(max_retries=12, backoff_base_seconds=30, timeout_seconds=120),
)
async def handle_disk_deprovision(session: AsyncSession, task: OutboxTask) -> None:
    """Delete the data-disk PVC (reclaimPolicy=Delete, the CSI destroys the subvolume), then mark
    deleted."""
    disk = await session.get(DataDisk, task.payload["disk_id"])
    if disk is None or disk.status != "deleting":
        return
    namespace = f"{get_settings().k8s_namespace_prefix}{disk.user_id}"
    await get_orchestrator().delete_data_disk(namespace, data_disk_pvc_name(disk.uuid))
    logger.info("disk_deprovisioned", disk_id=disk.id)
    disk.status = "deleted"
    disk.mounted_instance_id = None
