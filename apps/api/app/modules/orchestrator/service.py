"""编排服务:实例生命周期的唯一入口(门面)。

事务纪律:
- 状态变更只走 transition()(乐观锁 + 同事务 instance_events + 迁移监听器)
- 「改 DB + 动 K8s」一律 outbox;请求路径绝不直接调 K8s

拆分:状态迁移原语 → transitions.py;SSH 端口池 → ports.py;billing/管理端
查询聚合 → queries.py。本文件保留创建/操作/接入/日志主链路,并再导出全部拆出符号,
跨模块仍只经 app.modules.orchestrator.service 访问(lint-imports 契约不变)。
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
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.crypto import decrypt_str, encrypt_str, hash_api_key
from app.core.errors import AppError, ErrorCode, not_found
from app.core.gpu_adapter import POOL_HAMI, POOL_KATA, TIER_CPU, spec_to_gpu_request
from app.core.gpu_models import canonical_gpu_model
from app.core.idempotency import IDEMPOTENCY_WINDOW, find_replay
from app.core.k8s import InstancePodSpec, get_orchestrator
from app.core.logging import get_logger
from app.core.money import as_amount, hourly_cost
from app.core.outbox import enqueue
from app.core.pagination import RawPage
from app.core.platform_config import get_effective_platform_config
from app.core.policies import get_effective_policies
from app.core.pricing import (
    MARKET_ON_DEMAND,
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
from app.modules.orchestrator.schemas import (
    RESERVED_SERVICE_PORTS,
    WORKLOAD_DEV,
    WORKLOAD_SERVICE,
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
    from app.modules.orchestrator.schemas import (
        InstanceLogsOut,
        InstanceOut,
        ServiceEndpointOut,
    )

logger = get_logger(__name__)

# 单实例访问密钥上限。密钥行永不删(吊销只写 revoked_at,审计要看得见谁在什么时候
# 吊销了哪把),没有上限就等于给已鉴权用户开了一条无限追加写的口子
MAX_API_KEYS_PER_INSTANCE = 20
# 端点公网域名左标签前缀,与 deploy 侧 Gateway listener 的 hostname 通配同一形态
ENDPOINT_SLUG_PREFIX = "ep-"
_SLUG_ATTEMPTS = 3


def jupyter_host(instance_uuid: str, settings: Settings | None = None) -> str:
    """实例 Jupyter 主机名:<jupyter_host_prefix><uuid>.<jupyter_domain_suffix>。
    Ingress host / 入场 URL / JUPYTER_ALLOW_ORIGIN 三处必须同一口径,只从这里拼。"""
    s = settings or get_settings()
    return f"{s.jupyter_host_prefix}{instance_uuid}.{s.jupyter_domain_suffix}"


def service_endpoint_host(slug: str, settings: Settings | None = None) -> str:
    """服务端点主机名:<slug>.<service_domain_suffix>。

    HTTPRoute hostname / 用户看到的 URL / 鉴权回调解析 slug 三处同一口径,只从这里拼。
    """
    s = settings or get_settings()
    return f"{slug}.{s.service_domain_suffix}"


def endpoint_slug_from_host(host: str | None) -> str | None:
    """从 Host 头反解端点 slug。不匹配本环境的服务域名后缀一律 None(交调用方拒绝)。

    比对后缀而不是「取第一段」:后者会把 <slug>.app.<域名>(Jupyter 域)也认成端点,
    等于让 Jupyter 域名成为鉴权端点的别名。
    """
    if not host:
        return None
    name = host.split(":")[0].strip().rstrip(".").lower()
    suffix = f".{get_settings().service_domain_suffix.lower()}"
    if not name.endswith(suffix):
        return None
    slug = name[: -len(suffix)]
    # 只收单段左标签:多段说明是 <x>.<slug>.svc.<域名> 这种,不属本平台签发的端点
    return slug if slug and "." not in slug else None


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


async def _require_cluster_for_pool(
    session: AsyncSession,
    pool_label: str | None,
    gpu_count: int,
    *,
    with_data_disk: bool = False,
) -> None:
    """下发门禁:能力缺位即时 409,而非等 Pod Pending 到超时。

    判据与 `build_gpu_request` 完全同源 —— **先看要不要卡,再看落哪个池**:
    - `gpu_count == 0`(CPU 实例)不申请任何 `nvidia.com/*`、`scheduler_name` 为 None,
      走默认调度器。**即使它挂在 hami 池上,hami-scheduler 也不是它落地的前置**,
      拿 HAMi 就绪去拦它,等于让 HAMi 挂掉连带挡住一批根本不用 GPU 的实例。
    - 其余按池判:HAMi 只有 hami 池依赖,Kata RuntimeClass 只有 kata 池依赖
      (mig 池由 gpu-operator 的 MIG manager 管,无独立门禁项)。

    StorageClass 实例盘人人要挂,数据盘按需 —— 这一条与要不要卡无关。
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

    明文项也一起加密:分两列存会让「哪些键是密文」这件事本身泄漏给任何能读这张表的
    身份,而分列没有任何收益 —— 明文项的值同样是用户数据。
    """
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
    # 服务型实例要求版本钉死:它 restartPolicy=Always,可变 tag 会让一次无人值守的
    # 容器重启把线上服务换成另一个版本(见 core.registry.is_pinned_image_ref)
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

    生效值走统一校验链(account.get_user_limits:用户覆盖 → 平台策略 → env 默认);
    vCPU 维只有平台策略层(`max_vcpus_per_user`),无用户级覆盖列。

    两维刻意互不相交:GPU 实例只吃 `max_gpus_per_user`,CPU 实例只吃
    `max_vcpus_per_user`。让 GPU 实例也计 vCPU,会让一个 8 卡户被 CPU 额度先卡死;
    让 CPU 实例计 GPU,则是拿 0 去比上限,等于没有闸门。
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
    """该 SKU 的近似可分配量:返回 (匹配台账行数据是否存在的哨兵, 可售实例数)。

    第一项为 None 表示台账无此池(×型号)数据,调用方据此放行交调度器裁决;
    GPU 档下第一项是「Ready 空闲卡合计」,CPU 档下只是匹配到的节点行数(无卡可数)。
    只有 Ready 节点计入:NotReady/Cordoned/Missing 不卖。

    GPU 档:(池, 型号) 匹配 + 按算力份额折算可售实例数(超卖生效在调度层)。
    CPU 档:**不按型号匹配**(gpu_model 是空串,按型号匹配对它恒不成立),只按池;
    可售数走 catalog.sellable_cpu_slots 的 vCPU/内存上限口径。
    两条都走 nodes/catalog 的公共口径,与管理端容量预览同一份算法。
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


async def _soft_admit_capacity(session: AsyncSession, sku: "Sku", gpu_count: int) -> None:
    """创建软准入:台账明确显示该 (池, 型号) 可分配量不足 → 即时 409。

    台账 60s 粒度,只是近似:无数据(巡检未覆盖/全新集群)一律放行,交调度器裁决;
    放行后仍可能调度超时转 failed,本判断只挡「确定卖不出去」的单。
    """
    specs = await nodes_service.list_node_specs(session)
    cap = (await get_effective_policies(session)).gpu_node_cpu_instance_vcpu_cap
    matching_free, sellable = _sku_free_capacity(sku, specs, gpu_node_vcpu_cap=cap)
    if matching_free is None:
        return
    sellable -= await _reserved_slots(session, sku)
    # 要占几份容量:GPU 实例按卡数,CPU 实例(gpu_count=0)占 1 台的位置。
    # 写成 max(1, gpu_count) 会让「0 卡要 0 份」这种恒成立的比较悄悄放行所有 CPU 单
    needed = gpu_count if gpu_count > 0 else 1
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
    """sku_id → 被未到期包周期实例占住的槽位数(**一次查询**,市场页按 SKU 批量取)。

    台账里 `gpu_used` 只数真在跑的 Pod,包月用户关一晚机,他那张卡在台账上就是空闲的;
    别人买走之后他早上开不了机 —— 那是比超卖更难向他解释的事故。这里在控制面层面
    把周期内的实例继续算作占用(**物理层不预留**,卡确实空着,这一点必须在创建页
    与 docs/reference/billing.md 里写明)。

    只算**同一条 SKU** 的停机/冻结实例:同池同型号但规格不同的实例,槽位大小不一样,
    折算成本 SKU 的槽位数只会给出一个假精确的值,而软准入本来就是近似闸门。
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
    """单条 SKU 的包周期预留槽位(创建软准入用)。口径见 _reserved_slots_by_sku。

    台账里 `gpu_used` 只数真在跑的 Pod,包月用户关一晚机,他那张卡在台账上就是空闲的;
    别人买走之后他早上开不了机 —— 那是比超卖更难向他解释的事故。这里在控制面层面
    把周期内的实例继续算作占用(**物理层不预留**,卡确实空着,这一点必须在创建页
    与 docs/reference/billing.md 里写明)。

    """
    return (await _reserved_slots_by_sku(session, [sku.id])).get(sku.id, 0)


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
    """公网端点左标签:ep- + 10 位 base32(约 50 bit 熵)。

    刻意不用 instance.uuid:内部主键不该出现在公网域名、TLS SNI、访问日志与
    第三方 Referer 里 —— 那等于把「有多少台实例、编号怎么排」白送出去。
    """
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
    """建服务端点行。slug 撞 UNIQUE 就换一个重试,最多 3 次。

    每次插入包在 SAVEPOINT 里:不包的话一次碰撞会把整笔建实例事务打成
    rollback-only,重试的第二次插入必然再炸,「重试 3 次」形同虚设。
    """
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

    service 形态额外落一行 service_endpoints,并按 with_ssh 决定要不要 SSH 入口;
    dev 形态的入参与行为逐字不变。

    market='subscription' 时同事务里再多做两件事:落一行 subscriptions、按周期总价
    一次性扣款(不允许透支)。扣完还要过一遍在途燃烧率校验 —— 「买得起包月、但买完
    连正在跑的按量实例都撑不到下一小时」不是我们该放行的单。
    """
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
    await _require_cluster_for_pool(
        session, sku.pool_label, gpu_count, with_data_disk=data_disk_id is not None
    )
    # gpu_count 的下界随 SKU 形态走:CPU 规格(max_gpus_per_instance=0)只收 0,
    # GPU 规格只收 1..max。契约层放开到 ge=0 之后,这里是「0 卡的 GPU 实例」的唯一闸门
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
    await _soft_admit_capacity(session, sku, gpu_count)
    # 服务端口的三层同源闸门之一(另两层:契约层 InstanceCreate、DB CHECK)。
    # service 层这层不是冗余:service.create_instance 是唯一入口,巡检/管理端/脚本
    # 绕过契约层直调时,只有这里还挡着
    is_service = workload_type == WORKLOAD_SERVICE
    if is_service:
        if service_port is None:
            raise AppError(ErrorCode.SERVICE_PORT_INVALID, key="orchestrator.servicePortRequired")
        if service_port in RESERVED_SERVICE_PORTS:
            raise AppError(
                ErrorCode.SERVICE_PORT_INVALID,
                key="orchestrator.servicePortReserved",
                params={"port": service_port},
            )
    # dev 形态恒开 SSH(那是它唯一的登录方式);service 形态由用户勾选
    wants_ssh = with_ssh if is_service else True

    is_subscription = market == MARKET_SUBSCRIPTION
    if is_subscription:
        if period is None:
            raise AppError(ErrorCode.VALIDATION_ERROR, key="orchestrator.periodRequired")
        if not sku.period_enabled:
            # 运营对稀缺型号关掉包周期:不许有人一次把它锁走一年。
            # 市场页会把 chips 置灰,这里兜住直调接口
            raise AppError(ErrorCode.VALIDATION_ERROR, key="orchestrator.periodNotEnabled")
    # 有效时价:按量即原价,包周期按周期折扣打折(唯一折扣计算点在 core/pricing)
    policies = await get_effective_policies(session)
    unit_price = price_for(sku.price_hourly, market=market, policies=policies, period=period)

    # 临界区开始:FOR UPDATE 锁钱包行并持有到本事务 commit,同用户并发开户串行。
    # 在途统计与配额校验必须在锁内做(先算后锁即 TOCTOU)。
    # 余额口径:在途(running 实例 + 计费态盘)+ creating/starting 待燃 + 本次新增。
    estimate = hourly_cost(unit_price, gpu_count)
    try:
        await billing_service.lock_wallet(session, user_id)
        pending = await _pending_hourly(session, user_id)
        if not is_subscription:
            await billing_service.assert_can_afford(
                session, user_id, additional_hourly=as_amount(estimate + pending)
            )
        # CPU 实例才计 vCPU 维(GPU 实例的 vCPU 是配卡的附属,不单独设闸)
        await _check_user_quota(session, user_id, gpu_count, sku.vcpu if gpu_count == 0 else 0)

        selected: list[str] = []
        if wants_ssh:
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
            price_hourly=unit_price,
            gpu_count=gpu_count,
            market=market,
            image_ref=image_ref,
            status=sm_def.CREATING,
            k8s_namespace=f"{get_settings().k8s_namespace_prefix}{user_id}",
            # service 形态没有 Jupyter,但列非空:照常签一把(不进 Pod spec),
            # 免得为一个用不到的字段开一次可空迁移
            jupyter_token=_encode_token(jupyter_token, instance_uuid=instance_uuid),
            authorized_keys=selected,
            data_disk_id=disk_id_validated,
            idempotency_key=idempotency_key,
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
        if is_subscription:
            assert period is not None  # 上面已拦,这里给类型收敛
            # 先扣款(余额不够即 INSUFFICIENT_BALANCE,文案直指余额),再校验在途:
            # 此刻钱包行上的余额已是扣后值,assert_can_afford 校验的正是「付完这一单
            # 还撑不撑得住已经在跑的按量资源」
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
                # 两张表共用一个键反而会在 24h 窗口过后撞车 —— 实例行到期释放键位、
                # 订阅行还占着,同一个键第二次用就炸在这里
                idempotency_key=None,
            )
            await billing_service.assert_can_afford(
                session, user_id, additional_hourly=as_amount(pending)
            )
        if disk_id_validated is not None:
            from app.modules.orchestrator import disks as disks_service

            await disks_service.attach_for_instance(
                session, user_id, disk_id_validated, instance.id
            )
        if is_service:
            assert service_port is not None  # 上面已拦,这里给类型收敛
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


async def instance_by_id(session: AsyncSession, instance_id: int) -> Instance | None:
    """按主键取实例(不限归属、不限状态)。系统侧巡检用,用户请求一律走 get_instance。"""
    return (
        await session.execute(select(Instance).where(Instance.id == instance_id))
    ).scalar_one_or_none()


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
    items = [InstanceOut.model_validate(i) for i in page_items]
    await attach_instance_details(session, items)
    return Page[InstanceOut](items=items, next_cursor=next_cursor)


async def attach_instance_details(session: AsyncSession, items: "Sequence[InstanceOut]") -> None:
    """回填两个「住在别处」的字段:服务端点 slug 与包周期概要。

    两次批量查询(各自在无相关实例时直接返回),与列表长度无关。
    列表页和详情页共用同一条路径,免得详情少一个字段、前端为它单开一个请求。
    """
    await _attach_service_slugs(session, items)
    await _attach_subscriptions(session, items)


async def instance_view(session: AsyncSession, instance: Instance) -> "InstanceOut":
    """单实例出参:与列表项同形。"""
    from app.modules.orchestrator.schemas import InstanceOut

    items = [InstanceOut.model_validate(instance)]
    await attach_instance_details(session, items)
    return items[0]


async def _attach_service_slugs(session: AsyncSession, items: "Sequence[InstanceOut]") -> None:
    """给列表项回填端点 slug:**一次查询**,不是每行一次。

    列表页要内联「[服务] ep-xxxx」,而 slug 在另一张表。逐行查是 N+1,
    让前端逐行打 /service 是把 N+1 搬到网络上(web.md 明令禁止接口调用随行数放大)。
    """
    ids = [i.id for i in items if i.workload_type == WORKLOAD_SERVICE]
    if not ids:
        return
    # .tuples().all() 而不是直接 dict(session.execute(...)):Result 带 .keys()(列名),
    # dict() 见到 .keys() 就按映射协议对它做下标访问,报的是
    # "'ChunkedIteratorResult' object is not subscriptable" —— 跟真实原因毫无关系。
    # .tuples() 还顺带把行类型收成 tuple[int, str],dict() 的返回类型才推得出来
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
    """给列表项回填包周期概要:**一次查询**,理由同 _attach_service_slugs。

    列表页要在计费列里内联「包月 · 剩 23 天」和续费入口 —— 那要求每行都知道自己的
    到期时刻,而 subscriptions 在另一张表(还在另一个模块)。逐行查是 N+1。
    """
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
    await _require_cluster_for_pool(
        session,
        instance.spec.get("pool_label"),
        instance.gpu_count,
        with_data_disk=instance.data_disk_id is not None,
    )
    if instance.market == MARKET_SUBSCRIPTION:
        # 包周期已预付整段周期,开机不看余额;但周期已过就不能再开
        # (到期链路会停机 → 冻结 → 回收,允许开机等于白送算力)
        await billing_service.assert_subscription_active(session, instance.id)
    else:
        estimate = hourly_cost(instance.price_hourly, instance.gpu_count)
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

    续费同时刷新 `instances.price_hourly` —— 用户可以换周期续(包月转包年),
    有效时价随之变;不刷新的话列表页会一直显示上一个周期的折后价。
    冻结中的实例续费即解冻(回到 stopped,由用户自己开机):
    自动开机要过容量与调度,失败了反而给出「续费成功但机器没起来」的坏体验。
    """
    instance = await get_instance(session, user_id, uuid)
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

    **顺序是这个函数的全部要害**:先把转换前那段按量账结清,再翻 `market`。
    反过来的话,`billing_candidates` 会按翻新后的 market 把这台实例整个排除掉,
    水位线之后还没出账的小时就永远没人结了 —— 用户白拿转换前那段算力。
    结清用的是转换前的按量时价(此刻 `instance.price_hourly` 还没被改),这也是
    「先结后翻」的另一个理由。

    只收 running / stopped 两种状态:creating/starting/stopping/releasing 是在途,
    翻 market 会和收敛路径抢同一行;frozen 是欠费处置中,那笔账得先还清而不是转成预付。
    """
    instance = await get_instance(session, user_id, uuid)
    if idempotency_key:
        # 重放必须最先问:转换成功后 market 已经是 subscription,重放请求会撞上下面
        # 「只有按量实例可以转」那条守卫,拿到一个与真实情况毫不相干的 400;更糟的是
        # 它还会先跑一遍结算,而此刻 price_hourly 已是折后价 —— 等于拿包周期的价格
        # 去补一笔本该按按量收的账
        replayed = await billing_service.find_subscription_replay(
            session, user_id=user_id, key=idempotency_key
        )
        if replayed is not None:
            return (
                instance,
                await billing_service.quote_of_subscription_row(
                    session, replayed, instance.gpu_count
                ),
                False,
            )
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

    await billing_service.lock_wallet(session, user_id)
    if instance.status == sm_def.RUNNING:
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
    endpoint: ServiceEndpoint | None = None,
) -> InstancePodSpec:
    """构造 Pod spec。data_disk_subpath 由调用方从盘记录读出后传入:
    subPath 的唯一事实源是 `data_disks.juicefs_subpath`,就地重算会与擦除路径对不上。
    image_pull_secret 是平台已托管到该 ns 的拉取凭据 Secret 名(core/registry)。
    endpoint 是服务型实例的端点行(service 形态必传,dev 形态恒 None)。

    两形态的差别集中在本函数,不散到 k8s 层:k8s 层只按 spec 字段建对象,
    「dev 有 Jupyter、service 有对外端点」这条业务口径不该在那边再判一次。
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
    # 「这台开没开 SSH」是落库的事实(instances.with_ssh),不从 ssh_port 是否为空反推:
    # 端口是 outbox 建 Pod 时才分配的,创建那一刻两者都是空
    # 不要 SSH 的实例本就不进端口池,ssh_port 恒 None 是正常态,不是漏分配
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
            "JUPYTER_ALLOW_ORIGIN": f"https://{jupyter_host(instance.uuid, settings)}",
        }
        # token 走 per-instance Secret(secretKeyRef),不以明文 env 落 Pod spec:
        # spec 会进 etcd/审计快照,任何 pods:get/list 身份(含只读 SA)都能读走
        secrets_ = {"JUPYTER_TOKEN": _token_plain(instance)}
    # 规格倍率:GPU 实例按卡数放大 CPU/内存(N 卡收 N 倍价,Guaranteed QoS 下 CPU 是硬限,
    # 否则多卡被单份 CPU 饿死、节点侧资源被低估占用);CPU 实例(gpu_count=0)规格就是
    # SKU 本身,倍率恒 1。写 max(1, gpu_count) 结果碰巧一样,但那是「把 0 卡当 1 卡放大」,
    # 语义与这里要表达的「不放大」是两回事。系统盘任何形态都不随卡数放大。
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
        # service 形态不建 Jupyter 路由(k8s 层按 service_port 分叉),这里照常填:
        # 它同时是 SSH 的展示主机名,而 dataclass 上它是必填字段
        jupyter_host=jupyter_host(instance.uuid, settings),
        env=env,
        secret_env=secrets_,
        authorized_keys=tuple(instance.authorized_keys),
        node_selector=gpu_req.node_selector,
        data_disk_subpath=data_disk_subpath,
        scheduler_name=gpu_req.scheduler_name,
        annotations=gpu_req.annotations,
        image_pull_secret=image_pull_secret,
        # 服务容器退出必须原地重启(Always):Never 会让一次崩溃变成实例终结,
        # 而 Pod 重建会换名字,reconciler「Pod 名 = 实例 uuid」的假设整套塌掉
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
        # 一次性入场票据(单次、60s):浏览器打在实例域名的 bootstrap handler 上,
        # 验签核销后 Set-Cookie 第一方会话 cookie 再跳 Jupyter;token 不出现在 URL。
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
        protocol=endpoint.protocol,
        health_path=endpoint.health_path,
        require_api_key=endpoint.require_api_key,
        # 只读 DB:请求路径不碰 K8s。unready_since 由巡检写,是 Pod 就绪的库内投影
        ready=instance.status == sm_def.RUNNING and instance.unready_since is None,
        container_command=list(instance.container_command) if instance.container_command else None,
        container_args=list(instance.container_args) if instance.container_args else None,
        # 密文项只回键名不回值:回了值这个端点就成了「把密文变量读回明文」的入口,
        # 而「勾了密文就不再回显」是创建页对用户的明确承诺
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

    刻意不支持 Idempotency-Key:重放要能回同一份明文,就得把明文留在库里 ——
    与「只存摘要」直接冲突。重复提交最多多出一把可吊销的密钥,代价远小于存明文。
    """
    instance, _ = await _require_service_endpoint(session, user_id, uuid)
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


async def verify_endpoint_key(
    session: AsyncSession, *, slug: str | None, key: str | None
) -> EndpointAuthResult:
    """网关 extAuth 回调的校验链:端点存在 → 实例 running → 密钥有效且属于该端点。

    任一环节不过都抛同一个 401(同码同文案):区分「密钥错」与「端点不存在」等于
    给任意第三方一个枚举平台端点的预言机。
    """
    if not slug:
        raise _endpoint_denied()
    endpoint = (
        await session.execute(select(ServiceEndpoint).where(ServiceEndpoint.public_slug == slug))
    ).scalar_one_or_none()
    if endpoint is None:
        raise _endpoint_denied()
    instance = await session.get(Instance, endpoint.instance_id)
    # 非 running(关机/欠费冻结/释放中)一律拒:Pod 可能还在优雅删除期里活着,
    # 光靠删 HTTPRoute 收口有窗口
    if instance is None or instance.status != sm_def.RUNNING:
        raise _endpoint_denied()
    if not endpoint.require_api_key:
        return EndpointAuthResult(slug=endpoint.public_slug, key_id=None)
    if not key:
        raise _endpoint_denied()
    row = (
        await session.execute(
            select(ServiceApiKey).where(ServiceApiKey.key_hash == hash_api_key(key))
        )
    ).scalar_one_or_none()
    # instance_id 比对是「A 用户的密钥打 B 用户端点」的唯一闸门:密钥是全局唯一的
    # 高熵串,查得到不等于用得上
    if row is None or row.revoked_at is not None or row.instance_id != endpoint.instance_id:
        raise _endpoint_denied()
    # 回源即直写,不节流也不加进程内缓存:网关侧每次调用都回源(extAuth 结果不可缓存),
    # 叠缓存只会把吊销延迟拉长,却换不来什么 —— 这是主键级单行 UPDATE
    row.last_used_at = now_utc()
    await session.commit()
    return EndpointAuthResult(slug=endpoint.public_slug, key_id=row.id)


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

    要减掉包周期预留:软准入减了而这里不减,市场页就会显示「可开 16 台」、点进去建的时候
    409 —— 两个数必须同源,否则用户只能靠试错才知道到底有没有货。
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


async def system_stop(session: AsyncSession, instance: Instance, *, reason: str) -> None:
    """平台侧停机(巡检调用,actor=system)。同事务落事件 + outbox,不 commit。

    reason 由调用方给:欠费是 arrears_stop,包周期到期是 subscription_expired。
    两者在用户时间线上是不同的事,共用一个 reason 会让工单无从查起。
    """
    await transition(session, instance, sm_def.STOPPING, reason=reason, actor="system")
    enqueue(session, "instance.stop", {"instance_id": instance.id})


async def arrears_stop(session: AsyncSession, instance: Instance) -> None:
    """欠费停机。"""
    await system_stop(session, instance, reason="arrears_stop")


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
