"""编排服务:实例生命周期的唯一入口(门面)。

事务纪律:
- 状态变更只走 transition()(乐观锁 + 同事务 instance_events + 迁移监听器)
- 「改 DB + 动 K8s」一律 outbox;请求路径绝不直接调 K8s

拆分:状态迁移原语 → transitions.py;SSH 端口池 → ports.py;billing/管理端
查询聚合 → queries.py。本文件保留创建/操作/接入/日志主链路,并再导出全部拆出符号,
跨模块仍只经 app.modules.orchestrator.service 访问(lint-imports 契约不变)。
"""

import hashlib
import hmac
import re
import secrets
import time
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from fastapi import status as http_status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.crypto import decrypt_str, encrypt_str
from app.core.errors import AppError, ErrorCode, not_found
from app.core.gpu_adapter import spec_to_gpu_request
from app.core.gpu_models import canonical_gpu_model
from app.core.idempotency import IDEMPOTENCY_WINDOW, find_replay
from app.core.k8s import InstancePodSpec, get_orchestrator
from app.core.logging import get_logger
from app.core.money import as_amount
from app.core.outbox import enqueue
from app.core.pagination import RawPage
from app.core.platform_config import get_effective_platform_config
from app.core.ratelimit import check_rate_limit
from app.core.registry import effective_image_allowlist
from app.core.sqlutil import like_escape
from app.core.timeutil import now_utc
from app.modules.account import service as account_service
from app.modules.billing import service as billing_service
from app.modules.catalog import service as catalog_service
from app.modules.nodes import service as nodes_service
from app.modules.notify import service as notify_service
from app.modules.orchestrator import statemachine as sm_def
from app.modules.orchestrator.models import DataDisk, Instance, InstanceEvent
from app.modules.orchestrator.ports import (
    active_gpu_counts_by_sku as active_gpu_counts_by_sku,
)
from app.modules.orchestrator.ports import (
    block_port as block_port,
)
from app.modules.orchestrator.ports import (
    ensure_port as ensure_port,
)
from app.modules.orchestrator.ports import (
    free_port as free_port,
)
from app.modules.orchestrator.ports import (
    port_pool_stats as port_pool_stats,
)
from app.modules.orchestrator.queries import (
    arrears_chain_disk_user_ids as arrears_chain_disk_user_ids,
)
from app.modules.orchestrator.queries import (
    billable_disks as billable_disks,
)
from app.modules.orchestrator.queries import (
    billing_candidates as billing_candidates,
)
from app.modules.orchestrator.queries import (
    billing_events_before as billing_events_before,
)
from app.modules.orchestrator.queries import (
    deletion_leftover_counts as deletion_leftover_counts,
)
from app.modules.orchestrator.queries import (
    deletion_leftovers as deletion_leftovers,
)
from app.modules.orchestrator.queries import (
    disk_billing_snapshot as disk_billing_snapshot,
)
from app.modules.orchestrator.queries import (
    disks_arrears_transition as disks_arrears_transition,
)
from app.modules.orchestrator.queries import (
    instance_billing_snapshot as instance_billing_snapshot,
)
from app.modules.orchestrator.queries import (
    instance_disk_stats_by_user as instance_disk_stats_by_user,
)
from app.modules.orchestrator.queries import (
    instance_hourly_prices as instance_hourly_prices,
)
from app.modules.orchestrator.queries import (
    instance_locations as instance_locations,
)
from app.modules.orchestrator.queries import (
    instance_names as instance_names,
)
from app.modules.orchestrator.queries import (
    list_instances_by_status as list_instances_by_status,
)
from app.modules.orchestrator.queries import (
    list_running_instances_by_user as list_running_instances_by_user,
)
from app.modules.orchestrator.queries import (
    lock_instance_for_billing as lock_instance_for_billing,
)
from app.modules.orchestrator.queries import (
    pool_by_instance as pool_by_instance,
)
from app.modules.orchestrator.queries import (
    running_gpu_share_by_pool as running_gpu_share_by_pool,
)
from app.modules.orchestrator.transitions import (
    TransitionListener as TransitionListener,
)
from app.modules.orchestrator.transitions import (
    register_transition_listener as register_transition_listener,
)
from app.modules.orchestrator.transitions import (
    transition as transition,
)

if TYPE_CHECKING:
    from app.modules.catalog.models import Sku
    from app.modules.nodes.models import NodeSpec
    from app.modules.orchestrator.schemas import InstanceLogsOut

logger = get_logger(__name__)


def jupyter_host(instance_uuid: str, settings: Settings | None = None) -> str:
    """实例 Jupyter 主机名:<jupyter_host_prefix><uuid>.<jupyter_domain_suffix>。
    Ingress host / 入场 URL / JUPYTER_ALLOW_ORIGIN 三处必须同一口径,只从这里拼。"""
    s = settings or get_settings()
    return f"{s.jupyter_host_prefix}{instance_uuid}.{s.jupyter_domain_suffix}"


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
        # canonical 型号 → Pod nodeSelector(未识别型号存 None = 不钉)
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
    """读出明文(落库形态只有密文,见 _encode_token)。"""
    return decrypt_str(instance.jupyter_token, aad=f"jupyter-token:{instance.uuid}")


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
        f"https://{jupyter_host(instance.uuid, settings)}"
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

    来源白名单默认关(平台配置·镜像仓库 image_allowed_registries,Harbor 地址自动放行);
    配置后只放行平台镜像目录内的引用与白名单前缀;这是唯一的镜像来源闸门。
    """
    if not _IMAGE_REF_RE.match(image_ref):
        raise AppError(ErrorCode.VALIDATION_ERROR, key="orchestrator.imageRefInvalid")
    allowed = effective_image_allowlist(await get_effective_platform_config(session))
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
    """每用户配额(实例数 / GPU 总数);K8s 侧 ResourceQuota 为兜底。

    生效值走统一校验链(account.get_user_limits:用户覆盖 → 平台策略 → env 默认)。
    """

    limits = await account_service.get_user_limits(session, user_id)
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
    if count >= limits.max_instances:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="orchestrator.instanceQuota",
            params={"max": limits.max_instances},
        )
    if gpus + new_gpus > limits.max_gpus:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="orchestrator.gpuQuota",
            params={"max": limits.max_gpus},
        )


# ---------- 容量估算((池, 型号) 双维度,数据源是节点台账而非请求路径直连 K8s) ----------


def _sku_free_capacity(sku: "Sku", specs: list["NodeSpec"]) -> tuple[int | None, int]:
    """该 SKU 的近似可分配量:返回 (匹配台账行的 Ready 空闲卡合计, 折算后可售实例数)。

    第一项为 None 表示台账无此池×型号数据。只有 Ready 节点的空闲卡计入:
    NotReady/Cordoned/Missing 不卖。共享档按算力份额折算可售实例数(超卖生效在调度层),
    (池, 型号) 匹配与每卡可售数都走 nodes/catalog 的公共口径,与管理端容量预览同一份算法。
    """
    matching = nodes_service.matching_specs(
        specs, sku.pool_label, canonical_gpu_model(sku.gpu_model)
    )
    if not matching:
        return None, 0
    free = sum(max(0, s.gpu_count - s.gpu_used) for s in matching if s.status == "Ready")
    return free, free * catalog_service.sellable_per_gpu(
        sku.tier, sku.gpu_cores_pct, sku.oversell_cores
    )


async def _soft_admit_capacity(session: AsyncSession, sku: "Sku", gpu_count: int) -> None:
    """创建软准入:台账明确显示该 (池, 型号) 可分配量不足 → 即时 409。

    台账 60s 粒度,只是近似:无数据(巡检未覆盖/全新集群)一律放行,交调度器裁决;
    放行后仍可能调度超时转 failed,本判断只挡「确定卖不出去」的单。
    """
    specs = await nodes_service.list_node_specs(session)
    matching_free, sellable = _sku_free_capacity(sku, specs)
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
) -> tuple[Instance, bool]:
    """创建实例(202 异步)。返回 (实例, created):created=False = 幂等重放,
    路由据此回 200 + X-Idempotent-Replay 而非 202。"""
    if idempotency_key:
        existing = await find_replay(
            session,
            Instance,
            owner_col=Instance.user_id,
            owner_id=user_id,
            key=idempotency_key,
            window=IDEMPOTENCY_WINDOW,
        )
        if existing is not None:
            return existing, False

    sku = await catalog_service.get_on_sale_sku(session, sku_id)
    await _require_cluster_for_tier(session, sku.tier, with_data_disk=data_disk_id is not None)
    if gpu_count > sku.max_gpus_per_instance:
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
                await find_replay(
                    session,
                    Instance,
                    owner_col=Instance.user_id,
                    owner_id=user_id,
                    key=idempotency_key,
                )
                if idempotency_key
                else None
            )
            if raced is not None:
                return raced, False
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
    return instance, True


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


async def list_instances_page(
    session: AsyncSession,
    user_id: int,
    *,
    status: str | None = None,
    name: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
):
    """用户端实例列表:降序(最新在前)游标分页 + status 精确/name 模糊过滤。

    name 同时匹配 uuid 前缀(照 admin_list_instances 的 q 语义),与资金流水/账单
    同一套分页语义;released 终态永不出列表。
    """
    from app.core.pagination import Page, clamp_limit, decode_cursor_int, slice_page
    from app.modules.orchestrator.schemas import InstanceOut

    lim = clamp_limit(limit)
    stmt = (
        select(Instance)
        .where(Instance.user_id == user_id, Instance.status != sm_def.RELEASED)
        .order_by(Instance.id.desc())
        .limit(lim + 1)
    )
    if status is not None:
        stmt = stmt.where(Instance.status == status)
    name = (name or "").strip()
    if name:
        # uuid 前缀可走索引;实例名是短串,量级由 limit 兜住;
        # LIKE 元字符转义:name 里的 %/_ 按字面匹配,不当通配符
        stmt = stmt.where(
            Instance.name.ilike(f"%{like_escape(name)}%", escape="\\")
            | Instance.uuid.like(f"{like_escape(name)}%", escape="\\")
        )
    last_id = decode_cursor_int(cursor)
    if last_id is not None:
        stmt = stmt.where(Instance.id < last_id)
    rows = list((await session.execute(stmt)).scalars())
    page_items, next_cursor = slice_page(rows, lim, key=lambda r: r.id)
    return Page[InstanceOut](
        items=[InstanceOut.model_validate(i) for i in page_items], next_cursor=next_cursor
    )


async def list_events(
    session: AsyncSession, instance_id: int, *, cursor: str | None = None, limit: int | None = None
):
    """实例事件时间线:降序(最新在前)游标分页,与资金流水/账单同一套分页语义。"""
    from app.core.pagination import Page, clamp_limit, decode_cursor_int, slice_page
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
    page_items, next_cursor = slice_page(rows, lim, key=lambda r: r.id)
    return Page[InstanceEventOut](
        items=[InstanceEventOut.model_validate(e) for e in page_items], next_cursor=next_cursor
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
    盘已被用户删掉则放弃挂载点(系统盘数据仍在,实例照常能开)。
    FOR UPDATE 锁盘行:否则恢复开机与 delete_disk 并发时存在「边挂边擦」窗口。
    挂载校验(active / 配额已下发 / 未挂他处)与创建时同一入口 attach_for_instance,不另抄一份。"""
    if instance.data_disk_id is None:
        return
    disk = await session.get(DataDisk, instance.data_disk_id, with_for_update=True)
    if disk is None or disk.status == "deleted":
        instance.data_disk_id = None
        await session.flush()
        return
    from app.modules.orchestrator import disks as disks_service

    await disks_service.attach_for_instance(session, instance.user_id, disk.id, instance.id)


async def start_instance(session: AsyncSession, user_id: int, uuid: str) -> Instance:
    instance = await get_instance(session, user_id, uuid)
    if instance.status == sm_def.FROZEN:
        raise AppError(ErrorCode.INSTANCE_FROZEN, key="orchestrator.frozenNeedsRecharge")
    recovered = instance.status == sm_def.FAILED
    if instance.status != sm_def.STOPPED and not recovered:
        raise AppError(ErrorCode.INSTANCE_INVALID_TRANSITION, key="orchestrator.startNeedsStopped")
    # 实例盘钉在原节点(TopoLVM node affinity):节点失联(Missing)时开机会 Pending 到
    # 超时转 failed,前置拦截给可执行说明。台账无该行(巡检未覆盖/测试集群)一律放行,
    # 交调度器裁决(与软准入口径一致);NotReady 属瞬时态,不拦
    if instance.node_name:
        node = await nodes_service.get_node_spec(session, instance.node_name)
        if node is not None and node.status == "Missing":
            raise AppError(
                ErrorCode.INSTANCE_INVALID_TRANSITION,
                key="orchestrator.nodeUnreachable",
                http_status=409,
            )
    await _require_cluster_for_tier(
        session, instance.spec.get("tier"), with_data_disk=instance.data_disk_id is not None
    )
    estimate = as_amount(instance.price_hourly * instance.gpu_count)
    await billing_service.assert_can_afford(session, user_id, additional_hourly=estimate)
    if recovered:
        # 故障恢复:failed → stopped(复用同一块实例盘)→ 走正常开机链路
        await transition(session, instance, sm_def.STOPPED, reason="failed_recover", actor="user")
    # 所有开机路径统一校验数据盘挂载:盘已删则放弃挂载点(实例照常开),
    # 盘处于非 active(deleting/grace/frozen)即拒绝并提示,防止挂到擦除中的目录
    await _rebind_data_disk(session, instance)
    await transition(session, instance, sm_def.STARTING, reason="user_start", actor="user")
    # 新一轮就绪观察从零起算:陈旧 unready_since(上次失联 episode 的残留)会把
    # 新 running 段的计费截断到过去时刻,也会让 reconciler 的宽限判定立即超时
    instance.unready_since = None
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
    await transition(session, instance, sm_def.STOPPING, reason="restart", actor="user")
    enqueue(session, "instance.restart", {"instance_id": instance.id})
    await session.commit()
    return instance


async def release_instance(
    session: AsyncSession, user_id: int, uuid: str, *, actor: str = "user"
) -> Instance:
    instance = await get_instance(session, user_id, uuid)
    if instance.status in (sm_def.RELEASING, sm_def.RELEASED):
        # 幂等释放:释放中/已释放直接回当前状态(照 delete_disk 的 deleting 写法),
        # 重试/双击不报 400
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


# ---------- K8s spec 构造 ----------


def build_pod_spec(
    instance: Instance,
    *,
    distro: str | None = None,
    data_disk_subpath: str | None = None,
    image_pull_secret: str | None = None,
) -> InstancePodSpec:
    """构造 Pod spec。data_disk_subpath 由调用方从盘记录读出后传入:
    subPath 的唯一事实源是 `data_disks.juicefs_subpath`,就地重算会与擦除路径对不上。
    image_pull_secret 是平台已托管到该 ns 的拉取凭据 Secret 名(core/registry)。"""
    settings = get_settings()
    gpu_req = spec_to_gpu_request(
        instance.spec,
        instance.gpu_count,
        hami_use_gputype=settings.hami_use_gputype,
        distro=distro,
    )
    if instance.ssh_port is None:
        raise RuntimeError("build_pod_spec requires allocated ssh_port")
    # N 卡实例收 N 倍价,CPU/内存必须同步放大(Guaranteed QoS 下 CPU 是硬限,
    # 否则多卡被单份 CPU 饿死、节点侧资源被低估占用);系统盘不随卡数放大。
    gpu_n = max(1, instance.gpu_count)
    return InstancePodSpec(
        namespace=instance.k8s_namespace,
        name=instance.uuid,
        image=instance.image_ref,
        gpu_resources=gpu_req.resources,
        runtime_class=gpu_req.runtime_class,
        host_users=gpu_req.host_users,
        vcpu=instance.spec["vcpu"] * gpu_n,
        mem_gb=instance.spec["mem_gb"] * gpu_n,
        disk_gb=instance.spec["disk_gb"],
        ssh_node_port=instance.ssh_port,
        jupyter_host=jupyter_host(instance.uuid, settings),
        env={
            # 实例自己的域名:镜像据此收敛 Jupyter 的 Origin 校验(防跨站 WebSocket)
            "JUPYTER_ALLOW_ORIGIN": f"https://{jupyter_host(instance.uuid, settings)}",
        },
        # token 走 per-instance Secret(secretKeyRef),不以明文 env 落 Pod spec:
        # spec 会进 etcd/审计快照,任何 pods:get/list 身份(含只读 SA)都能读走
        secret_env={"JUPYTER_TOKEN": _token_plain(instance)},
        authorized_keys=tuple(instance.authorized_keys),
        node_selector=gpu_req.node_selector,
        data_disk_subpath=data_disk_subpath,
        scheduler_name=gpu_req.scheduler_name,
        annotations=gpu_req.annotations,
        image_pull_secret=image_pull_secret,
    )


async def build_pod_spec_with_cluster(
    session: AsyncSession, instance: Instance, *, image_pull_secret: str | None = None
) -> InstancePodSpec:
    """outbox handler 用:带集群发行版上下文(k3s → shared 档显式 runtimeClassName)
    与数据盘 subPath(从盘记录读,不就地重算)。"""
    row = await nodes_service.get_cluster_status(session)
    subpath: str | None = None
    if instance.data_disk_id is not None:
        disk = await session.get(DataDisk, instance.data_disk_id)
        if disk is None:
            raise RuntimeError(f"data disk {instance.data_disk_id} missing for {instance.uuid}")
        subpath = disk.juicefs_subpath
    return build_pod_spec(
        instance,
        distro=row.distro if row else None,
        data_disk_subpath=subpath,
        image_pull_secret=image_pull_secret,
    )


# ---------- 接入信息 ----------


def build_access(instance: Instance) -> dict[str, Any]:
    settings = get_settings()
    if instance.status != sm_def.RUNNING:
        raise AppError(ErrorCode.INSTANCE_INVALID_TRANSITION, key="orchestrator.accessNeedsRunning")
    token_plain = _token_plain(instance)
    # SSH 没有主机名语义(只靠 NodePort 区分实例),主机名直接用实例域名,与 Jupyter 同名
    ssh_host = jupyter_host(instance.uuid, settings)
    return {
        "ssh_host": ssh_host,
        "ssh_port": instance.ssh_port,
        "ssh_command": f"ssh root@{ssh_host} -p {instance.ssh_port}",
        # 一次性入场票据(单次、60s):浏览器打在实例域名的 bootstrap handler 上,
        # 验签核销后 Set-Cookie 第一方会话 cookie 再跳 Jupyter;token 不出现在 URL。
        "jupyter_url": _new_jupyter_ticket(instance, token_plain),
    }


async def reset_jupyter_token(session: AsyncSession, user_id: int, uuid: str) -> Instance:
    instance = await get_instance(session, user_id, uuid)
    instance.jupyter_token = _encode_token(secrets.token_urlsafe(24), instance_uuid=instance.uuid)
    # 需要重建 Pod 才生效(env 注入);running 时走 restart 流程
    if instance.status == sm_def.RUNNING:
        await transition(session, instance, sm_def.STOPPING, reason="restart", actor="user")
        enqueue(session, "instance.restart", {"instance_id": instance.id})
    await session.commit()
    return instance


# ---------- 容器日志 ----------
# 「请求路径绝不直接调 K8s」的例外:只读、用户在线等结果,走 outbox 语义不通。
# 代价由三道闸兜住:owner 校验、20/h/user 限流、K8s 读 5s 超时(real 侧 _request_timeout)。

LOGS_MAX_TAIL_LINES = 2000
LOGS_MAX_SINCE_SECONDS = 86400


async def read_instance_logs(
    session: AsyncSession,
    user_id: int,
    uuid: str,
    *,
    tail_lines: int,
    since_seconds: int | None,
) -> "InstanceLogsOut":
    """读取实例容器日志(只读;不记审计)。

    owner 校验(非属主 404,不暴露存在性);仅 running/stopping 可取(其余状态 Pod 已删,
    409 给明确文案);超上限参数按上限截断而非 422(tail_lines≤2000、since_seconds≤86400)。
    """
    from app.modules.orchestrator.schemas import InstanceLogsOut

    instance = await get_instance(session, user_id, uuid)
    if instance.status not in (sm_def.RUNNING, sm_def.STOPPING):
        raise AppError(
            ErrorCode.CONFLICT,
            key="orchestrator.logsNeedsRunning",
            http_status=http_status.HTTP_409_CONFLICT,
        )
    await check_rate_limit(f"instance-logs:{user_id}", max_attempts=20, window_seconds=3600.0)
    tail = min(tail_lines, LOGS_MAX_TAIL_LINES)
    since = min(since_seconds, LOGS_MAX_SINCE_SECONDS) if since_seconds is not None else None
    try:
        # +1 行探路:拿回的行数超过 tail 即知前面还有,truncated 标记由此而来
        raw = await get_orchestrator().read_instance_logs(
            instance.k8s_namespace, instance.uuid, tail_lines=tail + 1, since_seconds=since
        )
    except Exception as exc:
        logger.warning("instance_logs_read_failed", instance_uuid=uuid, error=str(exc))
        raise AppError(
            ErrorCode.INTERNAL,
            key="orchestrator.logsUnavailable",
            http_status=http_status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    lines = raw.splitlines()
    truncated = len(lines) > tail
    return InstanceLogsOut(lines=lines[-tail:] if truncated else lines, truncated=truncated)


# ---------- 近似库存 provider(注册进 catalog) ----------


async def estimate_available_many(session: AsyncSession, skus: list["Sku"]) -> dict[int, int]:
    """市场近似库存(批量):sku_id → 可售实例数。

    数据源是节点台账(node_specs,巡检 60s 粒度),按 (池, canonical 型号) 双维度
    聚合 Ready 节点空闲卡;请求路径不碰 K8s,台账一次查询供全部 SKU。
    台账无该池×型号数据 → 0(与市场页「无货」语义一致)。
    """
    specs = await nodes_service.list_node_specs(session)
    return {sku.id: _sku_free_capacity(sku, specs)[1] for sku in skus}


# ---------- 管理端 ----------


async def admin_list_instances(
    session: AsyncSession,
    *,
    status_filter: str | None = None,
    user_id: int | None = None,
    q: str | None = None,
    node_name: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> RawPage[Instance]:
    """管理端实例列表(游标分页,降序)。q 按实例名或 uuid 前缀匹配,node_name 精确。"""
    from app.core.pagination import clamp_limit, decode_cursor_int, slice_page

    lim = clamp_limit(limit)
    stmt = select(Instance).order_by(Instance.id.desc()).limit(lim + 1)
    if status_filter:
        stmt = stmt.where(Instance.status == status_filter)
    if user_id:
        stmt = stmt.where(Instance.user_id == user_id)
    if node_name:
        stmt = stmt.where(Instance.node_name == node_name)
    q = (q or "").strip()
    if q:
        # uuid 前缀可走索引;实例名是短串,量级由 limit 兜住;
        # LIKE 元字符转义:q 里的 %/_ 按字面匹配,不当通配符
        stmt = stmt.where(
            Instance.uuid.like(f"{like_escape(q)}%", escape="\\")
            | Instance.name.ilike(f"%{like_escape(q)}%", escape="\\")
        )
    last_id = decode_cursor_int(cursor)
    if last_id is not None:
        stmt = stmt.where(Instance.id < last_id)
    rows = list((await session.execute(stmt)).scalars())
    page_items, next_cursor = slice_page(rows, lim, key=lambda r: r.id)
    return RawPage(items=page_items, next_cursor=next_cursor)


async def admin_get_instance(session: AsyncSession, instance_uuid: str) -> Instance:
    """管理端按 uuid 取实例(不限租户);不存在 → 404。"""
    instance = (
        await session.execute(select(Instance).where(Instance.uuid == instance_uuid))
    ).scalar_one_or_none()
    if instance is None:
        raise not_found("实例不存在")
    return instance


async def admin_force_stop(session: AsyncSession, instance_uuid: str, *, reason: str) -> Instance:
    instance = await admin_get_instance(session, instance_uuid)
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


async def arrears_stop(session: AsyncSession, instance: Instance) -> None:
    """欠费停机(巡检调用,actor=system)。同事务落事件+outbox。"""
    await transition(session, instance, sm_def.STOPPING, reason="arrears_stop", actor="system")
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
    await transition(session, instance, sm_def.RELEASING, reason="arrears_reclaim", actor="system")
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
        await transition(session, inst, sm_def.STOPPING, reason=reason, actor="admin")
        enqueue(session, "instance.stop", {"instance_id": inst.id})
    if rows:
        logger.warning("tenant_frozen_instances_stopped", user_id=user_id, count=len(rows))
    return len(rows)
