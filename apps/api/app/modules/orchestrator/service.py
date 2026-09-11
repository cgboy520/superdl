"""编排服务门面:状态变更只走 transition(),改 DB + 动 K8s 一律 outbox。
再导出 transitions.py / ports.py / queries.py,跨模块只经本文件访问。
"""

import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from fastapi import status as http_status
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.crypto import decrypt_str, encrypt_str
from app.core.errors import AppError, ErrorCode, conflict, not_found
from app.core.gpu_adapter import POOL_HAMI, POOL_KATA, TIER_CPU, spec_to_gpu_request
from app.core.gpu_models import canonical_gpu_model
from app.core.idempotency import (
    IDEMPOTENCY_WINDOW,
    find_replay,
    insert_idempotent,
    request_fingerprint,
)
from app.core.k8s import InstancePodSpec, get_orchestrator
from app.core.logging import get_logger
from app.core.money import hourly_cost, money_str
from app.core.outbox import enqueue
from app.core.pagination import RawPage
from app.core.platform_config import get_effective_platform_config
from app.core.policies import get_effective_policies
from app.core.pricing import (
    MARKET_ON_DEMAND,
    MARKET_SPOT,
    MARKET_SUBSCRIPTION,
    price_for,
)
from app.core.ratelimit import check_rate_limit
from app.core.registry import (
    effective_image_allowlist,
    is_pinned_image_ref,
    is_valid_image_ref,
)
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
    billable_disks_of_user as billable_disks_of_user,
)
from app.modules.orchestrator.queries import (
    billing_candidates as billing_candidates,
)
from app.modules.orchestrator.queries import (
    billing_events_before as billing_events_before,
)
from app.modules.orchestrator.queries import (
    billing_history_exists_before as billing_history_exists_before,
)
from app.modules.orchestrator.queries import (
    count_instances_by_status as count_instances_by_status,
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
    instances_by_ids as instances_by_ids,
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
from app.modules.orchestrator.queries import (
    running_instances_of_user as running_instances_of_user,
)
from app.modules.orchestrator.queries import (
    running_spot_gpus_by_pool as running_spot_gpus_by_pool,
)
from app.modules.orchestrator.schemas import (
    WORKLOAD_DEV,
    WORKLOAD_SERVICE,
)
from app.modules.orchestrator.statemachine import (
    FAILED as FAILED,
)
from app.modules.orchestrator.statemachine import (
    FROZEN as FROZEN,
)
from app.modules.orchestrator.statemachine import (
    RELEASED as RELEASED,
)
from app.modules.orchestrator.statemachine import (
    RELEASING as RELEASING,
)
from app.modules.orchestrator.statemachine import (
    RUNNING as RUNNING,
)
from app.modules.orchestrator.statemachine import (
    STOPPED as STOPPED,
)
from app.modules.orchestrator.statemachine import (
    STOPPING as STOPPING,
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
    from app.modules.orchestrator.schemas import InstanceLogsOut, InstanceOut

logger = get_logger(__name__)


@dataclass(frozen=True)
class ServiceBinding:
    """建实例时绑定的在线服务版本:暴露规格快照到实例行,建 Pod 只读这些快照列。"""

    service_id: int
    revision: int
    slug: str
    service_port: int
    health_path: str | None


def jupyter_host(instance_uuid: str, settings: Settings | None = None) -> str:
    """实例 Jupyter 主机名:<jupyter_host_prefix><uuid>.<jupyter_domain_suffix>;唯一拼接点。"""
    s = settings or get_settings()
    return f"{s.jupyter_host_prefix}{instance_uuid}.{s.jupyter_domain_suffix}"


def jupyter_origin(instance_uuid: str, settings: Settings | None = None) -> str:
    """实例 Jupyter 的浏览器 origin(带端口);HTTPRoute hostname 与 SSH 连接串用 jupyter_host。"""
    s = settings or get_settings()
    host = jupyter_host(instance_uuid, s)
    return (
        f"https://{host}" if s.jupyter_url_port == 443 else f"https://{host}:{s.jupyter_url_port}"
    )


def service_endpoint_host(slug: str, settings: Settings | None = None) -> str:
    """服务端点主机名:<slug>.<service_domain_suffix>;唯一拼接点。"""
    s = settings or get_settings()
    return f"{slug}.{s.service_domain_suffix}"


def _snapshot_spec(sku: "Sku") -> dict[str, Any]:
    return {
        "sku_name": sku.name,
        # SKU 原价时价快照(字符串);竞价转按量据它还原原价
        "base_price_hourly": money_str(sku.price_hourly),
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
        # canonical 型号 → Pod nodeSelector(None = 不钉)
        "gpu_model_selector": canonical_gpu_model(sku.gpu_model),
    }


async def _require_cluster_for_pool(
    session: AsyncSession,
    pool_label: str | None,
    gpu_count: int,
    *,
    with_data_disk: bool = False,
) -> None:
    """下发门禁:集群能力缺位即 409。判据与 `build_gpu_request` 同源。
    gpu_count == 0 不查池;hami 池查 HAMi、kata 池查 RuntimeClass;StorageClass 必查,数据盘按需。
    """
    if gpu_count > 0:
        if pool_label == POOL_HAMI:
            await nodes_service.require_hami_ready(session)
        elif pool_label == POOL_KATA:
            await nodes_service.require_kata_runtimeclass(session)
    await nodes_service.require_storage_classes(session, with_data_disk=with_data_disk)


# ---------- Jupyter token(密文落库;bootstrap 票据入场) ----------


def _encode_token(plaintext: str, *, instance_uuid: str) -> str:
    """落库形态:AES-GCM 密文,AAD 绑定实例 uuid。"""
    return encrypt_str(plaintext, aad=f"jupyter-token:{instance_uuid}")


def _token_plain(instance: Instance) -> str:
    """读出明文(见 _encode_token)。"""
    return decrypt_str(instance.jupyter_token, aad=f"jupyter-token:{instance.uuid}")


def _env_aad(instance_uuid: str) -> str:
    return f"instance-env:{instance_uuid}"


def _encode_env(env: dict[str, str], secret_keys: set[str], *, instance_uuid: str) -> str:
    """用户环境变量的落库形态:明文项与密文项整包 JSON 的 AES-GCM 密文,AAD 绑实例 uuid。"""
    payload = {
        "plain": {k: v for k, v in env.items() if k not in secret_keys},
        "secret": {k: v for k, v in env.items() if k in secret_keys},
    }
    return encrypt_str(json.dumps(payload, ensure_ascii=False), aad=_env_aad(instance_uuid))


def instance_env(instance: Instance) -> tuple[dict[str, str], dict[str, str]]:
    """读出用户环境变量,返回 (明文项, 密文项)。未设置返回两个空字典。"""
    if not instance.env_encrypted:
        return {}, {}
    data = json.loads(decrypt_str(instance.env_encrypted, aad=_env_aad(instance.uuid)))
    return dict(data.get("plain") or {}), dict(data.get("secret") or {})


def _new_jupyter_ticket(instance: Instance, token_plain: str) -> str:
    """一次性入场票据:code(单次)+ TTL + HMAC 签名(密钥 = Jupyter token 本体)。
    镜像内 bootstrap handler 验签核销后 Set-Cookie;token 轮换即旧票据全部作废。
    """
    settings = get_settings()
    code = secrets.token_urlsafe(12)
    exp = int(time.time()) + settings.jupyter_ticket_ttl_seconds
    sig = hmac.new(token_plain.encode(), f"{code}.{exp}".encode(), hashlib.sha256).hexdigest()
    return (
        f"{jupyter_origin(instance.uuid, settings)}"
        f"/superdl-bootstrap?code={code}&exp={exp}&sig={sig}"
    )


async def _validate_image_ref(
    session: AsyncSession, image_ref: str, *, require_pinned: bool = False
) -> None:
    """镜像引用校验:形态(core.registry.is_valid_image_ref)→ 来源白名单(唯一闸门;
    image_allowed_registries 为空即不限,Harbor 地址与平台镜像目录恒放行)。
    """
    if not is_valid_image_ref(image_ref):
        raise AppError(ErrorCode.VALIDATION_ERROR, key="orchestrator.imageRefInvalid")
    # 服务型实例镜像必须钉版本
    if require_pinned and not is_pinned_image_ref(image_ref):
        raise AppError(ErrorCode.VALIDATION_ERROR, key="orchestrator.imageRefNotPinned")
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


async def _check_user_quota(
    session: AsyncSession,
    user_id: int,
    new_gpus: int,
    new_vcpus: int,
    *,
    exclude_instance_id: int | None = None,
) -> None:
    """每用户配额(实例数 / GPU 总数 / CPU 实例 vCPU 总数),生效值走 account.get_user_limits。
    GPU 实例只计 GPU 维、CPU 实例只计 vCPU 维;exclude_instance_id(即将被替换的旧实例)不占名额。
    """

    limits = await account_service.get_user_limits(session, user_id)
    policies = await get_effective_policies(session)
    stmt = select(
        func.count(),
        func.coalesce(func.sum(Instance.gpu_count), 0),
        # CPU 实例(gpu_count=0)的 vCPU 合计
        func.coalesce(
            func.sum(cast(Instance.spec["vcpu"].astext, Integer)).filter(Instance.gpu_count == 0),
            0,
        ),
    ).where(
        Instance.user_id == user_id,
        Instance.status.notin_(("released", "failed")),
    )
    if exclude_instance_id is not None:
        stmt = stmt.where(Instance.id != exclude_instance_id)
    live = (await session.execute(stmt)).tuples().one()
    count, gpus, vcpus = live
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
    if vcpus + new_vcpus > policies.max_vcpus_per_user:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="orchestrator.vcpuQuota",
            params={"max": policies.max_vcpus_per_user},
        )


# ---------- 容量估算((池, 型号) 双维度,数据源是节点台账) ----------


def _sku_free_capacity(
    sku: "Sku", specs: list["NodeSpec"], *, gpu_node_vcpu_cap: int
) -> tuple[int | None, int]:
    """该 SKU 的近似可分配量:(台账哨兵, 可售实例数);哨兵 None = 台账无数据,放行交调度器。
    GPU 档按 (池, canonical 型号) 匹配 Ready 空闲卡;CPU 档只按池匹配节点行数。
    与管理端容量预览同算法。
    """
    if sku.tier == TIER_CPU:
        matching = nodes_service.pool_specs(specs, sku.pool_label)
        if not matching:
            return None, 0
        return len(matching), catalog_service.sellable_cpu_slots(
            sku.vcpu, sku.mem_gb, matching, gpu_node_vcpu_cap=gpu_node_vcpu_cap
        )
    matching = nodes_service.matching_specs(
        specs, sku.pool_label, canonical_gpu_model(sku.gpu_model)
    )
    if not matching:
        return None, 0
    free = sum(max(0, s.gpu_count - s.gpu_used) for s in matching if s.status == "Ready")
    return free, free * catalog_service.sellable_per_gpu(
        sku.pool_label, sku.gpu_cores_pct, sku.oversell_cores
    )


async def _soft_admit_capacity(
    session: AsyncSession,
    sku: "Sku",
    gpu_count: int,
    *,
    market: str = MARKET_ON_DEMAND,
    user_id: int | None = None,
    freeing_slots: int = 0,
) -> None:
    """创建软准入:台账可分配量不足 → 先抢占竞价实例,仍不足则 409;无数据一律放行。
    抢占只对 GPU 档非竞价请求生效;freeing_slots = 同一请求里即将腾出的槽位,先加回可售数。
    """
    policies = await get_effective_policies(session)
    specs = await nodes_service.list_node_specs(session)
    matching_free, sellable = _sku_free_capacity(
        sku, specs, gpu_node_vcpu_cap=policies.gpu_node_cpu_instance_vcpu_cap
    )
    if matching_free is None:
        return
    sellable -= await _reserved_slots(session, sku)
    sellable += freeing_slots
    # GPU 实例按卡数占容量,CPU 实例占 1
    needed = gpu_count if gpu_count > 0 else 1
    if sellable < needed and market != MARKET_SPOT and sku.tier != TIER_CPU:
        from app.modules.orchestrator import preempt as preempt_mod

        if await preempt_mod.try_free_capacity(
            session,
            sku=sku,
            deficit_slots=needed - sellable,
            slots_per_card=catalog_service.sellable_per_gpu(
                sku.pool_label, sku.gpu_cores_pct, sku.oversell_cores
            ),
            grace_seconds=policies.spot_grace_seconds,
            requested_by=user_id or 0,
        ):
            return
    if sellable < needed:
        raise AppError(
            ErrorCode.NO_CAPACITY,
            key="orchestrator.noCapacityCpu" if sku.tier == TIER_CPU else "orchestrator.noCapacity",
            params=(
                {"pool": sku.pool_label}
                if sku.tier == TIER_CPU
                else {"model": sku.gpu_model, "pool": sku.pool_label}
            ),
            http_status=http_status.HTTP_409_CONFLICT,
        )


async def _reserved_slots_by_sku(session: AsyncSession, sku_ids: list[int]) -> dict[int, int]:
    """sku_id → 被未到期包周期实例(含停机/冻结)占住的槽位数,只算同一条 SKU。
    平台层预留、物理层不预留,见 docs/reference/billing.md。
    """
    if not sku_ids:
        return {}
    rows = (
        (
            await session.execute(
                select(Instance.id, Instance.sku_id, Instance.gpu_count).where(
                    Instance.sku_id.in_(sku_ids),
                    Instance.market == MARKET_SUBSCRIPTION,
                    Instance.status.in_((sm_def.STOPPED, sm_def.FROZEN)),
                )
            )
        )
        .tuples()
        .all()
    )
    if not rows:
        return {}
    reserved_ids = await billing_service.reserved_subscription_instance_ids(
        session, [iid for iid, _, _ in rows]
    )
    out: dict[int, int] = {}
    for instance_id, sku_id, gpus in rows:
        if instance_id in reserved_ids:
            out[sku_id] = out.get(sku_id, 0) + (gpus if gpus > 0 else 1)
    return out


async def _reserved_slots(session: AsyncSession, sku: "Sku") -> int:
    """单条 SKU 的包周期预留槽位(创建软准入用)。口径见 _reserved_slots_by_sku。"""
    return (await _reserved_slots_by_sku(session, [sku.id])).get(sku.id, 0)


async def pending_hourly(session: AsyncSession, user_id: int) -> Decimal:
    """该用户 creating/starting 实例的时费合计,由 wallet.assert_can_afford 内部并入。"""
    rows = (
        (
            await session.execute(
                select(Instance.price_hourly, Instance.gpu_count).where(
                    Instance.user_id == user_id,
                    Instance.status.in_((sm_def.CREATING, sm_def.STARTING)),
                    # 包周期实例已预付,不计
                    Instance.market != MARKET_SUBSCRIPTION,
                )
            )
        )
        .tuples()
        .all()
    )
    return sum((hourly_cost(price, count) for price, count in rows), Decimal("0.00"))


async def create_instance_row(
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
    container_command: list[str] | None = None,
    container_args: list[str] | None = None,
    env: dict[str, str] | None = None,
    env_secret_keys: list[str] | None = None,
    with_ssh: bool = False,
    market: str = MARKET_ON_DEMAND,
    period: str | None = None,
    period_count: int = 1,
    service: ServiceBinding | None = None,
    exclude_instance_id: int | None = None,
    fingerprint: str | None = None,
) -> tuple[Instance, bool]:
    """创建实例的 row 级核心:软准入 → 钱包行锁 → 写 instances / 事件 / outbox,**不 commit**。
    返回 (实例, created),created=False = 幂等重放。
    service 非空 = 在线服务的一个版本(镜像钉版本、暴露规格快照到实例行、SSH 按 with_ssh);
    dev 恒开 SSH。
    market='subscription' 同事务落 subscriptions 并一次性扣款(不许透支),再过在途燃烧率校验。
    exclude_instance_id 的份额让给新实例(配额与软准入),余额不让。
    fingerprint 由调用方给时须与 instance_fingerprint 同算法。
    """
    is_service = service is not None
    workload_type = WORKLOAD_SERVICE if is_service else WORKLOAD_DEV
    if fingerprint is None:
        fingerprint = instance_fingerprint(
            user_id,
            sku_id=sku_id,
            gpu_count=gpu_count,
            image_ref=image_ref,
            ssh_key_ids=ssh_key_ids,
            name=name,
            data_disk_id=data_disk_id,
            workload_type=workload_type,
            container_command=container_command,
            container_args=container_args,
            env=env,
            env_secret_keys=env_secret_keys,
            service_port=service.service_port if service else None,
            health_path=service.health_path if service else None,
            with_ssh=with_ssh,
            market=market,
            period=period,
            period_count=period_count,
        )
    if idempotency_key:
        existing = await find_instance_replay(
            session, user_id, key=idempotency_key, fingerprint=fingerprint
        )
        if existing is not None:
            return existing, False

    sku = await catalog_service.get_on_sale_sku(session, sku_id)
    await _require_cluster_for_pool(
        session, sku.pool_label, gpu_count, with_data_disk=data_disk_id is not None
    )
    # CPU 规格(max_gpus_per_instance=0)只收 0 卡,GPU 规格只收 1..max
    if sku.max_gpus_per_instance == 0:
        if gpu_count != 0:
            raise AppError(ErrorCode.VALIDATION_ERROR, key="orchestrator.cpuSkuNoGpu")
    elif not 1 <= gpu_count <= sku.max_gpus_per_instance:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="orchestrator.gpuCountRange",
            params={"max": sku.max_gpus_per_instance},
        )
    await _validate_image_ref(session, image_ref, require_pinned=is_service)
    if market == MARKET_SPOT and not sku.spot_enabled:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="orchestrator.spotNotEnabled")
    # 抢占与建实例同事务
    await _soft_admit_capacity(
        session,
        sku,
        gpu_count,
        market=market,
        user_id=user_id,
        freeing_slots=await _freeing_slots_of(session, exclude_instance_id, sku),
    )
    # dev 形态恒开 SSH;service 形态由用户勾选
    wants_ssh = with_ssh if is_service else True

    is_subscription = market == MARKET_SUBSCRIPTION
    if is_subscription and not sku.period_enabled:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="orchestrator.periodNotEnabled")
    # 有效时价:唯一折扣计算点在 core/pricing
    policies = await get_effective_policies(session)
    unit_price = price_for(sku.price_hourly, market=market, policies=policies, period=period)

    disk_id_validated: int | None = None
    if data_disk_id is not None:
        from app.modules.orchestrator import disks as disks_service

        # 锁序 disk → wallet(与删盘/扩盘链路同向);attach 在下方同事务重入此锁
        disk = await disks_service.lock_disk_for_attach(session, user_id, data_disk_id)
        disk_id_validated = disk.id

    # 临界区:FOR UPDATE 锁钱包行持有到 commit;在途统计与配额校验必须在锁内。
    # 余额口径:在途 + creating/starting 待燃(assert_can_afford 内部并入)+ 本次新增
    estimate = hourly_cost(unit_price, gpu_count)
    await billing_service.lock_wallet(session, user_id)
    if not is_subscription:
        await billing_service.assert_can_afford(session, user_id, additional_hourly=estimate)
    # 只有 CPU 实例计 vCPU 维
    await _check_user_quota(
        session,
        user_id,
        gpu_count,
        sku.vcpu if gpu_count == 0 else 0,
        exclude_instance_id=exclude_instance_id,
    )

    selected: list[str] = []
    if wants_ssh:
        keys = await account_service.list_ssh_keys(session, user_id)
        selected = [k.public_key for k in keys if k.id in set(ssh_key_ids)]
        if not selected:
            raise AppError(ErrorCode.SSH_KEY_INVALID, key="orchestrator.sshKeyRequired")

    instance_uuid = uuid4().hex
    jupyter_token = secrets.token_urlsafe(24)
    instance = Instance(
        uuid=instance_uuid,
        user_id=user_id,
        name=name or f"instance-{uuid4().hex[:6]}",
        sku_id=sku.id,
        spec=_snapshot_spec(sku),
        price_hourly=unit_price,
        gpu_count=gpu_count,
        market=market,
        image_ref=image_ref,
        status=sm_def.CREATING,
        k8s_namespace=f"{get_settings().k8s_namespace_prefix}{user_id}",
        # service 形态也签(列非空),不进 Pod spec
        jupyter_token=_encode_token(jupyter_token, instance_uuid=instance_uuid),
        authorized_keys=selected,
        data_disk_id=disk_id_validated,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        workload_type=workload_type,
        container_command=list(container_command) if container_command else None,
        container_args=list(container_args) if container_args else None,
        with_ssh=wants_ssh,
        env_encrypted=(
            _encode_env(env, set(env_secret_keys or ()), instance_uuid=instance_uuid)
            if env
            else None
        ),
        service_id=service.service_id if service else None,
        service_revision=service.revision if service else None,
        service_slug=service.slug if service else None,
        service_port=service.service_port if service else None,
        health_path=service.health_path if service else None,
    )
    result = await insert_idempotent(
        session,
        instance,
        model=Instance,
        owner_col=Instance.user_id,
        owner_id=user_id,
        key=idempotency_key,
        fingerprint=fingerprint,
    )
    if result is not instance:
        # 并发同幂等键:按重放返回既有实例
        return result, False
    if is_subscription:
        assert period is not None  # 契约层已拦,这里给类型收敛
        # 先扣款再校验在途(校验扣后余额)
        await billing_service.charge_new_subscription(
            session,
            user_id=user_id,
            instance_id=instance.id,
            instance_name=instance.name,
            sku_id=sku.id,
            base_hourly=sku.price_hourly,
            gpu_count=gpu_count,
            period=period,
            period_count=period_count,
            # 幂等由 instances 行担保,订阅行不带键
            idempotency_key=None,
        )
        await billing_service.assert_can_afford(session, user_id)
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
            event_metadata={
                "sku_id": sku.id,
                "gpu_count": gpu_count,
                "workload_type": workload_type,
                "market": market,
                **(
                    {"service_id": service.service_id, "revision": service.revision}
                    if service
                    else {}
                ),
            },
            created_at=now_utc(),
        )
    )
    enqueue(session, "instance.create", {"instance_id": instance.id})
    return instance, True


def instance_fingerprint(
    user_id: int,
    *,
    sku_id: int,
    gpu_count: int,
    image_ref: str,
    ssh_key_ids: list[int],
    name: str | None,
    data_disk_id: int | None,
    workload_type: str,
    container_command: list[str] | None,
    container_args: list[str] | None,
    env: dict[str, str] | None,
    env_secret_keys: list[str] | None,
    service_port: int | None,
    health_path: str | None,
    with_ssh: bool,
    market: str,
    period: str | None,
    period_count: int,
    extra: tuple[object, ...] = (),
) -> str:
    """异参检测指纹:下单参数全集的 sha256(dict 排序);服务部署经 extra 并入服务级属性。"""
    return request_fingerprint(
        user_id,
        sku_id,
        gpu_count,
        image_ref,
        sorted(ssh_key_ids),
        name,
        data_disk_id,
        workload_type,
        container_command,
        container_args,
        sorted(env.items()) if env else None,
        sorted(env_secret_keys) if env_secret_keys else None,
        service_port,
        health_path,
        with_ssh,
        market,
        period,
        period_count,
        *extra,
    )


async def find_instance_replay(
    session: AsyncSession, user_id: int, *, key: str, fingerprint: str
) -> Instance | None:
    """同 (user, Idempotency-Key) 24h 窗内的既有实例;同键异参 409。"""
    return await find_replay(
        session,
        Instance,
        owner_col=Instance.user_id,
        owner_id=user_id,
        key=key,
        window=IDEMPOTENCY_WINDOW,
        fingerprint=fingerprint,
    )


async def lock_instance(session: AsyncSession, instance_id: int) -> Instance | None:
    """FOR UPDATE 锁实例行并重读;不存在返回 None。"""
    return await session.get(Instance, instance_id, with_for_update=True, populate_existing=True)


async def instances_of_service(session: AsyncSession, service_id: int) -> list[Instance]:
    """某在线服务的全部版本实例(含已释放),版本号降序。"""
    return list(
        (
            await session.execute(
                select(Instance)
                .where(Instance.service_id == service_id)
                .order_by(Instance.service_revision.desc(), Instance.id.desc())
            )
        ).scalars()
    )


async def _freeing_slots_of(session: AsyncSession, instance_id: int | None, sku: "Sku") -> int:
    """即将被替换的旧实例占的可售份额:running 且同池同 canonical 型号才算。"""
    if instance_id is None:
        return 0
    old = await session.get(Instance, instance_id)
    if old is None or old.status != sm_def.RUNNING or old.gpu_count == 0:
        return 0
    if old.spec.get("pool_label") != sku.pool_label or canonical_gpu_model(
        str(old.spec.get("gpu_model") or "")
    ) != canonical_gpu_model(sku.gpu_model):
        return 0
    return old.gpu_count * catalog_service.sellable_per_gpu(
        sku.pool_label, sku.gpu_cores_pct, sku.oversell_cores
    )


async def create_instance(
    session: AsyncSession, user_id: int, **kwargs: Any
) -> tuple[Instance, bool]:
    """创建实例(202 异步):create_instance_row + commit。created=False = 幂等重放(路由回 200)。"""
    instance, created = await create_instance_row(session, user_id, **kwargs)
    if created:
        await session.commit()
        logger.info("instance_create_accepted", instance_id=instance.id, user_id=user_id)
    return instance, created


async def instance_by_id(session: AsyncSession, instance_id: int) -> Instance:
    """按主键取实例(不限归属与状态);系统侧用,用户请求走 get_instance。"""
    return (await session.execute(select(Instance).where(Instance.id == instance_id))).scalar_one()


async def instance_status(session: AsyncSession, instance_id: int) -> str | None:
    """按主键取实例状态;不存在返回 None(不抛)。"""
    instance = await session.get(Instance, instance_id)
    return None if instance is None else instance.status


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
    service_id: int | None = None,
    include_released: bool = False,
):
    """用户端实例列表:降序游标分页,status 精确 / name 模糊(含 uuid 前缀)。
    默认只列开发机;给 service_id 即该服务的版本实例(版本号降序),include_released 含已释放。"""
    from app.core.pagination import Page, paginate_by_id
    from app.modules.orchestrator.schemas import InstanceOut

    stmt = select(Instance).where(Instance.user_id == user_id)
    if service_id is None:
        stmt = stmt.where(Instance.service_id.is_(None)).order_by(Instance.id.desc())
    else:
        stmt = stmt.where(Instance.service_id == service_id).order_by(Instance.id.desc())
    if not include_released:
        stmt = stmt.where(Instance.status != sm_def.RELEASED)
    if status is not None:
        stmt = stmt.where(Instance.status == status)
    name = (name or "").strip()
    if name:
        # LIKE 元字符转义
        stmt = stmt.where(
            Instance.name.ilike(f"%{like_escape(name)}%", escape="\\")
            | Instance.uuid.like(f"{like_escape(name)}%", escape="\\")
        )
    page_items, next_cursor = await paginate_by_id(
        session, stmt, id_col=Instance.id, cursor=cursor, limit=limit
    )
    items = [InstanceOut.model_validate(i) for i in page_items]
    await attach_instance_details(session, items)
    return Page[InstanceOut](items=items, next_cursor=next_cursor)


async def attach_instance_details(session: AsyncSession, items: "Sequence[InstanceOut]") -> None:
    """回填包周期概要:一次批量查询。列表页、详情页、服务视图共用。"""
    await _attach_subscriptions(session, items)


async def list_expiring_instances(
    session: AsyncSession, user_id: int, *, within_days: int
) -> "list[InstanceOut]":
    """临期包周期实例:active 订阅且 expires_at ≤ now+within_days,按到期升序,不分页。"""
    from app.modules.orchestrator.schemas import InstanceOut

    subs = await billing_service.list_expiring_subscriptions(
        session, user_id, within_days=within_days
    )
    if not subs:
        return []
    instances = list(
        (
            await session.execute(
                select(Instance).where(Instance.id.in_([s.instance_id for s in subs]))
            )
        ).scalars()
    )
    by_id = {i.id: i for i in instances}
    items = [
        InstanceOut.model_validate(by_id[s.instance_id]) for s in subs if s.instance_id in by_id
    ]
    await attach_instance_details(session, items)
    return items


async def instance_view(session: AsyncSession, instance: Instance) -> "InstanceOut":
    """单实例出参,与列表项同形。"""
    from app.modules.orchestrator.schemas import InstanceOut

    items = [InstanceOut.model_validate(instance)]
    await attach_instance_details(session, items)
    return items[0]


async def _attach_subscriptions(session: AsyncSession, items: "Sequence[InstanceOut]") -> None:
    """给列表项回填包周期概要:一次查询。"""
    from app.modules.orchestrator.schemas import InstanceSubscriptionOut

    ids = [i.id for i in items if i.market == MARKET_SUBSCRIPTION]
    if not ids:
        return
    rows = await billing_service.subscriptions_by_instance(session, ids)
    for item in items:
        row = rows.get(item.id)
        if row is not None:
            item.subscription = InstanceSubscriptionOut.model_validate(row)


async def list_events_raw(
    session: AsyncSession,
    instance_ids: Sequence[int],
    *,
    cursor: str | None = None,
    limit: int | None = None,
) -> RawPage[InstanceEvent]:
    """多台实例的事件并集:降序游标分页的 ORM 行(服务级时间线用)。"""
    from app.core.pagination import paginate_by_id

    if not instance_ids:
        return RawPage(items=[], next_cursor=None)
    stmt = (
        select(InstanceEvent)
        .where(InstanceEvent.instance_id.in_(list(instance_ids)))
        .order_by(InstanceEvent.id.desc())
    )
    page_items, next_cursor = await paginate_by_id(
        session, stmt, id_col=InstanceEvent.id, cursor=cursor, limit=limit
    )
    return RawPage(items=page_items, next_cursor=next_cursor)


async def list_events(
    session: AsyncSession, instance_id: int, *, cursor: str | None = None, limit: int | None = None
):
    """实例事件时间线:降序游标分页。"""
    from app.core.pagination import Page
    from app.modules.orchestrator.schemas import InstanceEventOut

    raw = await list_events_raw(session, [instance_id], cursor=cursor, limit=limit)
    return Page[InstanceEventOut](
        items=[InstanceEventOut.model_validate(e) for e in raw.items], next_cursor=raw.next_cursor
    )


async def rename_instance(
    session: AsyncSession, user_id: int, uuid: str, new_name: str
) -> Instance:
    instance = await get_instance(session, user_id, uuid)
    instance.name = new_name
    await session.commit()
    return instance


# ---------- 用户操作 ----------


def _reject_service_instance(instance: Instance) -> None:
    """服务的版本实例拒绝实例级生命周期操作(stop / start / restart / release / 重置 token),
    统一由 /services 驱动;续费 / 转换与只读端点照常。"""
    if instance.service_id is not None:
        raise conflict(key="orchestrator.serviceInstanceLifecycle")


async def stop_instance_row(
    session: AsyncSession, instance: Instance, *, reason: str = "user_stop", actor: str = "user"
) -> Instance:
    """关机的 row 级核心:running 守卫 → stopping + outbox,**不 commit**。"""
    if instance.status != sm_def.RUNNING:
        raise AppError(ErrorCode.INSTANCE_INVALID_TRANSITION, key="orchestrator.stopNeedsRunning")
    await transition(session, instance, sm_def.STOPPING, reason=reason, actor=actor)
    enqueue(session, "instance.stop", {"instance_id": instance.id})
    return instance


async def stop_instance(session: AsyncSession, user_id: int, uuid: str) -> Instance:
    instance = await get_instance(session, user_id, uuid)
    _reject_service_instance(instance)
    await stop_instance_row(session, instance)
    await session.commit()
    return instance


async def _rebind_data_disk(session: AsyncSession, instance: Instance) -> None:
    """(重新)占用数据盘:盘在则锁盘并经 attach_for_instance 校验挂载,已删则放弃挂载点。"""
    if instance.data_disk_id is None:
        return
    disk = await session.get(DataDisk, instance.data_disk_id, with_for_update=True)
    if disk is None or disk.status == "deleted":
        instance.data_disk_id = None
        await session.flush()
        return
    from app.modules.orchestrator import disks as disks_service

    await disks_service.attach_for_instance(session, instance.user_id, disk.id, instance.id)


async def start_instance_row(session: AsyncSession, user_id: int, instance: Instance) -> Instance:
    """开机的 row 级核心:锁实例 → 冻结 / 状态 / 节点 / 集群 / 订阅 / 数据盘 / 余额逐道闸
    → starting + outbox,**不 commit**。"""
    # 锁序 instance → disk → wallet
    locked = await session.get(Instance, instance.id, with_for_update=True, populate_existing=True)
    assert locked is not None  # get_instance 刚取到,同事务内不可能消失
    instance = locked
    if instance.status == sm_def.FROZEN:
        raise AppError(ErrorCode.INSTANCE_FROZEN, key="orchestrator.frozenNeedsRecharge")
    recovered = instance.status == sm_def.FAILED
    if instance.status != sm_def.STOPPED and not recovered:
        raise AppError(ErrorCode.INSTANCE_INVALID_TRANSITION, key="orchestrator.startNeedsStopped")
    # 实例盘钉在原节点:节点 Missing 前置拦掉;台账无该行放行,NotReady 不拦
    if instance.node_name:
        node = await nodes_service.get_node_spec(session, instance.node_name)
        if node is not None and node.status == "Missing":
            raise AppError(
                ErrorCode.INSTANCE_INVALID_TRANSITION,
                key="orchestrator.nodeUnreachable",
                http_status=409,
            )
    await _require_cluster_for_pool(
        session,
        instance.spec.get("pool_label"),
        instance.gpu_count,
        with_data_disk=instance.data_disk_id is not None,
    )
    if instance.market == MARKET_SUBSCRIPTION:
        # 包周期不看余额,只看周期未过
        await billing_service.assert_subscription_active(session, instance.id)
    if recovered:
        # 故障恢复:failed → stopped → 正常开机链路
        await transition(session, instance, sm_def.STOPPED, reason="failed_recover", actor="user")
    # 数据盘挂载校验(盘锁)在钱包锁之前
    await _rebind_data_disk(session, instance)
    if instance.market != MARKET_SUBSCRIPTION:
        estimate = hourly_cost(instance.price_hourly, instance.gpu_count)
        await billing_service.assert_can_afford(session, user_id, additional_hourly=estimate)
    await transition(session, instance, sm_def.STARTING, reason="user_start", actor="user")
    # 开机必须清 unready_since(计费截断与 reconciler 宽限判定据它)
    instance.unready_since = None
    enqueue(session, "instance.start", {"instance_id": instance.id})
    return instance


async def start_instance(session: AsyncSession, user_id: int, uuid: str) -> Instance:
    instance = await get_instance(session, user_id, uuid)
    _reject_service_instance(instance)
    instance = await start_instance_row(session, user_id, instance)
    await session.commit()
    return instance


async def restart_instance(session: AsyncSession, user_id: int, uuid: str) -> Instance:
    instance = await get_instance(session, user_id, uuid)
    _reject_service_instance(instance)
    if instance.status != sm_def.RUNNING:
        raise AppError(
            ErrorCode.INSTANCE_INVALID_TRANSITION, key="orchestrator.restartNeedsRunning"
        )
    await _require_cluster_for_pool(
        session,
        instance.spec.get("pool_label"),
        instance.gpu_count,
        with_data_disk=instance.data_disk_id is not None,
    )
    await transition(session, instance, sm_def.STOPPING, reason="restart", actor="user")
    enqueue(session, "instance.restart", {"instance_id": instance.id})
    await session.commit()
    return instance


async def renew_instance(
    session: AsyncSession,
    user_id: int,
    uuid: str,
    *,
    period: str,
    period_count: int,
    idempotency_key: str | None,
) -> tuple[Instance, Any, bool]:
    """续费包周期实例。返回 (实例, 报价, created);created=False = 幂等重放。
    换周期续同时刷新 `instances.price_hourly`;冻结中续费即解冻回 stopped。
    """
    instance = await get_instance(session, user_id, uuid)
    # 锁序 instance → wallet → subscription
    locked = await session.get(Instance, instance.id, with_for_update=True, populate_existing=True)
    assert locked is not None  # get_instance 刚取到,同事务内不可能消失
    instance = locked
    if instance.market != MARKET_SUBSCRIPTION:
        raise AppError(
            ErrorCode.SUBSCRIPTION_NOT_RENEWABLE, key="orchestrator.renewNotSubscription"
        )
    if instance.status in (sm_def.RELEASING, sm_def.RELEASED):
        raise AppError(ErrorCode.SUBSCRIPTION_NOT_RENEWABLE, key="orchestrator.renewReleased")
    await billing_service.lock_wallet(session, user_id)
    row, quoted, created = await billing_service.renew_subscription(
        session,
        instance=instance,
        period=period,
        period_count=period_count,
        idempotency_key=idempotency_key,
    )
    if not created:
        # 幂等重放:手上的 instance 已在 billing 侧 rollback,重新取
        return await get_instance(session, user_id, uuid), quoted, False
    policies = await get_effective_policies(session)
    instance.price_hourly = price_for(
        row.unit_price, market=MARKET_SUBSCRIPTION, policies=policies, period=period
    )
    if instance.status == sm_def.FROZEN:
        await transition(
            session, instance, sm_def.STOPPED, reason="subscription_renew", actor="user"
        )
        instance.frozen_deadline = None
    await session.commit()
    return instance, quoted, True


async def subscribe_instance(
    session: AsyncSession,
    user_id: int,
    uuid: str,
    *,
    period: str,
    period_count: int,
    idempotency_key: str | None,
) -> tuple[Instance, Any, bool]:
    """按量实例转包周期。返回 (实例, 报价, created);created=False = 幂等重放。
    先按转换前时价结清按量账,再翻 `market`;只收 running / stopped。
    """
    instance = await get_instance(session, user_id, uuid)
    if idempotency_key:
        # 重放判定必须在状态守卫之前;三个定位参数给全才做异参检测(转换与续费共用幂等键命名空间)
        replayed = await billing_service.find_subscription_replay(
            session,
            user_id=user_id,
            key=idempotency_key,
            instance_id=instance.id,
            period=period,
            period_count=period_count,
        )
        if replayed is not None:
            return (
                instance,
                await billing_service.quote_of_subscription_row(
                    session, replayed, instance.gpu_count
                ),
                False,
            )
    # 锁序 instance → bill → wallet
    locked = await session.get(Instance, instance.id, with_for_update=True, populate_existing=True)
    assert locked is not None  # get_instance 刚取到,同事务内不可能消失
    instance = locked
    if instance.market != MARKET_ON_DEMAND:
        raise AppError(ErrorCode.SUBSCRIPTION_NOT_RENEWABLE, key="orchestrator.convertNotOnDemand")
    if instance.status not in (sm_def.RUNNING, sm_def.STOPPED):
        raise AppError(
            ErrorCode.INSTANCE_INVALID_TRANSITION,
            key="orchestrator.convertNeedsRunningOrStopped",
            http_status=http_status.HTTP_409_CONFLICT,
        )
    sku = await catalog_service.get_sku(session, instance.sku_id)
    if not sku.period_enabled:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="orchestrator.periodNotEnabled")

    # 翻 market 前逐小时结清(含 48h 滞后熔断);stopped 同样要结
    await billing_service.settle_on_demand_up_to(
        session,
        instance_id=instance.id,
        user_id=user_id,
        unit_price=instance.price_hourly,
        gpu_count=instance.gpu_count,
        at=now_utc(),
    )
    row, quoted, created = await billing_service.convert_to_subscription(
        session,
        instance=instance,
        period=period,
        period_count=period_count,
        idempotency_key=idempotency_key,
    )
    if not created:
        return await get_instance(session, user_id, uuid), quoted, False
    policies = await get_effective_policies(session)
    instance.market = MARKET_SUBSCRIPTION
    instance.price_hourly = price_for(
        row.unit_price, market=MARKET_SUBSCRIPTION, policies=policies, period=period
    )
    # 转换后余额仍须撑住其它在途按量资源
    await billing_service.assert_can_afford(session, user_id)
    await session.commit()
    logger.info("instance_converted_to_subscription", instance_id=instance.id, period=period)
    return instance, quoted, True


async def convert_to_on_demand(session: AsyncSession, user_id: int, uuid: str) -> Instance:
    """竞价实例转按量;已是按量则原样返回。只翻 `market` 不动 Pod;
    当前整点小时整体改按按量价(billing.reprice_current_hour),原价取 `spec.base_price_hourly`。
    """
    instance = await get_instance(session, user_id, uuid)
    if instance.market == MARKET_ON_DEMAND:
        return instance  # 幂等:目标状态已达成
    # 锁序 instance → bill → wallet
    locked = await session.get(Instance, instance.id, with_for_update=True, populate_existing=True)
    assert locked is not None  # get_instance 刚取到,同事务内不可能消失
    instance = locked
    if instance.market != MARKET_SPOT:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="orchestrator.toOnDemandNotSpot")
    if instance.status not in (sm_def.RUNNING, sm_def.STOPPED):
        raise AppError(
            ErrorCode.INSTANCE_INVALID_TRANSITION,
            key="orchestrator.convertNeedsRunningOrStopped",
            http_status=http_status.HTTP_409_CONFLICT,
        )
    base = Decimal(str(instance.spec.get("base_price_hourly") or instance.price_hourly))
    # 翻价前把滞后的整点小时先按竞价价结清(与转包周期同一口径),只有当前小时整体改按量价
    await billing_service.settle_on_demand_up_to(
        session,
        instance_id=instance.id,
        user_id=user_id,
        unit_price=instance.price_hourly,
        gpu_count=instance.gpu_count,
        at=now_utc(),
    )
    if instance.status == sm_def.RUNNING:
        await billing_service.reprice_current_hour(
            session,
            instance_id=instance.id,
            user_id=user_id,
            new_price=base,
            gpu_count=instance.gpu_count,
            at=now_utc(),
        )
    instance.market = MARKET_ON_DEMAND
    instance.price_hourly = base
    # 余额须撑住转换后的燃烧率
    await billing_service.assert_can_afford(session, user_id)
    await session.commit()
    logger.info("spot_converted_to_on_demand", instance_id=instance.id, user_id=user_id)
    return instance


async def set_instance_auto_renew(
    session: AsyncSession, user_id: int, uuid: str, *, enabled: bool
) -> Instance:
    """开关自动续费。"""
    instance = await get_instance(session, user_id, uuid)
    if instance.market != MARKET_SUBSCRIPTION:
        raise AppError(
            ErrorCode.SUBSCRIPTION_NOT_RENEWABLE, key="orchestrator.renewNotSubscription"
        )
    await billing_service.set_subscription_auto_renew(
        session, user_id=user_id, instance_id=instance.id, enabled=enabled
    )
    await session.commit()
    return instance


async def release_instance_row(
    session: AsyncSession, instance: Instance, *, actor: str = "user", reason: str | None = None
) -> Instance:
    """释放的 row 级核心:状态守卫 → releasing + outbox,**不 commit**;释放中 / 已释放幂等直回。"""
    if instance.status in (sm_def.RELEASING, sm_def.RELEASED):
        # 幂等释放
        return instance
    if instance.status not in (
        sm_def.STOPPED,
        sm_def.FROZEN,
        sm_def.FAILED,  # 清理失败实例,同走 releasing→released
        sm_def.CREATING,  # 用户主动取消,不等 creating 超时
        sm_def.STOPPING,  # 关机悬挂:允许用户直接放弃(reconciler 超时强删兜底)
    ):
        raise AppError(ErrorCode.INSTANCE_NOT_STOPPED, key="orchestrator.releaseNeedsStopped")
    await transition(
        session, instance, sm_def.RELEASING, reason=reason or f"{actor}_release", actor=actor
    )
    enqueue(session, "instance.release", {"instance_id": instance.id})
    return instance


async def release_instance(
    session: AsyncSession, user_id: int, uuid: str, *, actor: str = "user"
) -> Instance:
    instance = await get_instance(session, user_id, uuid)
    _reject_service_instance(instance)
    if instance.status in (sm_def.RELEASING, sm_def.RELEASED):
        return instance
    await release_instance_row(session, instance, actor=actor)
    await session.commit()
    return instance


# ---------- K8s spec 构造 ----------


def bandwidth_annotations(settings: Settings) -> dict[str, str]:
    """CNI bandwidth 插件识别的限速注解(k3s flannel 与 Cilium bandwidthManager 同口径);0 = 不加。"""
    out: dict[str, str] = {}
    if settings.tenant_egress_bandwidth_mbps > 0:
        out["kubernetes.io/egress-bandwidth"] = f"{settings.tenant_egress_bandwidth_mbps}M"
    if settings.tenant_ingress_bandwidth_mbps > 0:
        out["kubernetes.io/ingress-bandwidth"] = f"{settings.tenant_ingress_bandwidth_mbps}M"
    return out


def build_pod_spec(
    instance: Instance,
    *,
    distro: str | None = None,
    data_disk_subpath: str | None = None,
    image_pull_secret: str | None = None,
) -> InstancePodSpec:
    """构造 Pod spec。data_disk_subpath 由调用方从 `data_disks.juicefs_subpath` 读出传入;
    image_pull_secret 是该 ns 的拉取凭据 Secret 名;服务形态只读实例行的快照列。
    dev/service 两形态差别集中在此,k8s 层只按 spec 字段建对象。
    """
    settings = get_settings()
    gpu_req = spec_to_gpu_request(
        instance.spec,
        instance.gpu_count,
        hami_use_gputype=settings.hami_use_gputype,
        distro=distro,
    )
    is_service = instance.workload_type == WORKLOAD_SERVICE
    if is_service and (instance.service_slug is None or instance.service_port is None):
        raise RuntimeError(f"service instance {instance.uuid} lacks service snapshot columns")
    # SSH 只认 instances.with_ssh;不要 SSH 的实例 ssh_port 恒 None
    if instance.with_ssh and instance.ssh_port is None:
        raise RuntimeError("build_pod_spec requires allocated ssh_port")
    plain_env, secret_env = instance_env(instance)
    if is_service:
        # 服务容器不注入 JUPYTER_*
        env = plain_env
        secrets_ = secret_env
    else:
        env = {
            "JUPYTER_ALLOW_ORIGIN": jupyter_origin(instance.uuid, settings),
        }
        # token 走 per-instance Secret,不以明文 env 落 Pod spec
        secrets_ = {"JUPYTER_TOKEN": _token_plain(instance)}
    # GPU 实例按卡数放大 CPU/内存,CPU 实例倍率 1;系统盘不随卡数放大
    spec_n = instance.gpu_count if instance.gpu_count > 0 else 1
    return InstancePodSpec(
        namespace=instance.k8s_namespace,
        name=instance.uuid,
        image=instance.image_ref,
        gpu_resources=gpu_req.resources,
        runtime_class=gpu_req.runtime_class,
        host_users=gpu_req.host_users,
        vcpu=instance.spec["vcpu"] * spec_n,
        mem_gb=instance.spec["mem_gb"] * spec_n,
        disk_gb=instance.spec["disk_gb"],
        ssh_node_port=instance.ssh_port,
        # 同时是 SSH 的展示主机名,service 形态也填
        jupyter_host=jupyter_host(instance.uuid, settings),
        env=env,
        secret_env=secrets_,
        authorized_keys=tuple(instance.authorized_keys),
        node_selector=gpu_req.node_selector,
        data_disk_subpath=data_disk_subpath,
        scheduler_name=gpu_req.scheduler_name,
        annotations={**gpu_req.annotations, **bandwidth_annotations(settings)},
        image_pull_secret=image_pull_secret,
        # 服务容器 Always 原地重启(Pod 名 = 实例 uuid 不可变)
        restart_policy="Always" if is_service else "Never",
        command=tuple(instance.container_command) if instance.container_command else None,
        args=tuple(instance.container_args) if instance.container_args else None,
        service_port=instance.service_port if is_service else None,
        service_host=(
            service_endpoint_host(instance.service_slug, settings)
            if is_service and instance.service_slug
            else None
        ),
        health_path=instance.health_path if is_service else None,
        with_ssh=instance.with_ssh,
    )


async def build_pod_spec_with_cluster(
    session: AsyncSession, instance: Instance, *, image_pull_secret: str | None = None
) -> InstancePodSpec:
    """outbox handler 用:带集群发行版上下文与数据盘 subPath(从盘记录读)。"""
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
    """接入信息:按形态给字段,没有的入口直接缺席。"""
    settings = get_settings()
    if instance.status != sm_def.RUNNING:
        raise AppError(ErrorCode.INSTANCE_INVALID_TRANSITION, key="orchestrator.accessNeedsRunning")
    out: dict[str, Any] = {}
    if instance.with_ssh:
        # SSH 主机名 = 实例域名(靠 NodePort 区分实例)
        ssh_host = jupyter_host(instance.uuid, settings)
        out["ssh_host"] = ssh_host
        out["ssh_port"] = instance.ssh_port
        out["ssh_command"] = f"ssh root@{ssh_host} -p {instance.ssh_port}"
    if instance.workload_type == WORKLOAD_DEV:
        # 一次性入场票据,token 不进 URL
        out["jupyter_url"] = _new_jupyter_ticket(instance, _token_plain(instance))
    if instance.service_slug:
        out["endpoint_url"] = f"https://{service_endpoint_host(instance.service_slug, settings)}"
    return out


async def get_access(session: AsyncSession, user_id: int, uuid: str) -> dict[str, Any]:
    """取实例(owner 校验)再拼接入信息。"""
    return build_access(await get_instance(session, user_id, uuid))


# ---------- SSH 公钥 ----------


async def strip_ssh_key_from_instances(session: AsyncSession, user_id: int, public_key: str) -> int:
    """把公钥从该用户全部未释放实例的 authorized_keys 快照摘掉,返回实例数;
    运行中 Pod 下次重建才生效。"""
    rows = list(
        (
            await session.execute(
                select(Instance).where(
                    Instance.user_id == user_id,
                    Instance.status != sm_def.RELEASED,
                )
            )
        )
        .scalars()
        .all()
    )
    stripped = 0
    for inst in rows:
        if public_key in inst.authorized_keys:
            inst.authorized_keys = [k for k in inst.authorized_keys if k != public_key]
            stripped += 1
    return stripped


async def reset_jupyter_token(session: AsyncSession, user_id: int, uuid: str) -> Instance:
    instance = await get_instance(session, user_id, uuid)
    _reject_service_instance(instance)
    instance.jupyter_token = _encode_token(secrets.token_urlsafe(24), instance_uuid=instance.uuid)
    # 重建 Pod 才生效;running 时走 restart
    if instance.status == sm_def.RUNNING:
        await transition(session, instance, sm_def.STOPPING, reason="restart", actor="user")
        enqueue(session, "instance.restart", {"instance_id": instance.id})
    await session.commit()
    return instance


# ---------- 容器日志(请求路径直读 K8s 的唯一例外:owner 校验 + 限流 + 5s 超时) ----------

LOGS_MAX_TAIL_LINES = 2000


async def read_instance_logs(
    session: AsyncSession, user_id: int, uuid: str, *, tail_lines: int
) -> "InstanceLogsOut":
    """读取实例容器日志(只读,不记审计):非属主 404;仅 running/stopping,否则 409;
    tail_lines 超上限截断。"""
    from app.modules.orchestrator.schemas import InstanceLogsOut

    instance = await get_instance(session, user_id, uuid)
    if instance.status not in (sm_def.RUNNING, sm_def.STOPPING):
        raise conflict(key="orchestrator.logsNeedsRunning")
    await check_rate_limit(f"instance-logs:{user_id}", max_attempts=20, window_seconds=3600.0)
    tail = min(tail_lines, LOGS_MAX_TAIL_LINES)
    try:
        # +1 行探路,超出即 truncated
        raw = await get_orchestrator().read_instance_logs(
            instance.k8s_namespace, instance.uuid, tail_lines=tail + 1
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
    """市场近似库存(批量):sku_id → 可售实例数。数据源节点台账,无数据 → 0;
    减掉包周期预留(与软准入同源)。"""
    specs = await nodes_service.list_node_specs(session)
    cap = (await get_effective_policies(session)).gpu_node_cpu_instance_vcpu_cap
    reserved = await _reserved_slots_by_sku(session, [s.id for s in skus])
    return {
        sku.id: max(
            0, _sku_free_capacity(sku, specs, gpu_node_vcpu_cap=cap)[1] - reserved.get(sku.id, 0)
        )
        for sku in skus
    }


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
    """管理端实例列表(游标分页,降序):q 按实例名或 uuid 前缀,node_name 精确。"""
    from app.core.pagination import paginate_by_id

    stmt = select(Instance).order_by(Instance.id.desc())
    if status_filter:
        stmt = stmt.where(Instance.status == status_filter)
    if user_id:
        stmt = stmt.where(Instance.user_id == user_id)
    if node_name:
        stmt = stmt.where(Instance.node_name == node_name)
    q = (q or "").strip()
    if q:
        # LIKE 元字符转义
        stmt = stmt.where(
            Instance.uuid.like(f"{like_escape(q)}%", escape="\\")
            | Instance.name.ilike(f"%{like_escape(q)}%", escape="\\")
        )
    page_items, next_cursor = await paginate_by_id(
        session, stmt, id_col=Instance.id, cursor=cursor, limit=limit
    )
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
        target_id=instance.uuid,
    )
    await session.commit()
    return instance


async def admin_preempt(session: AsyncSession, instance_uuid: str, *, reason: str) -> Instance:
    """管理端强制回收一台竞价实例,走与自动抢占同一条回收路径(reason 与 admin_force_stop 不同)。"""
    from app.modules.orchestrator import preempt as preempt_mod

    instance = await admin_get_instance(session, instance_uuid)
    if instance.market != MARKET_SPOT:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="orchestrator.preemptNotSpot")
    if instance.status != sm_def.RUNNING:
        raise AppError(
            ErrorCode.INSTANCE_INVALID_TRANSITION, key="orchestrator.forceStopNeedsRunning"
        )
    policies = await get_effective_policies(session)
    await preempt_mod.preempt(
        session,
        [instance],
        grace_seconds=policies.spot_grace_seconds,
        requested_by=0,
        admin_reason=reason,
    )
    await session.commit()
    return instance


async def system_stop(session: AsyncSession, instance: Instance, *, reason: str) -> None:
    """平台侧停机(actor=system):同事务落事件 + outbox,不 commit;reason 由调用方给。"""
    await transition(session, instance, sm_def.STOPPING, reason=reason, actor="system")
    enqueue(session, "instance.stop", {"instance_id": instance.id})


async def freeze_instance(
    session: AsyncSession, instance: Instance, deadline: Any, *, reason: str = "arrears_freeze"
) -> None:
    await transition(
        session,
        instance,
        sm_def.FROZEN,
        reason=reason,
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
    """停掉该用户全部 running 实例(封禁用):同事务落事件 + outbox,不 commit,返回台数;
    creating/starting 由 billing.patrol 后续兜住。"""
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
