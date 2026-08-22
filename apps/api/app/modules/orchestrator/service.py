"""编排服务:实例生命周期的唯一入口。

事务纪律:
- 状态变更只走 transition()(乐观锁 + 同事务 instance_events + 迁移监听器)
- 「改 DB + 动 K8s」一律 outbox;请求路径绝不直接调 K8s
"""

import hashlib
import hmac
import re
import secrets
import time
from collections.abc import Awaitable, Callable, Iterable
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from fastapi import status as http_status
from sqlalchemy import CursorResult, func, select, union, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt_str, encrypt_str, is_encrypted
from app.core.errors import AppError, ErrorCode, not_found
from app.core.gpu_adapter import spec_to_gpu_request
from app.core.gpu_models import canonical_gpu_model, model_matches
from app.core.k8s import InstancePodSpec, get_orchestrator
from app.core.logging import get_logger
from app.core.money import as_amount
from app.core.outbox import enqueue
from app.core.timeutil import ensure_utc, now_utc
from app.modules.account import service as account_service
from app.modules.billing import service as billing_service
from app.modules.catalog import service as catalog_service
from app.modules.nodes import service as nodes_service
from app.modules.notify import service as notify_service
from app.modules.orchestrator import statemachine as sm_def
from app.modules.orchestrator.models import DataDisk, Instance, InstanceEvent, PortAllocation
from app.modules.orchestrator.statemachine import validate_transition

if TYPE_CHECKING:
    from app.modules.catalog.models import Sku
    from app.modules.nodes.service import GpuModelAggregate

logger = get_logger(__name__)

# 幂等键有效期:窗口内重放返回既有资源;窗口外同一键按新单处理
IDEMPOTENCY_WINDOW = timedelta(hours=24)

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
            key="orchestrator.stateChangedRetry",
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
        # canonical 型号 → Pod nodeSelector(未识别型号存 None = 不钉);存量快照无此键
        "gpu_model_selector": canonical_gpu_model(sku.gpu_model),
    }


_SHARED_TIERS = ("shared_std", "shared_eco")


async def _require_cluster_for_tier(
    session: AsyncSession, tier: str | None, *, with_data_disk: bool = False
) -> None:
    """下发门禁:能力缺位即时 409,而非等 Pod Pending 到超时。

    HAMi 只有 shared 档依赖;StorageClass 实例盘人人要挂,数据盘按需。
    """
    if tier in _SHARED_TIERS:
        await nodes_service.require_hami_ready(session)
    await nodes_service.require_storage_classes(session, with_data_disk=with_data_disk)


# ---------- Jupyter token(密文落库;bootstrap 票据入场) ----------


def _encode_token(plaintext: str, *, instance_uuid: str) -> str:
    """落库形态:AES-GCM 密文,AAD 绑定实例 uuid(防密文跨实例搬运)。"""
    return encrypt_str(plaintext, aad=f"jupyter-token:{instance_uuid}")


def _token_plain(instance: Instance) -> str:
    """读出明文:enc:v1: 前缀走解密;存量明文行(加密上线前创建)原样返回。"""
    if is_encrypted(instance.jupyter_token):
        return decrypt_str(instance.jupyter_token, aad=f"jupyter-token:{instance.uuid}")
    return instance.jupyter_token


def _new_jupyter_ticket(instance: Instance, token_plain: str) -> str:
    """一次性入场票据:code(单次)+ 60s TTL + HMAC 签名(密钥=Jupyter token 本体)。

    票据 URL 落在实例自己的域名上,由镜像内 bootstrap handler 验签、核销并
    Set-Cookie 第一方会话 cookie;token 从此不进 URL(访问日志/浏览器历史/Referer)。
    验签密钥随 token 轮换(reset-jupyter-token),旧票据即全部作废。
    """
    settings = get_settings()
    code = secrets.token_urlsafe(12)
    exp = int(time.time()) + settings.jupyter_ticket_ttl_seconds
    sig = hmac.new(token_plain.encode(), f"{code}.{exp}".encode(), hashlib.sha256).hexdigest()
    return (
        f"https://{instance.uuid}.{settings.jupyter_domain_suffix}"
        f"/superdl-bootstrap?code={code}&exp={exp}&sig={sig}"
    )


# 容器镜像引用形态(域名[:端口]/路径[:tag][@sha256:...]);拒绝空格、大写等非法串
_IMAGE_REF_RE = re.compile(
    r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?"
    r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
    r"(?::[A-Za-z0-9_][A-Za-z0-9._-]{0,127})?"
    r"(?:@sha256:[0-9a-f]{64})?$"
)


async def _validate_image_ref(session: AsyncSession, image_ref: str) -> None:
    """镜像引用校验:先形态,再来源。

    来源白名单默认关。配置 SUPERDL_IMAGE_ALLOWED_REGISTRIES 后只放行平台镜像目录内的
    引用与白名单前缀;这是唯一的镜像来源闸门。
    """
    if not _IMAGE_REF_RE.match(image_ref):
        raise AppError(ErrorCode.VALIDATION_ERROR, key="orchestrator.imageRefInvalid")
    allowed = get_settings().image_allowed_registries
    if not allowed:
        return
    if any(image_ref.startswith(prefix) for prefix in allowed):
        return
    if await catalog_service.is_catalog_image(session, image_ref):
        return
    raise AppError(
        ErrorCode.VALIDATION_ERROR,
        key="orchestrator.imageRefNotAllowed",
        params={"registries": "、".join(allowed)},
    )


async def _check_user_quota(session: AsyncSession, user_id: int, new_gpus: int) -> None:
    """每用户配额(实例数 / GPU 总数);K8s 侧 ResourceQuota 为兜底。"""
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
            key="orchestrator.instanceQuota",
            params={"max": settings.max_instances_per_user},
        )
    if gpus + new_gpus > settings.max_gpus_per_user:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="orchestrator.gpuQuota",
            params={"max": settings.max_gpus_per_user},
        )


# ---------- 容量估算((池, 型号) 双维度,数据源是节点台账而非请求路径直连 K8s) ----------


def _sku_free_capacity(sku: "Sku", aggregates: list["GpuModelAggregate"]) -> tuple[int | None, int]:
    """该 SKU 的近似可分配量:返回 (匹配桶的 Ready 空闲卡合计, 折算后可售实例数)。

    第一项为 None 表示台账无此池×型号数据。台账(node_specs)只统计 Ready 节点:
    NotReady/Cordoned/Missing 不卖。共享档按算力份额折算可售实例数(超卖生效在调度层)。
    """
    wanted = canonical_gpu_model(sku.gpu_model)
    matching = [
        a
        for a in aggregates
        if a.pool_label == sku.pool_label and model_matches(wanted, a.gpu_model)
    ]
    if not matching:
        return None, 0
    free = sum(a.ready_gpu_free for a in matching)
    if sku.tier in _SHARED_TIERS:
        per_gpu = max(1, int(100 * float(sku.oversell_cores)) // max(1, sku.gpu_cores_pct))
        return free, free * per_gpu
    return free, free


async def _soft_admit_capacity(session: AsyncSession, sku: "Sku", gpu_count: int) -> None:
    """创建软准入:台账明确显示该 (池, 型号) 可分配量不足 → 即时 409。

    台账 60s 粒度,只是近似:无数据(巡检未覆盖/全新集群)一律放行,交调度器裁决;
    放行后仍可能调度超时转 failed,本判断只挡「确定卖不出去」的单。
    """
    aggregates = await nodes_service.gpu_model_aggregates(session)
    matching_free, sellable = _sku_free_capacity(sku, aggregates)
    if matching_free is None:
        return
    if sellable < gpu_count:
        raise AppError(
            ErrorCode.NO_CAPACITY,
            key="orchestrator.noCapacity",
            params={"model": sku.gpu_model, "pool": sku.pool_label},
            http_status=http_status.HTTP_409_CONFLICT,
        )


async def _pending_hourly(session: AsyncSession, user_id: int) -> Decimal:
    """该用户 creating/starting 实例的时费合计:尚未跑起来不算「在途」,
    assert_can_afford 看不到它们,由调用方并入 additional_hourly 防止连续开户绕过护栏。
    """
    rows = (
        (
            await session.execute(
                select(Instance.price_hourly, Instance.gpu_count).where(
                    Instance.user_id == user_id,
                    Instance.status.in_((sm_def.CREATING, sm_def.STARTING)),
                )
            )
        )
        .tuples()
        .all()
    )
    return sum((as_amount(price * count) for price, count in rows), Decimal("0.00"))


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
            if now_utc() - ensure_utc(existing.created_at) < IDEMPOTENCY_WINDOW:
                return existing
            # 窗口外同一键按新单处理:先释放键位(唯一约束 (user_id, idempotency_key))
            existing.idempotency_key = None
            await session.flush()

    sku = await catalog_service.get_on_sale_sku(session, sku_id)
    await _require_cluster_for_tier(session, sku.tier, with_data_disk=data_disk_id is not None)
    if gpu_count < 1 or gpu_count > sku.max_gpus_per_instance:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="orchestrator.gpuCountRange",
            params={"max": sku.max_gpus_per_instance},
        )
    await _validate_image_ref(session, image_ref)
    await _soft_admit_capacity(session, sku, gpu_count)

    # 临界区开始:FOR UPDATE 锁钱包行并持有到本事务 commit,同用户并发开户串行。
    # 在途统计与配额校验必须在锁内做(先算后锁即 TOCTOU)。
    # 余额口径:在途(running 实例 + 计费态盘)+ creating/starting 待燃 + 本次新增。
    estimate = as_amount(sku.price_hourly * gpu_count)
    try:
        await billing_service.lock_wallet(session, user_id)
        pending = await _pending_hourly(session, user_id)
        await billing_service.assert_can_afford(
            session, user_id, additional_hourly=as_amount(estimate + pending)
        )
        await _check_user_quota(session, user_id, gpu_count)

        keys = await account_service.list_ssh_keys(session, user_id)
        selected = [k.public_key for k in keys if k.id in set(ssh_key_ids)]
        if not selected:
            raise AppError(ErrorCode.SSH_KEY_INVALID, key="orchestrator.sshKeyRequired")

        disk_id_validated: int | None = None
        if data_disk_id is not None:
            from app.modules.orchestrator import disks as disks_service

            # 先校验归属与状态;实例 id 生成后再占用
            disk = await disks_service.get_disk_by_id_for_user(session, user_id, data_disk_id)
            disk_id_validated = disk.id

        instance_uuid = uuid4().hex
        jupyter_token = secrets.token_urlsafe(24)
        instance = Instance(
            uuid=instance_uuid,
            user_id=user_id,
            name=name or f"instance-{uuid4().hex[:6]}",
            sku_id=sku.id,
            spec=_snapshot_spec(sku),
            price_hourly=sku.price_hourly,
            gpu_count=gpu_count,
            image_ref=image_ref,
            status=sm_def.CREATING,
            k8s_namespace=f"{get_settings().k8s_namespace_prefix}{user_id}",
            jupyter_token=_encode_token(jupyter_token, instance_uuid=instance_uuid),
            authorized_keys=selected,
            data_disk_id=disk_id_validated,
            idempotency_key=idempotency_key,
        )
        session.add(instance)
        try:
            await session.flush()
        except IntegrityError:
            # 并发同幂等键:对方已落库,回滚后按重放返回既有实例(不多开一台)
            await session.rollback()
            raced = (
                await session.execute(
                    select(Instance).where(
                        Instance.user_id == user_id, Instance.idempotency_key == idempotency_key
                    )
                )
            ).scalar_one_or_none()
            if raced is not None:
                return raced
            raise
        if disk_id_validated is not None:
            from app.modules.orchestrator import disks as disks_service

            await disks_service.attach_for_instance(
                session, user_id, disk_id_validated, instance.id
            )
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
    except IntegrityError as exc:
        # 钱包首建与并发开户互撞唯一索引(locks 序列化前的瞬时竞争):可安全重试
        await session.rollback()
        raise AppError(
            ErrorCode.CONFLICT,
            key="common.retryableConflict",
            http_status=http_status.HTTP_409_CONFLICT,
        ) from exc
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


async def list_events(
    session: AsyncSession, instance_id: int, *, cursor: str | None = None, limit: int | None = None
):
    """实例事件时间线:降序(最新在前)游标分页,与资金流水/账单同一套分页语义。"""
    from app.core.pagination import Page, clamp_limit, decode_cursor_int, encode_cursor
    from app.modules.orchestrator.schemas import InstanceEventOut

    lim = clamp_limit(limit)
    stmt = (
        select(InstanceEvent)
        .where(InstanceEvent.instance_id == instance_id)
        .order_by(InstanceEvent.id.desc())
        .limit(lim + 1)
    )
    last_id = decode_cursor_int(cursor)
    if last_id is not None:
        stmt = stmt.where(InstanceEvent.id < last_id)
    rows = list((await session.execute(stmt)).scalars())
    next_cursor = encode_cursor(rows[lim - 1].id) if len(rows) > lim else None
    return Page[InstanceEventOut](
        items=[InstanceEventOut.model_validate(e) for e in rows[:lim]], next_cursor=next_cursor
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
        raise AppError(ErrorCode.INSTANCE_INVALID_TRANSITION, key="orchestrator.stopNeedsRunning")
    await transition(session, instance, sm_def.STOPPING, reason="user_stop", actor="user")
    enqueue(session, "instance.stop", {"instance_id": instance.id})
    await session.commit()
    return instance


async def _rebind_data_disk(session: AsyncSession, instance: Instance) -> None:
    """(重新)占用数据盘标记。failed 恢复开机时:失败边缘已解挂(detach),盘若还在就重新占用;
    盘已被用户删掉则放弃挂载点(系统盘数据仍在,实例照常能开)。"""
    if instance.data_disk_id is None:
        return
    disk = await session.get(DataDisk, instance.data_disk_id)
    if disk is None or disk.status == "deleted":
        instance.data_disk_id = None
        await session.flush()
        return
    if disk.mounted_instance_id == instance.id:
        return
    if disk.status != "active":
        raise AppError(ErrorCode.VALIDATION_ERROR, key="disks.notMountable")
    if disk.mounted_instance_id is not None:
        raise AppError(ErrorCode.DISK_IN_USE, key="disks.mountedElsewhere")
    disk.mounted_instance_id = instance.id
    await session.flush()


async def start_instance(session: AsyncSession, user_id: int, uuid: str) -> Instance:
    instance = await get_instance(session, user_id, uuid)
    if instance.status == sm_def.FROZEN:
        raise AppError(ErrorCode.INSTANCE_FROZEN, key="orchestrator.frozenNeedsRecharge")
    recovered = instance.status == sm_def.FAILED
    if instance.status != sm_def.STOPPED and not recovered:
        raise AppError(ErrorCode.INSTANCE_INVALID_TRANSITION, key="orchestrator.startNeedsStopped")
    await _require_cluster_for_tier(
        session, instance.spec.get("tier"), with_data_disk=instance.data_disk_id is not None
    )
    estimate = as_amount(instance.price_hourly * instance.gpu_count)
    await billing_service.assert_can_afford(session, user_id, additional_hourly=estimate)
    if recovered:
        # 故障恢复:failed → stopped(复用同一块实例盘)→ 走正常开机链路
        await transition(session, instance, sm_def.STOPPED, reason="failed_recover", actor="user")
        await _rebind_data_disk(session, instance)
    await transition(session, instance, sm_def.STARTING, reason="user_start", actor="user")
    enqueue(session, "instance.start", {"instance_id": instance.id})
    await session.commit()
    return instance


async def restart_instance(session: AsyncSession, user_id: int, uuid: str) -> Instance:
    instance = await get_instance(session, user_id, uuid)
    if instance.status != sm_def.RUNNING:
        raise AppError(
            ErrorCode.INSTANCE_INVALID_TRANSITION, key="orchestrator.restartNeedsRunning"
        )
    await _require_cluster_for_tier(
        session, instance.spec.get("tier"), with_data_disk=instance.data_disk_id is not None
    )
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
    if instance.status in (sm_def.RELEASING, sm_def.RELEASED):
        # 幂等释放:释放中/已释放直接回当前状态(照 delete_disk 的 deleting 写法),
        # 重试/双击不再报 400
        return instance
    if instance.status not in (
        sm_def.STOPPED,
        sm_def.FROZEN,
        sm_def.FAILED,  # 清理失败实例,同走 releasing→released
        sm_def.CREATING,  # 用户主动取消,不等 creating 超时
        sm_def.STOPPING,  # 关机悬挂:允许用户直接放弃(reconciler 超时强删兜底)
    ):
        raise AppError(ErrorCode.INSTANCE_NOT_STOPPED, key="orchestrator.releaseNeedsStopped")
    await transition(session, instance, sm_def.RELEASING, reason=f"{actor}_release", actor=actor)
    enqueue(session, "instance.release", {"instance_id": instance.id})
    await session.commit()
    return instance


# ---------- 端口池 ----------


async def ensure_port(session: AsyncSession, instance: Instance) -> int:
    """分配一个 SSH NodePort。已分配则原样返回(幂等)。

    端口池 30000–32767 与 K8s NodePort 同段,集群其它对象会硬占其中某些端口,两道防护:
    `ssh_port_excluded` 预先跳过已知占用;`blocked` 由 handle_create 在运行期撞占后标记。
    """
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
            .where(PortAllocation.instance_id.is_(None), PortAllocation.blocked.is_(False))
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
    while next_port in settings.ssh_port_excluded:
        next_port += 1
    if next_port > settings.ssh_port_range_end:
        raise AppError(ErrorCode.NO_CAPACITY, key="orchestrator.sshPortsExhausted")
    alloc = PortAllocation(port=next_port, instance_id=instance.id)
    session.add(alloc)
    await session.flush()
    return next_port


async def block_port(sm: Any, port: int, *, reason: str, expected_instance_id: int | None) -> None:
    """把一个被集群其它对象占用的端口标记为不可分配。独立事务提交(调用方那笔要回滚)。

    调用方须先 rollback 再调本函数,否则未提交的同端口 PortAllocation 会锁死这笔事务。
    防迟到的占用报告覆盖活分配:仅当该端口空闲、或正分配给发起本次报告的实例
    (expected_instance_id,重启换端口自愈路径)时才落 blocked;已分配给其它实例的
    端口说明报告已过时,跳过不破坏在用归属。
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    condition = PortAllocation.instance_id.is_(None)
    if expected_instance_id is not None:
        condition = condition | (PortAllocation.instance_id == expected_instance_id)
    async with sm() as session:
        await session.execute(
            pg_insert(PortAllocation)
            .values(port=port, instance_id=None, blocked=True)
            .on_conflict_do_update(
                index_elements=["port"],
                set_={"blocked": True, "instance_id": None},
                where=condition,
            )
        )
        await session.commit()
    logger.error("ssh_port_blocked", port=port, reason=reason)


async def free_port(session: AsyncSession, instance_id: int) -> None:
    await session.execute(
        update(PortAllocation)
        .where(PortAllocation.instance_id == instance_id)
        .values(instance_id=None)
    )


async def active_gpu_counts_by_sku(session: AsyncSession) -> dict[int, int]:
    """活跃实例按 SKU 的 GPU 张数合计(口径与用户配额一致:creating/starting/running)。"""
    from sqlalchemy import func

    rows = await session.execute(
        select(Instance.sku_id, func.coalesce(func.sum(Instance.gpu_count), 0))
        .where(Instance.status.in_((sm_def.CREATING, sm_def.STARTING, sm_def.RUNNING)))
        .group_by(Instance.sku_id)
    )
    return {sku_id: int(total) for sku_id, total in rows.all()}


# ---------- K8s spec 构造 ----------


def build_pod_spec(
    instance: Instance, *, distro: str | None = None, data_disk_subpath: str | None = None
) -> InstancePodSpec:
    """构造 Pod spec。data_disk_subpath 由调用方从盘记录读出后传入:
    subPath 的唯一事实源是 `data_disks.juicefs_subpath`,就地重算会与擦除路径对不上。"""
    settings = get_settings()
    gpu_req = spec_to_gpu_request(
        instance.spec,
        instance.gpu_count,
        hami_use_gputype=settings.hami_use_gputype,
        distro=distro,
    )
    if instance.ssh_port is None:
        raise RuntimeError("build_pod_spec requires allocated ssh_port")
    return InstancePodSpec(
        namespace=instance.k8s_namespace,
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
        env={
            "JUPYTER_TOKEN": _token_plain(instance),
            # 实例自己的域名:镜像据此收敛 Jupyter 的 Origin 校验(防跨站 WebSocket)
            "JUPYTER_ALLOW_ORIGIN": (f"https://{instance.uuid}.{settings.jupyter_domain_suffix}"),
        },
        authorized_keys=tuple(instance.authorized_keys),
        node_selector=gpu_req.node_selector,
        data_disk_subpath=data_disk_subpath,
        scheduler_name=gpu_req.scheduler_name,
        annotations=gpu_req.annotations,
    )


async def build_pod_spec_with_cluster(session: AsyncSession, instance: Instance) -> InstancePodSpec:
    """outbox handler 用:带集群发行版上下文(k3s → shared 档显式 runtimeClassName)
    与数据盘 subPath(从盘记录读,不就地重算)。"""
    row = await nodes_service.get_cluster_status(session)
    subpath: str | None = None
    if instance.data_disk_id is not None:
        disk = await session.get(DataDisk, instance.data_disk_id)
        if disk is None:
            raise RuntimeError(f"data disk {instance.data_disk_id} missing for {instance.uuid}")
        subpath = disk.juicefs_subpath
    return build_pod_spec(instance, distro=row.distro if row else None, data_disk_subpath=subpath)


# ---------- 接入信息 ----------


def build_access(instance: Instance) -> dict[str, Any]:
    settings = get_settings()
    if instance.status != sm_def.RUNNING:
        raise AppError(ErrorCode.INSTANCE_INVALID_TRANSITION, key="orchestrator.accessNeedsRunning")
    token_plain = _token_plain(instance)
    return {
        "ssh_host": settings.ssh_host,
        "ssh_port": instance.ssh_port,
        "ssh_command": f"ssh root@{settings.ssh_host} -p {instance.ssh_port}",
        # 一次性入场票据(单次、60s):浏览器打在实例域名的 bootstrap handler 上,
        # 验签核销后 Set-Cookie 第一方会话 cookie 再跳 Jupyter;token 不出现在 URL。
        "jupyter_url": _new_jupyter_ticket(instance, token_plain),
    }


async def reset_jupyter_token(session: AsyncSession, user_id: int, uuid: str) -> Instance:
    instance = await get_instance(session, user_id, uuid)
    instance.jupyter_token = _encode_token(secrets.token_urlsafe(24), instance_uuid=instance.uuid)
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


async def estimate_available_many(session: AsyncSession, skus: list["Sku"]) -> dict[int, int]:
    """市场近似库存(批量):sku_id → 可售实例数。

    数据源是节点台账(node_specs,巡检 60s 粒度),按 (池, canonical 型号) 双维度
    聚合 Ready 节点空闲卡;请求路径不碰 K8s,台账一次查询供全部 SKU。
    台账无该池×型号数据 → 0(与市场页「无货」语义一致)。
    """
    aggregates = await nodes_service.gpu_model_aggregates(session)
    return {sku.id: _sku_free_capacity(sku, aggregates)[1] for sku in skus}


# ---------- 管理端 ----------


async def admin_list_instances(
    session: AsyncSession,
    *,
    status_filter: str | None = None,
    user_id: int | None = None,
    q: str | None = None,
    node_name: str | None = None,
) -> list[Instance]:
    """管理端实例列表。q 按实例名或 uuid 前缀匹配,node_name 精确。"""
    # 固定截断,与 admin/components/ListCapNote.tsx 的 LIST_CAPS 对齐(表底给出「已达上限」)
    stmt = select(Instance).order_by(Instance.id.desc()).limit(200)
    if status_filter:
        stmt = stmt.where(Instance.status == status_filter)
    if user_id:
        stmt = stmt.where(Instance.user_id == user_id)
    if node_name:
        stmt = stmt.where(Instance.node_name == node_name)
    q = (q or "").strip()
    if q:
        # uuid 前缀可走索引;实例名是短串,量级由 limit 兜住
        stmt = stmt.where(Instance.uuid.like(f"{q}%") | Instance.name.ilike(f"%{q}%"))
    return list((await session.execute(stmt)).scalars())


async def admin_force_stop(session: AsyncSession, instance_uuid: str, *, reason: str) -> Instance:
    instance = (
        await session.execute(select(Instance).where(Instance.uuid == instance_uuid))
    ).scalar_one_or_none()
    if instance is None:
        raise not_found("实例不存在")
    if instance.status != sm_def.RUNNING:
        raise AppError(
            ErrorCode.INSTANCE_INVALID_TRANSITION, key="orchestrator.forceStopNeedsRunning"
        )
    await transition(
        session,
        instance,
        sm_def.STOPPING,
        reason="admin_force_stop",
        actor="admin",
        metadata={"admin_reason": reason},
    )
    enqueue(session, "instance.stop", {"instance_id": instance.id})
    await notify_service.notify(
        session,
        instance.user_id,
        type_="instance",
        title="实例已被管理员强制停止",
        content=f"实例「{instance.name}」已被强制停止并结算尾账。原因:{reason}",
        severity="warning",
    )
    await session.commit()
    return instance


# ---------- billing 只读接口(事件是计费主依据,经 service 层暴露) ----------


async def lock_instance_for_billing(session: AsyncSession, instance_id: int) -> None:
    """结算前先拿实例行锁,再读事件。同事务内重复加锁是 no-op。

    transition() 首步 `UPDATE instances` 持该行写锁到提交,事件 created_at 在拿锁后生成。
    先拿锁则:在飞的迁移已提交(读得到),或迁移被挡住(其事件落进下一个小时窗口)。
    锁序 instance → bill_hourly → wallet,与 transition 一致。
    """
    await session.execute(
        select(Instance.id)
        .where(Instance.id == instance_id)
        .with_for_update(read=False, key_share=True)
    )


async def billing_events_before(
    session: AsyncSession, instance_id: int, before: Any
) -> list[tuple[Any, str | None, str, Any]]:
    """实例截至某时刻的事件 (created_at, from_status, to_status, event_metadata),按发生序。

    metadata 随行返回:node_lost/pod_lost 的退出边带 unready_since,结算据此把
    计费截断到 Pod 首次不可用时点(平台责任时段不向用户计费)。
    """
    return list(
        (
            await session.execute(
                select(
                    InstanceEvent.created_at,
                    InstanceEvent.from_status,
                    InstanceEvent.to_status,
                    InstanceEvent.event_metadata,
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

    候选 = 当前 running 的实例 ∪ 自窗口起点以来离开过 running 的实例。
    完备性论证:「窗口末仍在 running」= 现在仍 running ∪ 窗口末之后才离开 running;
    每个已结束的 running 区间都有一条 from_status='running' 的离开事件。
    两条腿都走索引(instances.status / instance_events.created_at)。
    """
    running_now = select(Instance.id.label("iid")).where(Instance.status == sm_def.RUNNING)
    exited = (
        select(InstanceEvent.instance_id.label("iid"))
        .where(
            InstanceEvent.from_status == sm_def.RUNNING,
            InstanceEvent.created_at >= window_start,
        )
        .distinct()
    )
    candidates = [row[0] for row in (await session.execute(union(running_now, exited))).all()]
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


async def instance_locations(
    session: AsyncSession, instance_ids: Iterable[int]
) -> dict[int, tuple[str, str, str | None]]:
    """metering 聚合用:instance_id → (k8s_namespace, uuid, tier)。按 id 精确取,不走列表截断。"""
    ids = list(instance_ids)
    if not ids:
        return {}
    rows = (
        (
            await session.execute(
                select(Instance.id, Instance.k8s_namespace, Instance.uuid, Instance.spec).where(
                    Instance.id.in_(ids)
                )
            )
        )
        .tuples()
        .all()
    )
    return {iid: (ns, uuid, (spec or {}).get("tier")) for iid, ns, uuid, spec in rows}


async def instance_hourly_prices(
    session: AsyncSession, instance_ids: Iterable[int]
) -> dict[int, Any]:
    """对账用:instance_id → 单价 × 卡数(元/时)。按 id 精确取,不受列表截断影响。"""
    ids = list(instance_ids)
    if not ids:
        return {}
    rows = (
        (
            await session.execute(
                select(Instance.id, Instance.price_hourly, Instance.gpu_count).where(
                    Instance.id.in_(ids)
                )
            )
        )
        .tuples()
        .all()
    )
    return {iid: as_amount(price * count) for iid, price, count in rows}


async def instance_names(session: AsyncSession, instance_ids: Iterable[int]) -> dict[int, str]:
    """账单展示用:instance_id → 实例名(释放后行保留,改名跟当前名)。"""
    ids = list(instance_ids)
    if not ids:
        return {}
    rows = (
        (await session.execute(select(Instance.id, Instance.name).where(Instance.id.in_(ids))))
        .tuples()
        .all()
    )
    return dict(rows)


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


async def stop_all_for_user(session: AsyncSession, user_id: int, *, reason: str) -> int:
    """停掉该用户全部 running 实例(封禁/风控处置用)。同事务落事件 + outbox,不 commit。

    返回被停的台数。creating/starting 本轮停不了(状态机不允许),它们收敛到 running 后
    由巡检兜住(见 billing.patrol 的冻结用户处置)。
    """
    rows = list(
        (
            await session.execute(
                select(Instance).where(
                    Instance.user_id == user_id, Instance.status == sm_def.RUNNING
                )
            )
        ).scalars()
    )
    for inst in rows:
        await transition(
            session,
            inst,
            sm_def.STOPPING,
            reason=reason,
            actor="admin",
            metadata={"hint": "账号被冻结,实例已停机"},
        )
        enqueue(session, "instance.stop", {"instance_id": inst.id})
    if rows:
        logger.warning("tenant_frozen_instances_stopped", user_id=user_id, count=len(rows))
    return len(rows)


async def list_instances_by_status(session: AsyncSession, status: str) -> list[Instance]:
    return list(
        (await session.execute(select(Instance).where(Instance.status == status))).scalars()
    )


# ---------- 数据盘门面(billing/巡检经此访问,模块边界) ----------


async def billable_disks(session: AsyncSession) -> list[Any]:
    from app.modules.orchestrator import disks as disks_service

    return await disks_service.list_billable_disks(session)


async def arrears_chain_disk_user_ids(session: AsyncSession) -> list[int]:
    from app.modules.orchestrator import disks as disks_service

    return await disks_service.list_arrears_chain_user_ids(session)


async def disks_arrears_transition(session: AsyncSession, user_id: int, in_arrears: bool) -> int:
    from app.modules.orchestrator import disks as disks_service

    return await disks_service.arrears_transition_disks(session, user_id, in_arrears)


async def instance_disk_stats_by_user(
    session: AsyncSession, user_ids: list[int] | None = None
) -> dict[int, dict[str, int]]:
    """管理端租户表:user_id → {instances, disk_gb}。user_ids 给定则只聚合这些用户。"""
    inst_stmt = (
        select(Instance.user_id, func.count())
        .where(Instance.status != sm_def.RELEASED)
        .group_by(Instance.user_id)
    )
    disk_stmt = (
        select(DataDisk.user_id, func.coalesce(func.sum(DataDisk.size_gb), 0))
        .where(DataDisk.status != "deleted")
        .group_by(DataDisk.user_id)
    )
    if user_ids is not None:
        inst_stmt = inst_stmt.where(Instance.user_id.in_(user_ids))
        disk_stmt = disk_stmt.where(DataDisk.user_id.in_(user_ids))
    inst_rows = (await session.execute(inst_stmt)).tuples().all()
    disk_rows = (await session.execute(disk_stmt)).tuples().all()
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
        pool = inst.spec["pool_label"]
        share = inst.gpu_count * (inst.spec["gpu_cores_pct"] / 100.0)
        by_pool[pool] = by_pool.get(pool, 0.0) + share
    return by_pool


async def pool_by_instance(session: AsyncSession, instance_ids: Iterable[int]) -> dict[int, str]:
    """实例 → 池标签(不限状态,已释放实例也算:超卖报表按池聚合近 24h 利用率用)。"""
    ids = list(instance_ids)
    if not ids:
        return {}
    rows = (await session.execute(select(Instance).where(Instance.id.in_(ids)))).scalars()
    return {inst.id: inst.spec["pool_label"] for inst in rows}


async def cluster_nodes() -> list[Any]:
    return await get_orchestrator().list_nodes()
