"""编排服务:实例生命周期的唯一入口。

事务纪律:
- 状态变更只走 transition()(乐观锁 + 同事务 instance_events + 迁移监听器)
- 「改 DB + 动 K8s」一律 outbox;请求路径绝不直接调 K8s
"""

import secrets
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from fastapi import status as http_status
from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode, not_found
from app.core.gpu_adapter import spec_to_gpu_request
from app.core.k8s import InstancePodSpec, get_orchestrator
from app.core.logging import get_logger
from app.core.money import as_amount
from app.core.outbox import enqueue
from app.core.timeutil import now_utc
from app.modules.account import service as account_service
from app.modules.billing import service as billing_service
from app.modules.catalog import service as catalog_service
from app.modules.orchestrator import statemachine as sm_def
from app.modules.orchestrator.models import Instance, InstanceEvent, PortAllocation
from app.modules.orchestrator.statemachine import validate_transition

if TYPE_CHECKING:
    from app.modules.catalog.models import Sku

logger = get_logger(__name__)

# 迁移监听器:billing 注册尾账/计费边处理,与状态迁移同事务
TransitionListener = Callable[[AsyncSession, Instance, InstanceEvent], Awaitable[None]]
_transition_listeners: list[TransitionListener] = []


def register_transition_listener(listener: TransitionListener) -> None:
    _transition_listeners.append(listener)


async def transition(
    session: AsyncSession,
    instance: Instance,
    to_status: str,
    *,
    reason: str,
    actor: str,
    metadata: dict[str, Any] | None = None,
) -> InstanceEvent:
    """校验 + 乐观锁更新 + 落事件 + 触发监听器。不 commit,由调用方控制事务。"""
    from_status = instance.status
    validate_transition(from_status, to_status)
    result = cast(
        CursorResult[Any],
        await session.execute(
            update(Instance)
            .where(Instance.id == instance.id, Instance.version == instance.version)
            .values(status=to_status, version=instance.version + 1)
        ),
    )
    if result.rowcount == 0:
        raise AppError(
            ErrorCode.CONFLICT,
            "实例状态已被其他操作变更,请刷新后重试",
            http_status=http_status.HTTP_409_CONFLICT,
        )
    instance.status = to_status
    instance.version += 1
    event = InstanceEvent(
        instance_id=instance.id,
        from_status=from_status,
        to_status=to_status,
        reason=reason,
        actor=actor,
        event_metadata=metadata,
        created_at=now_utc(),  # 计费依赖精确时刻,显式生成而非 server_default
    )
    session.add(event)
    await session.flush()
    for listener in _transition_listeners:
        await listener(session, instance, event)
    return event


def _snapshot_spec(sku: "Sku") -> dict[str, Any]:
    return {
        "sku_name": sku.name,
        "gpu_model": sku.gpu_model,
        "tier": sku.tier,
        "mig_profile": sku.mig_profile,
        "gpu_cores_pct": sku.gpu_cores_pct,
        "vram_gb": sku.vram_gb,
        "vcpu": sku.vcpu,
        "mem_gb": sku.mem_gb,
        "disk_gb": sku.disk_gb,
        "pool_label": sku.pool_label,
        "cuda_max": sku.cuda_max,
    }


async def _check_user_quota(session: AsyncSession, user_id: int, new_gpus: int) -> None:
    """每用户配额(实例数 / GPU 总数):防单账号无限开机(K8s 侧 ResourceQuota 是兜底)。"""
    from sqlalchemy import func

    from app.core.config import get_settings

    settings = get_settings()
    live = (
        (
            await session.execute(
                select(func.count(), func.coalesce(func.sum(Instance.gpu_count), 0)).where(
                    Instance.user_id == user_id,
                    Instance.status.notin_(("released", "failed")),
                )
            )
        )
        .tuples()
        .one()
    )
    count, gpus = live
    if count >= settings.max_instances_per_user:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            f"实例数已达上限({settings.max_instances_per_user} 台),请释放后再创建或联系客服提额",
        )
    if gpus + new_gpus > settings.max_gpus_per_user:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            f"GPU 总数将超过上限({settings.max_gpus_per_user} 卡),请释放后再创建或联系客服提额",
        )


async def create_instance(
    session: AsyncSession,
    user_id: int,
    *,
    sku_id: int,
    gpu_count: int,
    image_ref: str,
    ssh_key_ids: list[int],
    name: str | None,
    data_disk_id: int | None,
    idempotency_key: str | None,
) -> Instance:
    if idempotency_key:
        existing = (
            await session.execute(
                select(Instance).where(
                    Instance.user_id == user_id, Instance.idempotency_key == idempotency_key
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    sku = await catalog_service.get_on_sale_sku(session, sku_id)
    if gpu_count < 1 or gpu_count > sku.max_gpus_per_instance:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            f"GPU 数量须在 1~{sku.max_gpus_per_instance} 之间",
        )
    await _check_user_quota(session, user_id, gpu_count)
    # 计费护栏:开机前校验余额 ≥ 1 小时预估费用
    estimate = as_amount(sku.price_hourly * gpu_count)
    await billing_service.require_balance_at_least(
        session, user_id, estimate, hint=f"开机需要至少 1 小时预估费用 ¥{estimate}"
    )

    keys = await account_service.list_ssh_keys(session, user_id)
    selected = [k.public_key for k in keys if k.id in set(ssh_key_ids)]
    if not selected:
        raise AppError(ErrorCode.SSH_KEY_INVALID, "请至少选择一个 SSH 公钥(实例仅支持密钥登录)")

    disk_id_validated: int | None = None
    if data_disk_id is not None:
        from app.modules.orchestrator import disks as disks_service

        # 先校验归属与状态;实例 id 生成后再占用
        disk = await disks_service.get_disk_by_id_for_user(session, user_id, data_disk_id)
        disk_id_validated = disk.id

    instance = Instance(
        uuid=uuid4().hex,
        user_id=user_id,
        name=name or f"instance-{uuid4().hex[:6]}",
        sku_id=sku.id,
        spec=_snapshot_spec(sku),
        price_hourly=sku.price_hourly,
        gpu_count=gpu_count,
        image_ref=image_ref,
        status=sm_def.CREATING,
        k8s_namespace=f"{get_settings().k8s_namespace_prefix}{user_id}",
        jupyter_token=secrets.token_urlsafe(24),
        authorized_keys=selected,
        data_disk_id=disk_id_validated,
        idempotency_key=idempotency_key,
    )
    session.add(instance)
    await session.flush()
    if disk_id_validated is not None:
        from app.modules.orchestrator import disks as disks_service

        await disks_service.attach_for_instance(session, user_id, disk_id_validated, instance.id)
    session.add(
        InstanceEvent(
            instance_id=instance.id,
            from_status=None,
            to_status=sm_def.CREATING,
            reason="create",
            actor="user",
            event_metadata={"sku_id": sku.id, "gpu_count": gpu_count},
            created_at=now_utc(),
        )
    )
    enqueue(session, "instance.create", {"instance_id": instance.id})
    await session.commit()
    logger.info("instance_create_accepted", instance_id=instance.id, user_id=user_id)
    return instance


async def get_instance(session: AsyncSession, user_id: int, uuid: str) -> Instance:
    instance = (
        await session.execute(
            select(Instance).where(Instance.uuid == uuid, Instance.user_id == user_id)
        )
    ).scalar_one_or_none()
    if instance is None:
        raise not_found("实例不存在")
    return instance


async def list_instances(session: AsyncSession, user_id: int) -> list[Instance]:
    return list(
        (
            await session.execute(
                select(Instance)
                .where(Instance.user_id == user_id, Instance.status != sm_def.RELEASED)
                .order_by(Instance.id.desc())
            )
        ).scalars()
    )


async def list_events(session: AsyncSession, instance_id: int) -> list[InstanceEvent]:
    return list(
        (
            await session.execute(
                select(InstanceEvent)
                .where(InstanceEvent.instance_id == instance_id)
                .order_by(InstanceEvent.id)
            )
        ).scalars()
    )


async def rename_instance(
    session: AsyncSession, user_id: int, uuid: str, new_name: str
) -> Instance:
    instance = await get_instance(session, user_id, uuid)
    instance.name = new_name
    await session.commit()
    return instance


# ---------- 用户操作 ----------


async def stop_instance(session: AsyncSession, user_id: int, uuid: str) -> Instance:
    instance = await get_instance(session, user_id, uuid)
    if instance.status != sm_def.RUNNING:
        raise AppError(ErrorCode.INSTANCE_INVALID_TRANSITION, "仅运行中的实例可以关机")
    await transition(session, instance, sm_def.STOPPING, reason="user_stop", actor="user")
    enqueue(session, "instance.stop", {"instance_id": instance.id})
    await session.commit()
    return instance


async def start_instance(session: AsyncSession, user_id: int, uuid: str) -> Instance:
    instance = await get_instance(session, user_id, uuid)
    if instance.status == sm_def.FROZEN:
        raise AppError(ErrorCode.INSTANCE_FROZEN, "实例已因欠费冻结,充值解冻后可开机")
    if instance.status != sm_def.STOPPED:
        raise AppError(ErrorCode.INSTANCE_INVALID_TRANSITION, "仅已关机的实例可以开机")
    estimate = as_amount(instance.price_hourly * instance.gpu_count)
    await billing_service.require_balance_at_least(
        session, user_id, estimate, hint=f"开机需要至少 1 小时预估费用 ¥{estimate}"
    )
    await transition(session, instance, sm_def.STARTING, reason="user_start", actor="user")
    enqueue(session, "instance.start", {"instance_id": instance.id})
    await session.commit()
    return instance


async def restart_instance(session: AsyncSession, user_id: int, uuid: str) -> Instance:
    instance = await get_instance(session, user_id, uuid)
    if instance.status != sm_def.RUNNING:
        raise AppError(ErrorCode.INSTANCE_INVALID_TRANSITION, "仅运行中的实例可以重启")
    await transition(
        session,
        instance,
        sm_def.STOPPING,
        reason="restart",
        actor="user",
        metadata={"restart": True},
    )
    enqueue(session, "instance.restart", {"instance_id": instance.id})
    await session.commit()
    return instance


async def release_instance(
    session: AsyncSession, user_id: int, uuid: str, *, actor: str = "user"
) -> Instance:
    instance = await get_instance(session, user_id, uuid)
    if instance.status not in (sm_def.STOPPED, sm_def.FROZEN, sm_def.FAILED):
        raise AppError(ErrorCode.INSTANCE_NOT_STOPPED, "关机后才能释放实例")
    if instance.status == sm_def.FAILED:
        # failed 已是终态:仅做资源清理与端口回收
        enqueue(session, "instance.release", {"instance_id": instance.id})
        await session.commit()
        return instance
    await transition(session, instance, sm_def.RELEASING, reason=f"{actor}_release", actor=actor)
    enqueue(session, "instance.release", {"instance_id": instance.id})
    await session.commit()
    return instance


# ---------- 端口池 ----------


async def ensure_port(session: AsyncSession, instance: Instance) -> int:
    settings = get_settings()
    mine = (
        await session.execute(
            select(PortAllocation).where(PortAllocation.instance_id == instance.id)
        )
    ).scalar_one_or_none()
    if mine is not None:
        return mine.port
    free = (
        await session.execute(
            select(PortAllocation)
            .where(PortAllocation.instance_id.is_(None))
            .order_by(PortAllocation.port)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if free is not None:
        free.instance_id = instance.id
        await session.flush()
        return free.port
    max_port = (await session.execute(select(func.max(PortAllocation.port)))).scalar_one()
    next_port = settings.ssh_port_range_start if max_port is None else max_port + 1
    if next_port > settings.ssh_port_range_end:
        raise AppError(ErrorCode.NO_CAPACITY, "SSH 端口池已耗尽,请联系管理员")
    alloc = PortAllocation(port=next_port, instance_id=instance.id)
    session.add(alloc)
    await session.flush()
    return next_port


async def free_port(session: AsyncSession, instance_id: int) -> None:
    await session.execute(
        update(PortAllocation)
        .where(PortAllocation.instance_id == instance_id)
        .values(instance_id=None)
    )


# ---------- K8s spec 构造 ----------


def build_pod_spec(instance: Instance) -> InstancePodSpec:
    settings = get_settings()
    gpu_req = spec_to_gpu_request(instance.spec, instance.gpu_count)
    if instance.ssh_port is None:
        raise RuntimeError("build_pod_spec requires allocated ssh_port")
    return InstancePodSpec(
        namespace=instance.k8s_namespace or f"{settings.k8s_namespace_prefix}{instance.user_id}",
        name=instance.uuid,
        image=instance.image_ref,
        gpu_resources=gpu_req.resources,
        runtime_class=gpu_req.runtime_class,
        host_users=gpu_req.host_users,
        vcpu=instance.spec["vcpu"],
        mem_gb=instance.spec["mem_gb"],
        disk_gb=instance.spec["disk_gb"],
        ssh_node_port=instance.ssh_port,
        jupyter_host=f"{instance.uuid}.{settings.jupyter_domain_suffix}",
        env={"JUPYTER_TOKEN": instance.jupyter_token},
        authorized_keys=tuple(instance.authorized_keys),
        node_selector=gpu_req.node_selector,
        data_disk_subpath=f"disk-{instance.data_disk_id}" if instance.data_disk_id else None,
    )


# ---------- 接入信息 ----------


def build_access(instance: Instance) -> dict[str, Any]:
    settings = get_settings()
    if instance.status != sm_def.RUNNING:
        raise AppError(ErrorCode.INSTANCE_INVALID_TRANSITION, "实例运行中才能获取接入信息")
    return {
        "ssh_host": settings.ssh_host,
        "ssh_port": instance.ssh_port,
        "ssh_command": f"ssh root@{settings.ssh_host} -p {instance.ssh_port}",
        "jupyter_url": (
            f"https://{instance.uuid}.{settings.jupyter_domain_suffix}/"
            f"?token={instance.jupyter_token}"
        ),
    }


async def reset_jupyter_token(session: AsyncSession, user_id: int, uuid: str) -> Instance:
    instance = await get_instance(session, user_id, uuid)
    instance.jupyter_token = secrets.token_urlsafe(24)
    # 需要重建 Pod 才生效(env 注入);running 时走 restart 流程
    if instance.status == sm_def.RUNNING:
        await transition(
            session,
            instance,
            sm_def.STOPPING,
            reason="restart",
            actor="user",
            metadata={"restart": True, "token_reset": True},
        )
        enqueue(session, "instance.restart", {"instance_id": instance.id})
    await session.commit()
    return instance


# ---------- 近似库存 provider(注册进 catalog) ----------


async def estimate_available(sku: "Sku") -> int:
    orch = get_orchestrator()
    free_gpus = await orch.available_gpus(sku.pool_label)
    if sku.tier in ("shared_std", "shared_eco"):
        # 共享档按算力份额折算可售实例数(近似;超卖参数生效在调度层)
        per_gpu = max(1, int(100 * float(sku.oversell_cores)) // max(1, sku.gpu_cores_pct))
        return free_gpus * per_gpu
    return free_gpus


def register_inventory_provider() -> None:
    from app.modules.catalog.inventory import register_inventory_provider as reg

    reg(estimate_available)  # type: ignore[arg-type]


# ---------- 管理端 ----------


async def admin_list_instances(
    session: AsyncSession, *, status_filter: str | None = None, user_id: int | None = None
) -> list[Instance]:
    stmt = select(Instance).order_by(Instance.id.desc()).limit(200)
    if status_filter:
        stmt = stmt.where(Instance.status == status_filter)
    if user_id:
        stmt = stmt.where(Instance.user_id == user_id)
    return list((await session.execute(stmt)).scalars())


async def admin_force_stop(session: AsyncSession, instance_uuid: str, *, reason: str) -> Instance:
    instance = (
        await session.execute(select(Instance).where(Instance.uuid == instance_uuid))
    ).scalar_one_or_none()
    if instance is None:
        raise not_found("实例不存在")
    if instance.status != sm_def.RUNNING:
        raise AppError(ErrorCode.INSTANCE_INVALID_TRANSITION, "仅运行中的实例可以强制停止")
    await transition(
        session,
        instance,
        sm_def.STOPPING,
        reason="admin_force_stop",
        actor="admin",
        metadata={"admin_reason": reason},
    )
    enqueue(session, "instance.stop", {"instance_id": instance.id})
    await session.commit()
    return instance


# ---------- billing 只读接口(事件是计费主依据,经 service 层暴露) ----------


async def billing_events_before(
    session: AsyncSession, instance_id: int, before: Any
) -> list[tuple[Any, str | None, str]]:
    """实例截至某时刻的事件 (created_at, from_status, to_status),按发生序。"""
    return list(
        (
            await session.execute(
                select(
                    InstanceEvent.created_at,
                    InstanceEvent.from_status,
                    InstanceEvent.to_status,
                )
                .where(
                    InstanceEvent.instance_id == instance_id,
                    InstanceEvent.created_at < before,
                )
                .order_by(InstanceEvent.id)
            )
        )
        .tuples()
        .all()
    )


async def billing_candidates(
    session: AsyncSession, window_start: Any, window_end: Any
) -> list[tuple[int, int, Any, int]]:
    """小时结算候选:(instance_id, user_id, price_hourly, gpu_count)。

    候选 = 截至窗口末仍在 running 的实例 ∪ 窗口内有事件的实例。
    """
    latest = (
        select(InstanceEvent.instance_id, InstanceEvent.to_status)
        .where(InstanceEvent.created_at < window_end)
        .distinct(InstanceEvent.instance_id)
        .order_by(InstanceEvent.instance_id, InstanceEvent.id.desc())
    ).subquery()
    a_ids = set(
        (
            await session.execute(
                select(latest.c.instance_id).where(latest.c.to_status == sm_def.RUNNING)
            )
        )
        .scalars()
        .all()
    )
    b_ids = set(
        (
            await session.execute(
                select(InstanceEvent.instance_id)
                .where(
                    InstanceEvent.created_at >= window_start,
                    InstanceEvent.created_at < window_end,
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    candidates = a_ids | b_ids
    if not candidates:
        return []
    return list(
        (
            await session.execute(
                select(
                    Instance.id, Instance.user_id, Instance.price_hourly, Instance.gpu_count
                ).where(Instance.id.in_(candidates))
            )
        )
        .tuples()
        .all()
    )


async def list_running_instances_by_user(session: AsyncSession) -> dict[int, list[Instance]]:
    """欠费巡检用:user_id → running 实例列表。"""
    rows = (
        (await session.execute(select(Instance).where(Instance.status == sm_def.RUNNING)))
        .scalars()
        .all()
    )
    by_user: dict[int, list[Instance]] = {}
    for inst in rows:
        by_user.setdefault(inst.user_id, []).append(inst)
    return by_user


async def arrears_stop(session: AsyncSession, instance: Instance) -> None:
    """欠费停机(巡检调用,actor=system)。同事务落事件+outbox。"""
    await transition(
        session,
        instance,
        sm_def.STOPPING,
        reason="arrears_stop",
        actor="system",
        metadata={"hint": "余额耗尽自动关机"},
    )
    enqueue(session, "instance.stop", {"instance_id": instance.id})


async def freeze_instance(session: AsyncSession, instance: Instance, deadline: Any) -> None:
    await transition(
        session,
        instance,
        sm_def.FROZEN,
        reason="arrears_freeze",
        actor="system",
        metadata={"deadline": deadline.isoformat()},
    )
    instance.frozen_deadline = deadline


async def unfreeze_instance(session: AsyncSession, instance: Instance) -> None:
    await transition(session, instance, sm_def.STOPPED, reason="recharge_unfreeze", actor="system")
    instance.frozen_deadline = None


async def reclaim_frozen(session: AsyncSession, instance: Instance) -> None:
    await transition(
        session,
        instance,
        sm_def.RELEASING,
        reason="arrears_reclaim",
        actor="system",
        metadata={"hint": "冻结 72 小时到期回收(数据盘不受影响)"},
    )
    instance.frozen_deadline = None
    enqueue(session, "instance.release", {"instance_id": instance.id})


async def list_instances_by_status(session: AsyncSession, status: str) -> list[Instance]:
    return list(
        (await session.execute(select(Instance).where(Instance.status == status))).scalars()
    )


# ---------- 数据盘门面(billing/巡检经此访问,模块边界) ----------


async def billable_disks(session: AsyncSession) -> list[Any]:
    from app.modules.orchestrator import disks as disks_service

    return await disks_service.list_billable_disks(session)


async def disks_arrears_transition(session: AsyncSession, user_id: int, in_arrears: bool) -> int:
    from app.modules.orchestrator import disks as disks_service

    return await disks_service.arrears_transition_disks(session, user_id, in_arrears)


async def instance_disk_stats_by_user(session: AsyncSession) -> dict[int, dict[str, int]]:
    """管理端租户表:user_id → {instances, disk_gb}。"""
    inst_rows = (
        (
            await session.execute(
                select(Instance.user_id, func.count())
                .where(Instance.status != sm_def.RELEASED)
                .group_by(Instance.user_id)
            )
        )
        .tuples()
        .all()
    )
    from app.modules.orchestrator.models import DataDisk

    disk_rows = (
        (
            await session.execute(
                select(DataDisk.user_id, func.coalesce(func.sum(DataDisk.size_gb), 0))
                .where(DataDisk.status != "deleted")
                .group_by(DataDisk.user_id)
            )
        )
        .tuples()
        .all()
    )
    stats: dict[int, dict[str, int]] = {}
    for uid, n in inst_rows:
        stats.setdefault(uid, {"instances": 0, "disk_gb": 0})["instances"] = int(n)
    for uid, gb in disk_rows:
        stats.setdefault(uid, {"instances": 0, "disk_gb": 0})["disk_gb"] = int(gb)
    return stats


async def running_gpu_share_by_pool(session: AsyncSession) -> dict[str, float]:
    """超卖报表:各池已售算力份额(等效整卡数)。共享档按 gpu_cores_pct 折算。"""
    rows = (
        (
            await session.execute(
                select(Instance).where(
                    Instance.status.in_((sm_def.RUNNING, sm_def.STARTING, sm_def.CREATING))
                )
            )
        )
        .scalars()
        .all()
    )
    by_pool: dict[str, float] = {}
    for inst in rows:
        pool = inst.spec.get("pool_label", "unknown")
        share = inst.gpu_count * (inst.spec.get("gpu_cores_pct", 100) / 100.0)
        by_pool[pool] = by_pool.get(pool, 0.0) + share
    return by_pool


async def cluster_nodes() -> list[Any]:
    return await get_orchestrator().list_nodes()
