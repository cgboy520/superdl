"""编排服务:实例生命周期的唯一入口(门面)。

事务纪律:状态变更只走 transition()(乐观锁 + 同事务 instance_events + 迁移监听器);
「改 DB + 动 K8s」一律 outbox,请求路径绝不直接调 K8s。

状态迁移原语在 transitions.py、SSH 端口池在 ports.py、查询聚合在 queries.py,本文件
再导出它们:跨模块只经 app.modules.orchestrator.service 访问(lint-imports 强制)。
"""

import base64
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
from sqlalchemy import Integer, cast, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.crypto import decrypt_str, encrypt_str, hash_api_key, hash_api_key_candidates
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
from app.modules.orchestrator.models import (
    DataDisk,
    Instance,
    InstanceEvent,
    ServiceApiKey,
    ServiceEndpoint,
)
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
    FROZEN as FROZEN,
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
from app.modules.orchestrator.transitions import (
    register_transition_listener as register_transition_listener,
)
from app.modules.orchestrator.transitions import (
    transition as transition,
)

if TYPE_CHECKING:
    from app.modules.catalog.models import Sku
    from app.modules.nodes.models import NodeSpec
    from app.modules.orchestrator.schemas import (
        InstanceLogsOut,
        InstanceOut,
        ServiceEndpointOut,
    )

logger = get_logger(__name__)

# 单实例活跃密钥上限;密钥行永不删(吊销只写 revoked_at),没有上限即一条无限追加写的口子
MAX_API_KEYS_PER_INSTANCE = 20
# 端点公网域名左标签前缀,与 deploy 侧 Gateway listener 的 hostname 通配同一形态
ENDPOINT_SLUG_PREFIX = "svc-"
_SLUG_ATTEMPTS = 3


def jupyter_host(instance_uuid: str, settings: Settings | None = None) -> str:
    """实例 Jupyter 主机名:<jupyter_host_prefix><uuid>.<jupyter_domain_suffix>。
    Ingress host / 入场 URL / JUPYTER_ALLOW_ORIGIN 三处必须同一口径,只从这里拼。"""
    s = settings or get_settings()
    return f"{s.jupyter_host_prefix}{instance_uuid}.{s.jupyter_domain_suffix}"


def jupyter_origin(instance_uuid: str, settings: Settings | None = None) -> str:
    """实例 Jupyter 的浏览器 origin:`https://<主机名>` 或 `https://<主机名>:<端口>`。

    入场票据 URL 与 JUPYTER_ALLOW_ORIGIN 必须带端口(浏览器同源判定把端口算进 origin),
    HTTPRoute hostname 与 SSH 连接串只认不带端口的 `jupyter_host`,两者不可互换。
    """
    s = settings or get_settings()
    host = jupyter_host(instance_uuid, s)
    return (
        f"https://{host}" if s.jupyter_url_port == 443 else f"https://{host}:{s.jupyter_url_port}"
    )


def service_endpoint_host(slug: str, settings: Settings | None = None) -> str:
    """服务端点主机名:<slug>.<service_domain_suffix>。
    HTTPRoute hostname / 用户看到的 URL / 鉴权回调解析 slug 三处同一口径,只从这里拼。"""
    s = settings or get_settings()
    return f"{slug}.{s.service_domain_suffix}"


def endpoint_slug_from_host(host: str | None) -> str | None:
    """从 Host 头反解端点 slug;不匹配本环境的服务域名后缀一律 None(交调用方拒绝)。"""
    if not host:
        return None
    name = host.split(":")[0].strip().rstrip(".").lower()
    suffix = f".{get_settings().service_domain_suffix.lower()}"
    if not name.endswith(suffix):
        return None
    slug = name[: -len(suffix)]
    # 只收单段左标签 + svc- 前缀:部署可把 Jupyter 与端点放在同一后缀下(靠端口分开),
    # 少了这两条 Jupyter 域名就成了鉴权端点的别名
    if "." in slug or not slug.startswith(ENDPOINT_SLUG_PREFIX):
        return None
    return slug


def _snapshot_spec(sku: "Sku") -> dict[str, Any]:
    return {
        "sku_name": sku.name,
        # SKU 原价时价快照(字符串,JSONB 不存 Decimal);折扣策略在线可调,竞价转按量
        # 只能读它还原原价,不能拿折后价反推
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
        # canonical 型号 → Pod nodeSelector(未识别型号存 None = 不钉)
        "gpu_model_selector": canonical_gpu_model(sku.gpu_model),
    }


async def _require_cluster_for_pool(
    session: AsyncSession,
    pool_label: str | None,
    gpu_count: int,
    *,
    with_data_disk: bool = False,
) -> None:
    """下发门禁:能力缺位即时 409,而非等 Pod Pending 到超时。判据与 `build_gpu_request` 同源。

    先看要不要卡再看落哪个池:gpu_count == 0(CPU 实例)走默认调度器,即使挂在 hami 池上
    也不查 HAMi 就绪;其余按池判,hami 池查 HAMi、kata 池查 RuntimeClass(mig 池由
    gpu-operator 的 MIG manager 管,无独立门禁项)。StorageClass 实例盘人人要挂,数据盘按需。
    """
    if gpu_count > 0:
        if pool_label == POOL_HAMI:
            await nodes_service.require_hami_ready(session)
        elif pool_label == POOL_KATA:
            await nodes_service.require_kata_runtimeclass(session)
    await nodes_service.require_storage_classes(session, with_data_disk=with_data_disk)


# ---------- Jupyter token(密文落库;bootstrap 票据入场) ----------


def _encode_token(plaintext: str, *, instance_uuid: str) -> str:
    """落库形态:AES-GCM 密文,AAD 绑定实例 uuid(防密文跨实例搬运)。"""
    return encrypt_str(plaintext, aad=f"jupyter-token:{instance_uuid}")


def _token_plain(instance: Instance) -> str:
    """读出明文(落库形态只有密文,见 _encode_token)。"""
    return decrypt_str(instance.jupyter_token, aad=f"jupyter-token:{instance.uuid}")


def _env_aad(instance_uuid: str) -> str:
    return f"instance-env:{instance_uuid}"


def _encode_env(env: dict[str, str], secret_keys: set[str], *, instance_uuid: str) -> str:
    """用户环境变量的落库形态:整包 JSON 的 AES-GCM 密文,AAD 绑实例 uuid。
    明文项一起加密不分列存:分列会把「哪些键是密文」本身泄漏给能读这张表的身份。"""
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
    """一次性入场票据:code(单次)+ TTL + HMAC 签名(密钥=Jupyter token 本体)。

    镜像内 bootstrap handler 验签核销后 Set-Cookie 第一方会话 cookie,token 不进 URL
    (访问日志/浏览器历史/Referer);验签密钥随 token 轮换,旧票据即全部作废。
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
    """镜像引用校验:先形态,再来源。

    形态判定与管理端目录 CRUD 共用 core.registry.is_valid_image_ref(单一事实源)。
    来源白名单默认关(平台配置·镜像仓库 image_allowed_registries,Harbor 地址自动放行);
    配置后只放行平台镜像目录内的引用与白名单前缀;这是唯一的镜像来源闸门。
    """
    if not is_valid_image_ref(image_ref):
        raise AppError(ErrorCode.VALIDATION_ERROR, key="orchestrator.imageRefInvalid")
    # 服务型实例要求版本钉死:它 restartPolicy=Always,可变 tag 会让一次无人值守的容器重启
    # 换掉线上版本
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
    session: AsyncSession, user_id: int, new_gpus: int, new_vcpus: int
) -> None:
    """每用户配额(实例数 / GPU 总数 / CPU 实例 vCPU 总数);K8s 侧 ResourceQuota 为兜底。

    生效值走 account.get_user_limits(用户覆盖 → 平台策略 → env 默认);vCPU 维只有平台
    策略层 `max_vcpus_per_user`,无用户级覆盖列。GPU 实例只计 GPU 维、CPU 实例只计 vCPU 维,
    两维互不相交。
    """

    limits = await account_service.get_user_limits(session, user_id)
    policies = await get_effective_policies(session)
    live = (
        (
            await session.execute(
                select(
                    func.count(),
                    func.coalesce(func.sum(Instance.gpu_count), 0),
                    # CPU 实例(gpu_count=0)的 vCPU 合计;spec 是落库时的 SKU 快照
                    func.coalesce(
                        func.sum(cast(Instance.spec["vcpu"].astext, Integer)).filter(
                            Instance.gpu_count == 0
                        ),
                        0,
                    ),
                ).where(
                    Instance.user_id == user_id,
                    Instance.status.notin_(("released", "failed")),
                )
            )
        )
        .tuples()
        .one()
    )
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


# ---------- 容量估算((池, 型号) 双维度,数据源是节点台账而非请求路径直连 K8s) ----------


def _sku_free_capacity(
    sku: "Sku", specs: list["NodeSpec"], *, gpu_node_vcpu_cap: int
) -> tuple[int | None, int]:
    """该 SKU 的近似可分配量:返回 (台账哨兵, 可售实例数)。

    哨兵为 None 表示台账无此池(×型号)数据,调用方据此放行交调度器裁决。
    GPU 档按 (池, canonical 型号) 匹配,哨兵是 Ready 空闲卡合计(NotReady/Cordoned/Missing
    不卖),可售数按算力份额折算;CPU 档只按池匹配(gpu_model 是空串),哨兵是匹配到的节点
    行数,可售数走 catalog.sellable_cpu_slots。与管理端容量预览同一份算法。
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
) -> None:
    """创建软准入:台账显示该 (池, 型号) 可分配量不足 → 先尝试抢占竞价实例,仍不足则 409。

    台账 60s 粒度只是近似:无数据一律放行交调度器裁决,本判断只挡「确定卖不出去」的单。
    抢占只对 GPU 档的非竞价请求生效:竞价请求不许抢别人,CPU 档的容量口径是 vCPU/内存
    而非卡数,套不上「腾几张卡」的换算。
    """
    policies = await get_effective_policies(session)
    specs = await nodes_service.list_node_specs(session)
    matching_free, sellable = _sku_free_capacity(
        sku, specs, gpu_node_vcpu_cap=policies.gpu_node_cpu_instance_vcpu_cap
    )
    if matching_free is None:
        return
    sellable -= await _reserved_slots(session, sku)
    # 要占几份容量:GPU 实例按卡数,CPU 实例(gpu_count=0)占 1 台的位置
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
    """sku_id → 被未到期包周期实例占住的槽位数(一次查询,市场页按 SKU 批量取)。

    台账 `gpu_used` 只数真在跑的 Pod,包月用户关机时那张卡在台账上是空闲的;这里在控制面
    层面把周期内的停机/冻结实例继续算作占用(物理层不预留,见 docs/reference/billing.md),
    只算同一条 SKU 的实例。
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
    """该用户 creating/starting 实例的时费合计:它们还没跑起来,RUNNING 口径的在途统计
    看不到。由 wallet.assert_can_afford 内部统一并入(调用方无从遗漏:漏并 = 连续开户/
    循环开机可绕过余额护栏)。"""
    rows = (
        (
            await session.execute(
                select(Instance.price_hourly, Instance.gpu_count).where(
                    Instance.user_id == user_id,
                    Instance.status.in_((sm_def.CREATING, sm_def.STARTING)),
                    # 包周期实例已经付过整段周期的钱,它跑起来不会再动余额
                    Instance.market != MARKET_SUBSCRIPTION,
                )
            )
        )
        .tuples()
        .all()
    )
    return sum((hourly_cost(price, count) for price, count in rows), Decimal("0.00"))


def _new_endpoint_slug() -> str:
    """公网端点左标签:svc- + 10 位 base32(约 50 bit 熵)。不用 instance.uuid:内部主键
    不该出现在公网域名、TLS SNI、访问日志与第三方 Referer 里。"""
    raw = base64.b32encode(secrets.token_bytes(7)).decode().lower().rstrip("=")
    return f"{ENDPOINT_SLUG_PREFIX}{raw[:10]}"


async def _create_service_endpoint(
    session: AsyncSession,
    instance: Instance,
    *,
    container_port: int,
    health_path: str | None,
    require_api_key: bool,
) -> ServiceEndpoint:
    """建服务端点行,slug 撞 UNIQUE 就换一个重试(最多 3 次)。
    每次插入必须包在 SAVEPOINT 里,否则一次碰撞会把整笔建实例事务打成 rollback-only。"""
    for attempt in range(_SLUG_ATTEMPTS):
        endpoint = ServiceEndpoint(
            instance_id=instance.id,
            public_slug=_new_endpoint_slug(),
            container_port=container_port,
            health_path=health_path,
            require_api_key=require_api_key,
        )
        try:
            async with session.begin_nested():
                session.add(endpoint)
                await session.flush()
        except IntegrityError:
            if attempt == _SLUG_ATTEMPTS - 1:
                raise
            logger.warning("service_endpoint_slug_collision", instance_id=instance.id)
            continue
        return endpoint
    raise AssertionError("unreachable")  # pragma: no cover


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
    workload_type: str = WORKLOAD_DEV,
    container_command: list[str] | None = None,
    container_args: list[str] | None = None,
    env: dict[str, str] | None = None,
    env_secret_keys: list[str] | None = None,
    service_port: int | None = None,
    health_path: str | None = None,
    require_api_key: bool = True,
    with_ssh: bool = False,
    market: str = MARKET_ON_DEMAND,
    period: str | None = None,
    period_count: int = 1,
) -> tuple[Instance, bool]:
    """创建实例(202 异步)。返回 (实例, created):created=False = 幂等重放,
    路由据此回 200 + X-Idempotent-Replay 而非 202。

    service 形态额外落一行 service_endpoints,按 with_ssh 决定要不要 SSH 入口。
    market='subscription' 时同事务再落一行 subscriptions、按周期总价一次性扣款(不许透支),
    扣完还要过一遍在途燃烧率校验。
    """
    # 异参检测指纹:下单参数全集(改任何一个都视为新请求)。dict 先排序保证确定性;
    # env 含密文键值也只进 sha256,不落明文
    fingerprint = request_fingerprint(
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
        require_api_key,
        with_ssh,
        market,
        period,
        period_count,
    )
    if idempotency_key:
        existing = await find_replay(
            session,
            Instance,
            owner_col=Instance.user_id,
            owner_id=user_id,
            key=idempotency_key,
            window=IDEMPOTENCY_WINDOW,
            fingerprint=fingerprint,
        )
        if existing is not None:
            return existing, False

    sku = await catalog_service.get_on_sale_sku(session, sku_id)
    await _require_cluster_for_pool(
        session, sku.pool_label, gpu_count, with_data_disk=data_disk_id is not None
    )
    # CPU 规格(max_gpus_per_instance=0)只收 0 卡,GPU 规格只收 1..max;契约层放到 ge=0,
    # 这里是「0 卡的 GPU 实例」的唯一闸门
    if sku.max_gpus_per_instance == 0:
        if gpu_count != 0:
            raise AppError(ErrorCode.VALIDATION_ERROR, key="orchestrator.cpuSkuNoGpu")
    elif not 1 <= gpu_count <= sku.max_gpus_per_instance:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="orchestrator.gpuCountRange",
            params={"max": sku.max_gpus_per_instance},
        )
    await _validate_image_ref(session, image_ref, require_pinned=workload_type == WORKLOAD_SERVICE)
    if market == MARKET_SPOT and not sku.spot_enabled:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="orchestrator.spotNotEnabled")
    # 抢占在本函数内下发,与建实例同事务:后面任何一步失败都会把回收一起回滚
    await _soft_admit_capacity(session, sku, gpu_count, market=market, user_id=user_id)
    # service_port 必填/保留端口、subscription 必带 period 由契约层 InstanceCreate
    # 与 DB CHECK 把关,本函数唯一生产入口是路由层,不重复校验
    is_service = workload_type == WORKLOAD_SERVICE
    # dev 形态恒开 SSH(那是它唯一的登录方式);service 形态由用户勾选
    wants_ssh = with_ssh if is_service else True

    is_subscription = market == MARKET_SUBSCRIPTION
    if is_subscription and not sku.period_enabled:
        # 运营对稀缺型号关掉包周期;市场页置灰 chips,这里兜住直调接口
        raise AppError(ErrorCode.VALIDATION_ERROR, key="orchestrator.periodNotEnabled")
    # 有效时价:按量即原价,包周期按周期折扣打折(唯一折扣计算点在 core/pricing)
    policies = await get_effective_policies(session)
    unit_price = price_for(sku.price_hourly, market=market, policies=policies, period=period)

    disk_id_validated: int | None = None
    if data_disk_id is not None:
        from app.modules.orchestrator import disks as disks_service

        # 先校验归属与状态;实例 id 生成后再占用(attach 在下方,同事务重入此锁)。
        # FOR UPDATE 提前锁盘:锁序 disk → wallet——若等到钱包锁(下方临界区)之后
        # 才锁盘,则成 wallet → disk,与删盘/扩盘链路(disk → bill → wallet)交叉成死锁对
        disk = await disks_service.lock_disk_for_attach(session, user_id, data_disk_id)
        disk_id_validated = disk.id

    # 临界区开始:FOR UPDATE 锁钱包行并持有到本事务 commit,同用户并发开户串行。
    # 在途统计与配额校验必须在锁内做(先算后锁即 TOCTOU)。
    # 余额口径:在途(running 实例 + 计费态盘)+ creating/starting 待燃 + 本次新增,
    # 待燃部分由 assert_can_afford 内部经 pending_hourly 统一并入(调用方无从遗漏)。
    # 异常路径不需要手动 rollback:session 出上下文管理器时未提交事务自动回滚。
    estimate = hourly_cost(unit_price, gpu_count)
    await billing_service.lock_wallet(session, user_id)
    if not is_subscription:
        await billing_service.assert_can_afford(session, user_id, additional_hourly=estimate)
    # CPU 实例才计 vCPU 维(GPU 实例的 vCPU 是配卡的附属,不单独设闸)
    await _check_user_quota(session, user_id, gpu_count, sku.vcpu if gpu_count == 0 else 0)

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
        # service 形态不用 Jupyter,但该列非空:照常签一把,不进 Pod spec
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
        # 并发同幂等键:对方已落库,按重放返回既有实例(不多开一台)
        return result, False
    if is_subscription:
        assert period is not None  # 契约层已拦,这里给类型收敛
        # 必须先扣款再校验在途:此刻钱包余额已是扣后值,校验的才是「付完这一单还撑不
        # 撑得住已经在跑的按量资源」;creating/starting 待燃由 assert_can_afford 内部并入。
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
            # 订阅行不带幂等键:整笔创建的幂等由 instances 那行担保(同事务),
            # 两张表共用一个键会在幂等窗口过后撞车
            idempotency_key=None,
        )
        await billing_service.assert_can_afford(session, user_id)
    if disk_id_validated is not None:
        from app.modules.orchestrator import disks as disks_service

        await disks_service.attach_for_instance(session, user_id, disk_id_validated, instance.id)
    if is_service:
        assert service_port is not None  # 契约层已拦,这里给类型收敛
        await _create_service_endpoint(
            session,
            instance,
            container_port=service_port,
            health_path=health_path,
            require_api_key=require_api_key,
        )
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
            },
            created_at=now_utc(),
        )
    )
    enqueue(session, "instance.create", {"instance_id": instance.id})
    await session.commit()
    logger.info("instance_create_accepted", instance_id=instance.id, user_id=user_id)
    return instance, True


async def instance_by_id(session: AsyncSession, instance_id: int) -> Instance:
    """按主键取实例(不限归属、不限状态;行从不硬删,按 id 必命中)。
    系统侧巡检用,用户请求一律走 get_instance。"""
    return (await session.execute(select(Instance).where(Instance.id == instance_id))).scalar_one()


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
    """用户端实例列表:降序游标分页 + status 精确 / name 模糊过滤。
    name 同时匹配 uuid 前缀(同 admin_list_instances 的 q 语义);released 终态永不出列表。"""
    from app.core.pagination import Page, paginate_by_id
    from app.modules.orchestrator.schemas import InstanceOut

    stmt = (
        select(Instance)
        .where(Instance.user_id == user_id, Instance.status != sm_def.RELEASED)
        .order_by(Instance.id.desc())
    )
    if status is not None:
        stmt = stmt.where(Instance.status == status)
    name = (name or "").strip()
    if name:
        # LIKE 元字符转义:name 里的 %/_ 按字面匹配,不当通配符
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
    """回填两个住在别处的字段:服务端点 slug 与包周期概要。两次批量查询,与列表长度无关;
    列表页与详情页共用同一条路径。"""
    await _attach_service_slugs(session, items)
    await _attach_subscriptions(session, items)


async def list_expiring_instances(
    session: AsyncSession, user_id: int, *, within_days: int
) -> "list[InstanceOut]":
    """临期包周期实例(到期横幅数据源):active 订阅且 expires_at ≤ now+within_days,
    按到期时刻升序。专用轻端点,不经分页:列表筛选/翻页/首页截断都不会把临期实例藏掉。
    订阅查询在 billing 层(跨模块只经 service 门面),这里只装配实例出参。"""
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
    """单实例出参:与列表项同形。"""
    from app.modules.orchestrator.schemas import InstanceOut

    items = [InstanceOut.model_validate(instance)]
    await attach_instance_details(session, items)
    return items[0]


async def _attach_service_slugs(session: AsyncSession, items: "Sequence[InstanceOut]") -> None:
    """给列表项回填端点 slug:一次查询,不是每行一次(接口调用不得随行数放大)。"""
    ids = [i.id for i in items if i.workload_type == WORKLOAD_SERVICE]
    if not ids:
        return
    # 必须 .tuples().all():直接 dict(Result) 会走映射协议报 "not subscriptable",
    # .tuples() 还把行类型收成 tuple[int, str],dict() 的返回类型才推得出来
    slugs = dict(
        (
            await session.execute(
                select(ServiceEndpoint.instance_id, ServiceEndpoint.public_slug).where(
                    ServiceEndpoint.instance_id.in_(ids)
                )
            )
        )
        .tuples()
        .all()
    )
    for item in items:
        item.service_slug = slugs.get(item.id)


async def _attach_subscriptions(session: AsyncSession, items: "Sequence[InstanceOut]") -> None:
    """给列表项回填包周期概要:一次查询,理由同 _attach_service_slugs。"""
    from app.modules.orchestrator.schemas import InstanceSubscriptionOut

    ids = [i.id for i in items if i.market == MARKET_SUBSCRIPTION]
    if not ids:
        return
    rows = await billing_service.subscriptions_by_instance(session, ids)
    for item in items:
        row = rows.get(item.id)
        if row is not None:
            item.subscription = InstanceSubscriptionOut.model_validate(row)


async def list_events(
    session: AsyncSession, instance_id: int, *, cursor: str | None = None, limit: int | None = None
):
    """实例事件时间线:降序(最新在前)游标分页,与资金流水/账单同一套分页语义。"""
    from app.core.pagination import Page, paginate_by_id
    from app.modules.orchestrator.schemas import InstanceEventOut

    stmt = (
        select(InstanceEvent)
        .where(InstanceEvent.instance_id == instance_id)
        .order_by(InstanceEvent.id.desc())
    )
    page_items, next_cursor = await paginate_by_id(
        session, stmt, id_col=InstanceEvent.id, cursor=cursor, limit=limit
    )
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
    """(重新)占用数据盘标记:盘还在就重新占用,已被用户删掉则放弃挂载点(实例照常能开)。
    必须 FOR UPDATE 锁盘行,否则与 delete_disk 并发存在「边挂边擦」窗口;
    挂载校验(active / 配额已下发 / 未挂他处)走与创建同一入口 attach_for_instance。"""
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
    # 锁序 instance → disk → wallet(与停机尾账 instance→bill→wallet、删盘
    # disk→bill→wallet 同向):先锁实例行,挂载校验(盘锁)先于余额校验(钱包锁)。
    # 先钱包后盘/后实例会与其成交叉死锁对;锁内重读(populate_existing),
    # 状态判定对加锁后的新鲜值成立
    locked = await session.get(Instance, instance.id, with_for_update=True, populate_existing=True)
    assert locked is not None  # get_instance 刚取到,同事务内不可能消失
    instance = locked
    if instance.status == sm_def.FROZEN:
        raise AppError(ErrorCode.INSTANCE_FROZEN, key="orchestrator.frozenNeedsRecharge")
    recovered = instance.status == sm_def.FAILED
    if instance.status != sm_def.STOPPED and not recovered:
        raise AppError(ErrorCode.INSTANCE_INVALID_TRANSITION, key="orchestrator.startNeedsStopped")
    # 实例盘钉在原节点(TopoLVM node affinity):节点 Missing 时开机必 Pending 到超时转
    # failed,前置拦掉;台账无该行一律放行交调度器裁决,NotReady 属瞬时态不拦
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
        # 包周期已预付整段周期,开机不看余额;但周期已过不能再开,否则等于白送算力
        await billing_service.assert_subscription_active(session, instance.id)
    if recovered:
        # 故障恢复:failed → stopped(复用同一块实例盘)→ 走正常开机链路
        await transition(session, instance, sm_def.STOPPED, reason="failed_recover", actor="user")
    # 所有开机路径统一校验数据盘挂载:盘已删则放弃挂载点,非 active 即拒绝,防止挂到擦除中的目录。
    # 盘锁必须在钱包锁(assert_can_afford)之前(instance → disk → wallet)
    await _rebind_data_disk(session, instance)
    if instance.market != MARKET_SUBSCRIPTION:
        estimate = hourly_cost(instance.price_hourly, instance.gpu_count)
        await billing_service.assert_can_afford(session, user_id, additional_hourly=estimate)
    await transition(session, instance, sm_def.STARTING, reason="user_start", actor="user")
    # 必须清 unready_since:残留值会把新 running 段的计费截断到过去时刻,
    # 也会让 reconciler 的宽限判定立即超时
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

    换周期续(包月转包年)会改有效时价,故同时刷新 `instances.price_hourly`;
    冻结中的实例续费即解冻回 stopped,由用户自己开机。
    """
    instance = await get_instance(session, user_id, uuid)
    # 锁序 instance → wallet → subscription:末尾 transition 要拿实例写锁,
    # 先钱包后实例会与停机尾账(instance→bill→wallet)交叉成死锁对;
    # 续费的余额扣减仍在钱包锁内完成(renew 契约不变)。
    # 状态判定对锁内重读(populate_existing)的新鲜值成立
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
        # 幂等重放:并发撞键那条路径在 billing 侧 rollback 过,手上的 instance 已失效
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

    必须先结清转换前那段按量账再翻 `market`:反过来 `billing_candidates` 会按新 market
    排除该实例,水位线之后未出账的小时永远没人结;结算也要用转换前的按量时价。
    只收 running / stopped:在途状态翻 market 会和收敛路径抢同一行,frozen 得先还清欠账。
    """
    instance = await get_instance(session, user_id, uuid)
    if idempotency_key:
        # 重放必须最先问:转换后 market 已是 subscription,重放会撞上「只有按量实例可以转」
        # 拿到 400,还会拿已是折后价的 price_hourly 再补一笔本该按按量收的账
        # 给全三个定位参数才会做异参检测:转换与续费共用 UNIQUE(user_id, idempotency_key)
        # 一个命名空间,缺了它们同键打向另一台实例会把**别人那单**当本次重放返回 ——
        # 用户被告知「买好了」,可目标实例既没转成包周期也没扣钱
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
    # 锁序 instance → bill → wallet:结清(settle_on_demand_up_to)内部经
    # lock_instance_for_billing 重入实例锁、经扣款拿钱包锁;若在此先锁钱包,
    # 则成 wallet → instance,与停机尾账/小时结算(instance→bill→wallet)
    # 交叉成死锁对。状态判定必须对锁内重读(populate_existing)的新鲜值成立
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

    # 转订阅前逐小时结清(含 48h 滞后熔断):billing_candidates 按**当前** market 挑
    # 候选,market 一翻成 subscription,水位线后未出账的小时永远没人结。
    # stopped 同样要结:停机尾账只补停机那一小时,结算滞后时的在账窗口不能孤儿化;
    # 停机后的窗口按事件重建秒数为 0(空账行,不扣款),滞后健康时只有当前一小时
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
    # 转换后余额还得撑得住其它在途按量资源(与建包周期实例同一条判据)
    await billing_service.assert_can_afford(session, user_id)
    await session.commit()
    logger.info("instance_converted_to_subscription", instance_id=instance.id, period=period)
    return instance, quoted, True


async def convert_to_on_demand(session: AsyncSession, user_id: int, uuid: str) -> Instance:
    """竞价实例转按量(免被回收)。已经是按量则原样返回(幂等,不报错)。

    翻 `market` 不动 Pod、不重调度,零中断。计价口径是一小时一价:转换把当前整点小时整体
    改按按量价(billing.reprice_current_hour),`bills_hourly` 一小时只有一个 unit_price。
    原价从 `spec.base_price_hourly` 取,不从折后价反推(折扣策略在线可调)。
    """
    instance = await get_instance(session, user_id, uuid)
    if instance.market == MARKET_ON_DEMAND:
        return instance  # 幂等:目标状态已达成
    # 锁序 instance → bill → wallet:reprice_current_hour 经账单行锁与扣款拿
    # 钱包锁;若在此先锁钱包,则成 wallet → bill,与小时结算
    # (upsert_hour_bill:账单行锁 → 钱包锁)交叉成死锁对。
    # 状态判定对锁内重读(populate_existing)的新鲜值成立
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
    # 转按量后单价涨了,余额得撑得住新的燃烧率:撑不住的话转完立刻会被欠费巡检停机
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


async def release_instance(
    session: AsyncSession, user_id: int, uuid: str, *, actor: str = "user"
) -> Instance:
    instance = await get_instance(session, user_id, uuid)
    if instance.status in (sm_def.RELEASING, sm_def.RELEASED):
        # 幂等释放:释放中/已释放直接回当前状态,重试/双击不报 400
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
    endpoint: ServiceEndpoint | None = None,
) -> InstancePodSpec:
    """构造 Pod spec。data_disk_subpath 必须由调用方从 `data_disks.juicefs_subpath` 读出传入,
    就地重算会与擦除路径对不上;image_pull_secret 是托管到该 ns 的拉取凭据 Secret 名;
    endpoint 是服务型实例的端点行(service 形态必传,dev 形态恒 None)。

    dev/service 两形态的差别集中在本函数,k8s 层只按 spec 字段建对象。
    """
    settings = get_settings()
    gpu_req = spec_to_gpu_request(
        instance.spec,
        instance.gpu_count,
        hami_use_gputype=settings.hami_use_gputype,
        distro=distro,
    )
    is_service = instance.workload_type == WORKLOAD_SERVICE
    if is_service and endpoint is None:
        raise RuntimeError(f"service instance {instance.uuid} has no service_endpoints row")
    # 开没开 SSH 只认 instances.with_ssh,不从 ssh_port 反推:端口是 outbox 建 Pod 时才分配的,
    # 不要 SSH 的实例不进端口池,ssh_port 恒 None 属正常态
    if instance.with_ssh and instance.ssh_port is None:
        raise RuntimeError("build_pod_spec requires allocated ssh_port")
    plain_env, secret_env = instance_env(instance)
    if is_service:
        # 服务容器不跑 Jupyter:不注入 JUPYTER_*,也不把 token 塞进 Secret
        env = plain_env
        secrets_ = secret_env
    else:
        env = {
            # 实例自己的域名:镜像据此收敛 Jupyter 的 Origin 校验(防跨站 WebSocket)
            "JUPYTER_ALLOW_ORIGIN": jupyter_origin(instance.uuid, settings),
        }
        # token 走 per-instance Secret(secretKeyRef),不以明文 env 落 Pod spec:
        # spec 会进 etcd/审计快照,任何 pods:get/list 身份(含只读 SA)都能读走
        secrets_ = {"JUPYTER_TOKEN": _token_plain(instance)}
    # 规格倍率:GPU 实例按卡数放大 CPU/内存(N 卡收 N 倍价,Guaranteed QoS 下 CPU 是硬限,
    # 否则多卡被单份 CPU 饿死);CPU 实例倍率恒 1。系统盘任何形态都不随卡数放大
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
        # service 形态不建 Jupyter 路由,但这里照常填:它同时是 SSH 的展示主机名
        jupyter_host=jupyter_host(instance.uuid, settings),
        env=env,
        secret_env=secrets_,
        authorized_keys=tuple(instance.authorized_keys),
        node_selector=gpu_req.node_selector,
        data_disk_subpath=data_disk_subpath,
        scheduler_name=gpu_req.scheduler_name,
        annotations=gpu_req.annotations,
        image_pull_secret=image_pull_secret,
        # 服务容器必须 Always 原地重启:Never 会让一次崩溃变成实例终结,而 Pod 重建换名字后
        # reconciler「Pod 名 = 实例 uuid」的假设会整套塌掉
        restart_policy="Always" if is_service else "Never",
        command=tuple(instance.container_command) if instance.container_command else None,
        args=tuple(instance.container_args) if instance.container_args else None,
        service_port=endpoint.container_port if endpoint else None,
        service_host=(service_endpoint_host(endpoint.public_slug, settings) if endpoint else None),
        health_path=endpoint.health_path if endpoint else None,
        with_ssh=instance.with_ssh,
    )


async def get_service_endpoint(session: AsyncSession, instance_id: int) -> ServiceEndpoint | None:
    return (
        await session.execute(
            select(ServiceEndpoint).where(ServiceEndpoint.instance_id == instance_id)
        )
    ).scalar_one_or_none()


async def build_pod_spec_with_cluster(
    session: AsyncSession, instance: Instance, *, image_pull_secret: str | None = None
) -> InstancePodSpec:
    """outbox handler 用:带集群发行版上下文(k3s → shared 档显式 runtimeClassName)、
    数据盘 subPath(从盘记录读,不就地重算)与服务端点行。"""
    row = await nodes_service.get_cluster_status(session)
    subpath: str | None = None
    if instance.data_disk_id is not None:
        disk = await session.get(DataDisk, instance.data_disk_id)
        if disk is None:
            raise RuntimeError(f"data disk {instance.data_disk_id} missing for {instance.uuid}")
        subpath = disk.juicefs_subpath
    endpoint = (
        await get_service_endpoint(session, instance.id)
        if instance.workload_type == WORKLOAD_SERVICE
        else None
    )
    return build_pod_spec(
        instance,
        distro=row.distro if row else None,
        data_disk_subpath=subpath,
        image_pull_secret=image_pull_secret,
        endpoint=endpoint,
    )


# ---------- 接入信息 ----------


def build_access(instance: Instance, endpoint: ServiceEndpoint | None = None) -> dict[str, Any]:
    """接入信息。按形态给字段:没有的入口不回空串占位,直接缺席(契约全可空)。"""
    settings = get_settings()
    if instance.status != sm_def.RUNNING:
        raise AppError(ErrorCode.INSTANCE_INVALID_TRANSITION, key="orchestrator.accessNeedsRunning")
    out: dict[str, Any] = {}
    if instance.with_ssh:
        # SSH 没有主机名语义(只靠 NodePort 区分实例),主机名直接用实例域名,与 Jupyter 同名
        ssh_host = jupyter_host(instance.uuid, settings)
        out["ssh_host"] = ssh_host
        out["ssh_port"] = instance.ssh_port
        out["ssh_command"] = f"ssh root@{ssh_host} -p {instance.ssh_port}"
    if instance.workload_type == WORKLOAD_DEV:
        # 一次性入场票据:bootstrap handler 验签核销后 Set-Cookie 再跳 Jupyter,token 不进 URL
        out["jupyter_url"] = _new_jupyter_ticket(instance, _token_plain(instance))
    if endpoint is not None:
        out["endpoint_url"] = f"https://{service_endpoint_host(endpoint.public_slug, settings)}"
    return out


async def get_access(session: AsyncSession, user_id: int, uuid: str) -> dict[str, Any]:
    """路由入口:取实例(带 owner 校验)+ 服务端点,再拼接入信息。"""
    instance = await get_instance(session, user_id, uuid)
    endpoint = (
        await get_service_endpoint(session, instance.id)
        if instance.workload_type == WORKLOAD_SERVICE
        else None
    )
    return build_access(instance, endpoint)


# ---------- 服务端点与访问密钥 ----------


async def _require_service_endpoint(
    session: AsyncSession, user_id: int, uuid: str
) -> tuple[Instance, ServiceEndpoint]:
    """取「用户自己的服务型实例 + 端点行」。非属主 404(get_instance),非服务型 404。"""
    instance = await get_instance(session, user_id, uuid)
    endpoint = (
        await get_service_endpoint(session, instance.id)
        if instance.workload_type == WORKLOAD_SERVICE
        else None
    )
    if endpoint is None:
        raise AppError(
            ErrorCode.SERVICE_ENDPOINT_NOT_FOUND,
            key="orchestrator.serviceEndpointNotFound",
            http_status=http_status.HTTP_404_NOT_FOUND,
        )
    return instance, endpoint


async def service_endpoint_view(
    session: AsyncSession, user_id: int, uuid: str
) -> "ServiceEndpointOut":
    from app.modules.orchestrator.schemas import ServiceEndpointOut

    instance, endpoint = await _require_service_endpoint(session, user_id, uuid)
    plain_env, secret_env = instance_env(instance)
    return ServiceEndpointOut(
        slug=endpoint.public_slug,
        url=f"https://{service_endpoint_host(endpoint.public_slug)}",
        container_port=endpoint.container_port,
        health_path=endpoint.health_path,
        require_api_key=endpoint.require_api_key,
        # 只读 DB:请求路径不碰 K8s。unready_since 由巡检写,是 Pod 就绪的库内投影
        ready=instance.status == sm_def.RUNNING and instance.unready_since is None,
        container_command=list(instance.container_command) if instance.container_command else None,
        container_args=list(instance.container_args) if instance.container_args else None,
        # 密文项只回键名不回值:回值就成了「把密文变量读回明文」的入口
        env=plain_env,
        env_secret_keys=sorted(secret_env),
        created_at=endpoint.created_at,
    )


async def list_api_keys(session: AsyncSession, user_id: int, uuid: str) -> list[ServiceApiKey]:
    """列出该服务实例的访问密钥(含已吊销的:吊销记录本身是审计线索)。"""
    instance, _ = await _require_service_endpoint(session, user_id, uuid)
    return list(
        (
            await session.execute(
                select(ServiceApiKey)
                .where(ServiceApiKey.instance_id == instance.id)
                .order_by(ServiceApiKey.id.desc())
            )
        ).scalars()
    )


async def create_api_key(
    session: AsyncSession, user_id: int, uuid: str, *, name: str
) -> tuple[ServiceApiKey, str]:
    """新建访问密钥。返回 (行, 明文);明文只此一次,库里只有 HMAC 摘要。
    不支持 Idempotency-Key:重放要回同一份明文就得把明文留在库里,与只存摘要冲突。"""
    instance, _ = await _require_service_endpoint(session, user_id, uuid)
    # 计数必须串行:FOR UPDATE 锁实例行(实例:密钥 = 1:N),并发建钥在实例行上排队;
    # 否则 count-then-insert 两请求同见 19 把、各插一把越过上限
    await session.execute(select(Instance.id).where(Instance.id == instance.id).with_for_update())
    live = (
        await session.execute(
            select(func.count())
            .select_from(ServiceApiKey)
            .where(ServiceApiKey.instance_id == instance.id, ServiceApiKey.revoked_at.is_(None))
        )
    ).scalar_one()
    if live >= MAX_API_KEYS_PER_INSTANCE:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="orchestrator.apiKeyQuota",
            params={"max": MAX_API_KEYS_PER_INSTANCE},
        )
    plaintext = f"sk-{secrets.token_urlsafe(32)}"
    row = ServiceApiKey(
        user_id=user_id,
        instance_id=instance.id,
        name=name,
        key_hash=hash_api_key(plaintext),
        # 前 11 位(sk- + 8 位):够用户在列表里认出是哪一把,又不足以缩小爆破空间
        key_prefix=plaintext[:11],
    )
    session.add(row)
    await session.commit()
    return row, plaintext


async def strip_ssh_key_from_instances(session: AsyncSession, user_id: int, public_key: str) -> int:
    """把某把公钥从该用户全部未释放实例的 authorized_keys 快照里摘掉。返回摘除的实例数。

    供删除 SSH 公钥调用(账号侧):运行中 Pod 的容器内 authorized_keys 由 entrypoint
    在建 Pod 时写定,平台无 exec 通道,运行实例要等下次重启才失效;但此后的任何
    重启/重建都不再把已删的钥匙带回来。
    """
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


async def revoke_api_key(
    session: AsyncSession, user_id: int, uuid: str, key_id: int
) -> ServiceApiKey:
    """吊销访问密钥:写 revoked_at,不删行(谁在什么时候吊销了哪把,得留得下来)。"""
    instance, _ = await _require_service_endpoint(session, user_id, uuid)
    row = (
        await session.execute(
            select(ServiceApiKey).where(
                ServiceApiKey.id == key_id, ServiceApiKey.instance_id == instance.id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise not_found(key="orchestrator.apiKeyNotFound")
    if row.revoked_at is None:  # 重复吊销幂等:不刷新时刻,首次吊销的时点才是审计事实
        row.revoked_at = now_utc()
        await session.commit()
        # 鉴权缓存主动失效:本进程即刻拒,跨副本最坏一个 TTL(5s)收敛
        invalidate_endpoint_auth_cache(key_id=row.id)
    return row


@dataclass(frozen=True)
class EndpointAuthResult:
    """鉴权通过的凭据。key_id 为 None = 该端点不要求 API Key(公开端点)。"""

    slug: str
    key_id: int | None


def _endpoint_denied() -> AppError:
    return AppError(
        ErrorCode.API_KEY_INVALID,
        key="orchestrator.apiKeyInvalid",
        http_status=http_status.HTTP_401_UNAUTHORIZED,
    )


# ---------- extAuth 鉴权缓存 ----------
# extAuth 回调挂在每个 svc-https 请求的同步路径上:无缓存时每请求 3 次 SELECT +
# 一次 UPDATE+commit(last_used_at),数据面流量直接放大成中央库写压。
# 正向结果进程内缓存 5s:
# - 吊销:revoke_api_key 主动失效本进程条目,跨副本最坏一个 TTL 收敛;
# - 停机:RUNNING 迁出经 transition 监听器失效(同进程),跨副本 TTL 兜底;
#   停机同时删 svc HTTPRoute(网关 404),缓存只影响优雅删除窗口;
# - 负结果不缓存:爆破每次回源(主键级 SELECT、无写),新建密钥立即可用。
# last_used_at 从「每请求直写」降为「每 key 每 60s 至多一写」:它是排查
# 「钥匙还被谁用」的审计线索,分钟级粒度足够,写放大从 rps 降为 key 数/分钟。
_ENDPOINT_AUTH_CACHE_TTL_SECONDS = 5.0
_ENDPOINT_AUTH_CACHE_MAX = 4096
_LAST_USED_WRITE_INTERVAL_SECONDS = 60.0


@dataclass(frozen=True)
class _EndpointAuthCacheEntry:
    result: EndpointAuthResult
    instance_id: int
    key_id: int | None
    expires_at: float  # time.monotonic 口径


_endpoint_auth_cache: dict[tuple[str, str], _EndpointAuthCacheEntry] = {}
_endpoint_key_last_write: dict[int, float] = {}


def _endpoint_auth_cache_get(slug: str, key_hash: str) -> _EndpointAuthCacheEntry | None:
    entry = _endpoint_auth_cache.get((slug, key_hash))
    if entry is None or entry.expires_at <= time.monotonic():
        return None
    return entry


def _endpoint_auth_cache_put(slug: str, key_hash: str, entry: _EndpointAuthCacheEntry) -> None:
    cache = _endpoint_auth_cache
    if len(cache) >= _ENDPOINT_AUTH_CACHE_MAX:
        # key 来自任意外网输入,表必须有界:先清过期,仍满则整表清空(宁可回源不可无界)
        now = time.monotonic()
        for k in [k for k, v in cache.items() if v.expires_at <= now]:
            del cache[k]
        if len(cache) >= _ENDPOINT_AUTH_CACHE_MAX:
            cache.clear()
    cache[(slug, key_hash)] = entry


def invalidate_endpoint_auth_cache(
    *, key_id: int | None = None, instance_id: int | None = None
) -> None:
    """主动失效(吊销按 key、停机按实例);表 ≤4096,全扫代价可忽略。"""
    doomed = [
        k
        for k, v in _endpoint_auth_cache.items()
        if (key_id is not None and v.key_id == key_id)
        or (instance_id is not None and v.instance_id == instance_id)
    ]
    for k in doomed:
        del _endpoint_auth_cache[k]


def clear_endpoint_auth_cache() -> None:
    """测试隔离用:函数级 TRUNCATE 清库清不到进程内缓存。"""
    _endpoint_auth_cache.clear()
    _endpoint_key_last_write.clear()


async def _touch_key_last_used(session: AsyncSession, key_id: int | None) -> None:
    """last_used_at 节流直写:每 key 每进程 60s 至多一次 UPDATE+commit。"""
    if key_id is None:
        return
    now = time.monotonic()
    last = _endpoint_key_last_write.get(key_id)
    if last is not None and now - last < _LAST_USED_WRITE_INTERVAL_SECONDS:
        return
    await session.execute(
        update(ServiceApiKey).where(ServiceApiKey.id == key_id).values(last_used_at=now_utc())
    )
    await session.commit()
    _endpoint_key_last_write[key_id] = now


async def verify_endpoint_key(
    session: AsyncSession, *, slug: str | None, key: str | None
) -> EndpointAuthResult:
    """网关 extAuth 回调的校验链:端点存在 → 实例 running → 密钥有效且属于该端点。
    任一环节不过都抛同一个 401(同码同文案),区分开等于给第三方一个枚举平台端点的预言机。"""
    if not slug:
        raise _endpoint_denied()
    # candidates 兼读主密钥轮换/legacy 世代(见 crypto.py);缓存键取当前世代([0]),稳定
    key_hashes = hash_api_key_candidates(key) if key else []
    key_hash = key_hashes[0] if key_hashes else ""
    cached = _endpoint_auth_cache_get(slug, key_hash)
    if cached is not None:
        await _touch_key_last_used(session, cached.key_id)
        return cached.result
    endpoint = (
        await session.execute(select(ServiceEndpoint).where(ServiceEndpoint.public_slug == slug))
    ).scalar_one_or_none()
    if endpoint is None:
        raise _endpoint_denied()
    instance = await session.get(Instance, endpoint.instance_id)
    # 非 running 一律拒:Pod 可能还在优雅删除期里活着,光靠删 HTTPRoute 收口有窗口
    if instance is None or instance.status != sm_def.RUNNING:
        raise _endpoint_denied()
    expires = time.monotonic() + _ENDPOINT_AUTH_CACHE_TTL_SECONDS
    if not endpoint.require_api_key:
        result = EndpointAuthResult(slug=endpoint.public_slug, key_id=None)
        _endpoint_auth_cache_put(
            slug, key_hash, _EndpointAuthCacheEntry(result, instance.id, None, expires)
        )
        return result
    if not key:
        raise _endpoint_denied()
    row = (
        await session.execute(
            select(ServiceApiKey).where(ServiceApiKey.key_hash.in_(key_hashes)).limit(1)
        )
    ).scalar_one_or_none()
    # instance_id 比对是「A 用户的密钥打 B 用户端点」的唯一闸门:密钥是全局唯一的
    # 高熵串,查得到不等于用得上
    if row is None or row.revoked_at is not None or row.instance_id != endpoint.instance_id:
        raise _endpoint_denied()
    result = EndpointAuthResult(slug=endpoint.public_slug, key_id=row.id)
    _endpoint_auth_cache_put(
        slug, key_hash, _EndpointAuthCacheEntry(result, instance.id, row.id, expires)
    )
    await _touch_key_last_used(session, row.id)
    return result


_endpoint_cache_listener_registered = False


def register_endpoint_auth_cache_listener() -> None:
    """RUNNING 迁出即失效该实例的鉴权缓存(幂等注册;跨进程副本由 TTL 兜底收敛)。"""
    global _endpoint_cache_listener_registered
    if _endpoint_cache_listener_registered:
        return

    async def _drop_on_leave_running(
        _session: AsyncSession, instance: Instance, event: InstanceEvent
    ) -> None:
        if event.from_status == sm_def.RUNNING:
            invalidate_endpoint_auth_cache(instance_id=instance.id)

    register_transition_listener(_drop_on_leave_running)
    _endpoint_cache_listener_registered = True


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


async def read_instance_logs(
    session: AsyncSession, user_id: int, uuid: str, *, tail_lines: int
) -> "InstanceLogsOut":
    """读取实例容器日志(只读;不记审计)。

    owner 校验(非属主 404,不暴露存在性);仅 running/stopping 可取(其余状态 Pod 已删,
    409 给明确文案);tail_lines 超上限按上限截断而非 422(≤2000)。
    """
    from app.modules.orchestrator.schemas import InstanceLogsOut

    instance = await get_instance(session, user_id, uuid)
    if instance.status not in (sm_def.RUNNING, sm_def.STOPPING):
        raise conflict(key="orchestrator.logsNeedsRunning")
    await check_rate_limit(f"instance-logs:{user_id}", max_attempts=20, window_seconds=3600.0)
    tail = min(tail_lines, LOGS_MAX_TAIL_LINES)
    try:
        # +1 行探路:拿回的行数超过 tail 即知前面还有,truncated 标记由此而来
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
    """市场近似库存(批量):sku_id → 可售实例数。

    数据源是节点台账(node_specs,巡检 60s 粒度),请求路径不碰 K8s,一次查询供全部 SKU;
    台账无该池×型号数据 → 0。必须减掉包周期预留(与软准入同源),否则市场页显示有货而创建 409。
    """
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
    """管理端实例列表(游标分页,降序)。q 按实例名或 uuid 前缀匹配,node_name 精确。"""
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
        # LIKE 元字符转义:q 里的 %/_ 按字面匹配,不当通配符
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
    """管理端强制回收一台竞价实例(腾容量用),走与自动抢占同一条回收路径。
    与 admin_force_stop 分开:两者 reason 不同,用户时间线与竞价可靠性指标要分得开。"""
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
    """平台侧停机(巡检调用,actor=system)。同事务落事件 + outbox,不 commit。
    reason 由调用方给(欠费 arrears_stop / 到期 subscription_expired),不共用。"""
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
    """停掉该用户全部 running 实例(封禁/风控处置用)。同事务落事件 + outbox,不 commit。
    返回被停的台数;creating/starting 本轮停不了,收敛到 running 后由 billing.patrol 兜住。"""
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
