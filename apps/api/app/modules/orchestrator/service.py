"""Orchestration service: instance operations started by users / admins and Pod spec construction.
Status changes go only through transition(),
changing the DB + touching K8s always goes through the outbox. Read-only queries live in
queries.py, system-side transitions in transitions.py, the port pool in ports.py.
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
from app.core.k8s.base import STARTUP_PROBE_PERIOD_SECONDS, data_disk_pvc_name
from app.core.logging import get_logger
from app.core.money import hourly_cost, money_str
from app.core.outbox import enqueue
from app.core.pagination import Page, RawPage, paginate_by_id
from app.core.platform_config import get_runtime_config
from app.core.pricing import (
    MARKET_ON_DEMAND,
    MARKET_SPOT,
    MARKET_SUBSCRIPTION,
    SubscriptionQuote,
    price_for,
)
from app.core.ratelimit import check_rate_limit
from app.core.registry import is_pinned_image_ref, is_valid_image_ref
from app.core.servercopy import copy as server_copy
from app.core.sqlutil import like_escape
from app.core.timeutil import now_utc
from app.modules.account import service as account_service
from app.modules.billing import service as billing_service
from app.modules.catalog import service as catalog_service
from app.modules.nodes import service as nodes_service
from app.modules.notify import service as notify_service
from app.modules.orchestrator import (
    disks as disks_service,
    preempt as preempt_mod,
    statemachine as sm_def,
)
from app.modules.orchestrator.models import DataDisk, Instance, InstanceEvent
from app.modules.orchestrator.queries import get_instance, lock_instance
from app.modules.orchestrator.schemas import (
    WORKLOAD_DEV,
    WORKLOAD_SERVICE,
    InstanceAccessOut,
    InstanceEventOut,
    InstanceLogsOut,
    InstanceOut,
    InstanceSubscriptionOut,
)
from app.modules.orchestrator.transitions import transition

if TYPE_CHECKING:
    from app.modules.catalog.models import Sku
    from app.modules.nodes.models import NodeSpec

logger = get_logger(__name__)


@dataclass(frozen=True)
class ServiceBinding:
    """Online-service revision identity bound at instance creation (only once the service row is
    stored); port and health path live in InstanceRequest."""

    service_id: int
    revision: int
    slug: str


@dataclass(frozen=True)
class InstanceRequest:
    """All order parameters of one instance creation (shared by dev boxes and service revisions);
    the
    parameter-mismatch fingerprint derives from this object.
    A non-null service_port = service form (the image must pin a version, SSH follows with_ssh);
    dev boxes always have SSH."""

    sku_id: int
    gpu_count: int
    image_ref: str
    ssh_key_ids: tuple[int, ...]
    name: str | None = None
    data_disk_id: int | None = None
    container_command: tuple[str, ...] | None = None
    container_args: tuple[str, ...] | None = None
    env: dict[str, str] | None = None
    env_secret_keys: tuple[str, ...] | None = None
    with_ssh: bool = True
    market: str = MARKET_ON_DEMAND
    period: str | None = None
    period_count: int = 1
    service_port: int | None = None
    health_path: str | None = None

    @property
    def is_service(self) -> bool:
        return self.service_port is not None

    @property
    def workload_type(self) -> str:
        return WORKLOAD_SERVICE if self.is_service else WORKLOAD_DEV

    @property
    def wants_ssh(self) -> bool:
        """dev form always has SSH; service form follows the user's choice."""
        return self.with_ssh if self.is_service else True

    def fingerprint(self, user_id: int, *, extra: tuple[object, ...] = ()) -> str:
        """Parameter-mismatch fingerprint: sha256 over the full order parameters; service
        deployments
        merge service-level attributes via extra (without slug / id)."""
        return request_fingerprint(
            user_id,
            self.sku_id,
            self.gpu_count,
            self.image_ref,
            sorted(self.ssh_key_ids),
            self.name,
            self.data_disk_id,
            self.workload_type,
            self.container_command,
            self.container_args,
            sorted(self.env.items()) if self.env else None,
            sorted(self.env_secret_keys) if self.env_secret_keys else None,
            self.service_port,
            self.health_path,
            self.with_ssh,
            self.market,
            self.period,
            self.period_count,
            *extra,
        )


def jupyter_host(instance_uuid: str, settings: Settings | None = None) -> str:
    """Instance Jupyter hostname: <jupyter_host_prefix><uuid>.<jupyter_domain_suffix>."""
    s = settings or get_settings()
    return f"{s.jupyter_host_prefix}{instance_uuid}.{s.jupyter_domain_suffix}"


def jupyter_origin(instance_uuid: str, settings: Settings | None = None) -> str:
    """Browser origin of the instance's Jupyter (with port); the HTTPRoute hostname and the SSH
    connection string use jupyter_host."""
    s = settings or get_settings()
    host = jupyter_host(instance_uuid, s)
    return (
        f"https://{host}" if s.jupyter_url_port == 443 else f"https://{host}:{s.jupyter_url_port}"
    )


def service_endpoint_host(slug: str, settings: Settings | None = None) -> str:
    """Service endpoint hostname: <slug>.<service_domain_suffix>."""
    s = settings or get_settings()
    return f"{slug}.{s.service_domain_suffix}"


def _snapshot_spec(sku: "Sku") -> dict[str, Any]:
    return {
        "sku_name": sku.name,
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
        "gpu_model_selector": canonical_gpu_model(sku.gpu_model),
    }


async def _require_cluster_for_pool(
    session: AsyncSession,
    pool_label: str | None,
    gpu_count: int,
    *,
    with_data_disk: bool = False,
) -> None:
    """Missing cluster capability → 409; gpu_count == 0 skips the pool check.

    hami checks HAMi, kata the RuntimeClass; the StorageClass is always checked, data disks on
    demand.
    """
    if gpu_count > 0:
        if pool_label == POOL_HAMI:
            await nodes_service.require_hami_ready(session)
        elif pool_label == POOL_KATA:
            await nodes_service.require_kata_runtimeclass(session)
    await nodes_service.require_storage_classes(session, with_data_disk=with_data_disk)


def _encode_token(plaintext: str, *, instance_uuid: str) -> str:
    """Stored form: AES-GCM ciphertext, AAD bound to the instance uuid."""
    return encrypt_str(plaintext, aad=f"jupyter-token:{instance_uuid}")


def _token_plain(instance: Instance) -> str:
    """Read the plaintext (see _encode_token)."""
    return decrypt_str(instance.jupyter_token, aad=f"jupyter-token:{instance.uuid}")


def _env_aad(instance_uuid: str) -> str:
    return f"instance-env:{instance_uuid}"


def _encode_env(env: dict[str, str], secret_keys: set[str], *, instance_uuid: str) -> str:
    """Stored form of user environment variables: AES-GCM ciphertext of the JSON bundle of plaintext
    and secret entries, AAD bound to the instance uuid."""
    payload = {
        "plain": {k: v for k, v in env.items() if k not in secret_keys},
        "secret": {k: v for k, v in env.items() if k in secret_keys},
    }
    return encrypt_str(json.dumps(payload, ensure_ascii=False), aad=_env_aad(instance_uuid))


def instance_env(instance: Instance) -> tuple[dict[str, str], dict[str, str]]:
    """Read the user environment variables, returning (plaintext entries, secret entries). Two empty
    dicts when unset."""
    if not instance.env_encrypted:
        return {}, {}
    data = json.loads(decrypt_str(instance.env_encrypted, aad=_env_aad(instance.uuid)))
    return dict(data.get("plain") or {}), dict(data.get("secret") or {})


def _new_jupyter_ticket(instance: Instance, token_plain: str) -> str:
    """One-off entry ticket: code (single use) + TTL + HMAC signature (key = the Jupyter token
    itself).
    The in-image bootstrap handler verifies, redeems and sets the cookie; a token rotation voids
    every old ticket.
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
    """Image reference validation: shape (core.registry.is_valid_image_ref) → source allow-list (the
    single gate;
    an empty image_allowed_registries = unrestricted, the Harbor address and the platform image
    catalog always pass).
    """
    if not is_valid_image_ref(image_ref):
        raise AppError(ErrorCode.VALIDATION_ERROR, key="orchestrator.imageRefInvalid")
    if require_pinned and not is_pinned_image_ref(image_ref):
        raise AppError(ErrorCode.VALIDATION_ERROR, key="orchestrator.imageRefNotPinned")
    allowed = (await get_runtime_config(session)).image_allowlist()
    if not allowed:
        return
    if any(image_ref.startswith(prefix) for prefix in allowed):
        return
    if await catalog_service.is_catalog_image(session, image_ref):
        return
    raise AppError(
        ErrorCode.VALIDATION_ERROR,
        key="orchestrator.imageRefNotAllowed",
        params={"registries": ", ".join(allowed)},
    )


async def _check_user_quota(
    session: AsyncSession,
    user_id: int,
    new_gpus: int,
    new_vcpus: int,
    *,
    exclude_instance_id: int | None = None,
) -> None:
    """Per-user quotas (instance count / total GPUs / CPU-instance vCPUs), effective values via
    account.get_user_limits.
    GPU instances count the GPU dimension only, CPU instances the vCPU dimension only;
    exclude_instance_id (the old instance about to be replaced) takes no slot.
    """
    limits = await account_service.get_user_limits(session, user_id)
    policies = await get_runtime_config(session)
    stmt = select(
        func.count(),
        func.coalesce(func.sum(Instance.gpu_count), 0),
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


def _sku_free_capacity(
    sku: "Sku", specs: list["NodeSpec"], *, gpu_node_vcpu_cap: int
) -> tuple[int | None, int]:
    """Approximate allocatable capacity of the SKU: (inventory sentinel, sellable instances);
    sentinel None = no inventory data, let the scheduler decide.
    GPU tiers match Ready idle cards by (pool, canonical model); the CPU tier matches node rows by
    pool only.
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
    """Creation soft admission: insufficient allocatable capacity in the inventory → preempt spot
    instances first, still short → 409; no data always passes.
    Preemption applies only to non-spot GPU-tier requests; freeing_slots = slots the same request
    is about to free, added back to the sellable count first.
    """
    policies = await get_runtime_config(session)
    specs = await nodes_service.list_node_specs(session)
    matching_free, sellable = _sku_free_capacity(
        sku, specs, gpu_node_vcpu_cap=policies.gpu_node_cpu_instance_vcpu_cap
    )
    if matching_free is None:
        return
    sellable -= (await _reserved_slots_by_sku(session, [sku.id])).get(sku.id, 0)
    sellable += freeing_slots
    needed = gpu_count if gpu_count > 0 else 1
    if (
        sellable < needed
        and market != MARKET_SPOT
        and sku.tier != TIER_CPU
        and await preempt_mod.try_free_capacity(
            session,
            sku=sku,
            deficit_slots=needed - sellable,
            slots_per_card=catalog_service.sellable_per_gpu(
                sku.pool_label, sku.gpu_cores_pct, sku.oversell_cores
            ),
            grace_seconds=policies.spot_grace_seconds,
            requested_by=user_id or 0,
        )
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
    """sku_id → slots held by unexpired subscription instances (stopped / frozen included), same SKU
    only.
    Reserved at the platform level, not physically.
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


async def create_instance_row(
    session: AsyncSession,
    user_id: int,
    req: InstanceRequest,
    *,
    idempotency_key: str | None,
    service: ServiceBinding | None = None,
    exclude_instance_id: int | None = None,
    fingerprint: str | None = None,
) -> tuple[Instance, bool]:
    """Row-level core of instance creation: validation → soft admission → quotas and balance under
    the wallet row lock → write instances / event / outbox,
    **no commit**. Returns (instance, created), created=False = idempotent replay.
    service set = one revision of an online service (req.service_port must be set too);
    market='subscription' writes subscriptions and charges once in the same transaction (no
    overdraft), then passes the in-flight burn-rate check;
    exclude_instance_id's share goes to the new instance (quotas and soft admission), the balance
    does not;
    a caller-supplied fingerprint must be req.fingerprint(...) of the same req.
    """
    if (service is not None) != req.is_service:
        raise ValueError("service binding and req.service_port must be both set or both unset")
    if fingerprint is None:
        fingerprint = req.fingerprint(user_id)
    if idempotency_key:
        existing = await find_instance_replay(
            session, user_id, key=idempotency_key, fingerprint=fingerprint
        )
        if existing is not None:
            return existing, False

    sku = await catalog_service.get_on_sale_sku(session, req.sku_id)
    await _validate_request(session, req, sku)
    await _admit(session, user_id, req, sku, exclude_instance_id=exclude_instance_id)
    policies = await get_runtime_config(session)
    unit_price = price_for(
        sku.price_hourly, market=req.market, policies=policies, period=req.period
    )
    disk_id = await _lock_disk(session, user_id, req.data_disk_id)
    authorized_keys = await _reserve_funds_and_quota(
        session, user_id, req, sku, unit_price, exclude_instance_id=exclude_instance_id
    )
    instance = _build_row(
        user_id,
        req,
        sku,
        unit_price=unit_price,
        authorized_keys=authorized_keys,
        disk_id=disk_id,
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
        service=service,
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
        return result, False
    await _post_insert(session, user_id, req, sku, instance, service=service)
    return instance, True


async def _validate_request(session: AsyncSession, req: InstanceRequest, sku: "Sku") -> None:
    """Spec pairings the contract layer cannot catch: card count vs SKU form, image shape and
    source,
    purchase mode vs SKU switches."""
    if sku.max_gpus_per_instance == 0:
        if req.gpu_count != 0:
            raise AppError(ErrorCode.VALIDATION_ERROR, key="orchestrator.cpuSkuNoGpu")
    elif not 1 <= req.gpu_count <= sku.max_gpus_per_instance:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="orchestrator.gpuCountRange",
            params={"max": sku.max_gpus_per_instance},
        )
    await _validate_image_ref(session, req.image_ref, require_pinned=req.is_service)
    if req.market == MARKET_SPOT and not sku.spot_enabled:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="orchestrator.spotNotEnabled")
    if req.market == MARKET_SUBSCRIPTION and not sku.period_enabled:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="orchestrator.periodNotEnabled")


async def _admit(
    session: AsyncSession,
    user_id: int,
    req: InstanceRequest,
    sku: "Sku",
    *,
    exclude_instance_id: int | None,
) -> None:
    """Dispatch gate (cluster capability) + capacity soft admission (preempt spot instances first
    when
    short, same transaction as the creation)."""
    await _require_cluster_for_pool(
        session, sku.pool_label, req.gpu_count, with_data_disk=req.data_disk_id is not None
    )
    await _soft_admit_capacity(
        session,
        sku,
        req.gpu_count,
        market=req.market,
        user_id=user_id,
        freeing_slots=await _freeing_slots_of(session, exclude_instance_id, sku),
    )


async def _lock_disk(session: AsyncSession, user_id: int, data_disk_id: int | None) -> int | None:
    """Lock the disk row before mounting (lock order disk → wallet, same direction as the delete /
    expand chains); attach re-enters this lock after the insert in the same transaction."""
    if data_disk_id is None:
        return None
    return (await disks_service.lock_disk_for_attach(session, user_id, data_disk_id)).id


async def _reserve_funds_and_quota(
    session: AsyncSession,
    user_id: int,
    req: InstanceRequest,
    sku: "Sku",
    unit_price: Decimal,
    *,
    exclude_instance_id: int | None,
) -> list[str]:
    """Critical section: FOR UPDATE on the wallet row held until commit; in-flight statistics,
    quotas
    and key validation all inside the lock.
    Balance definition: in flight + creating/starting pending burn (merged in by
    assert_can_afford) + this addition. Returns the keys to hand down."""
    await billing_service.lock_wallet(session, user_id)
    if req.market != MARKET_SUBSCRIPTION:
        await billing_service.assert_can_afford(
            session, user_id, additional_hourly=hourly_cost(unit_price, req.gpu_count)
        )
    await _check_user_quota(
        session,
        user_id,
        req.gpu_count,
        sku.vcpu if req.gpu_count == 0 else 0,
        exclude_instance_id=exclude_instance_id,
    )
    if not req.wants_ssh:
        return []
    keys = await account_service.ssh_keys_by_ids(session, user_id, list(req.ssh_key_ids))
    selected = [k.public_key for k in keys]
    if not selected:
        raise AppError(ErrorCode.SSH_KEY_INVALID, key="orchestrator.sshKeyRequired")
    return selected


def _build_row(
    user_id: int,
    req: InstanceRequest,
    sku: "Sku",
    *,
    unit_price: Decimal,
    authorized_keys: list[str],
    disk_id: int | None,
    idempotency_key: str | None,
    fingerprint: str,
    service: ServiceBinding | None,
) -> Instance:
    """instances row (creating); the Jupyter token and user env ciphertext are stored,
    the service-form snapshot columns come from req and service."""
    instance_uuid = uuid4().hex
    return Instance(
        uuid=instance_uuid,
        user_id=user_id,
        name=req.name or f"instance-{uuid4().hex[:6]}",
        sku_id=sku.id,
        spec=_snapshot_spec(sku),
        price_hourly=unit_price,
        gpu_count=req.gpu_count,
        market=req.market,
        image_ref=req.image_ref,
        status=sm_def.CREATING,
        k8s_namespace=f"{get_settings().k8s_namespace_prefix}{user_id}",
        jupyter_token=_encode_token(secrets.token_urlsafe(24), instance_uuid=instance_uuid),
        authorized_keys=authorized_keys,
        data_disk_id=disk_id,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        workload_type=req.workload_type,
        container_command=list(req.container_command) if req.container_command else None,
        container_args=list(req.container_args) if req.container_args else None,
        with_ssh=req.wants_ssh,
        env_encrypted=(
            _encode_env(req.env, set(req.env_secret_keys or ()), instance_uuid=instance_uuid)
            if req.env
            else None
        ),
        service_id=service.service_id if service else None,
        service_revision=service.revision if service else None,
        service_slug=service.slug if service else None,
        service_port=req.service_port,
        health_path=req.health_path,
    )


async def _post_insert(
    session: AsyncSession,
    user_id: int,
    req: InstanceRequest,
    sku: "Sku",
    instance: Instance,
    *,
    service: ServiceBinding | None,
) -> None:
    """Same-transaction wrap-up after the row insert: subscription prepayment, data-disk occupation,
    creation event, outbox."""
    if req.market == MARKET_SUBSCRIPTION:
        assert req.period is not None
        await billing_service.charge_new_subscription(
            session,
            user_id=user_id,
            instance_id=instance.id,
            instance_name=instance.name,
            sku_id=sku.id,
            base_hourly=sku.price_hourly,
            gpu_count=req.gpu_count,
            period=req.period,
            period_count=req.period_count,
            idempotency_key=None,
        )
        await billing_service.assert_can_afford(session, user_id)
    if instance.data_disk_id is not None:
        await disks_service.attach_for_instance(
            session, user_id, instance.data_disk_id, instance.id
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
                "gpu_count": req.gpu_count,
                "workload_type": req.workload_type,
                "market": req.market,
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


async def find_instance_replay(
    session: AsyncSession, user_id: int, *, key: str, fingerprint: str
) -> Instance | None:
    """Existing instance for the same (user, Idempotency-Key) within the 24 h window; same key with
    different params → 409."""
    return await find_replay(
        session,
        Instance,
        owner_col=Instance.user_id,
        owner_id=user_id,
        key=key,
        window=IDEMPOTENCY_WINDOW,
        fingerprint=fingerprint,
    )


async def instances_of_service(session: AsyncSession, service_id: int) -> list[Instance]:
    """Every revision instance of an online service (released included), revision descending."""
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
    """Sellable share held by the old instance about to be replaced: only when running in the same
    pool with the same canonical model."""
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
    session: AsyncSession, user_id: int, req: InstanceRequest, *, idempotency_key: str | None
) -> tuple[Instance, bool]:
    """Create and commit a dev-box request, returning (instance, created); created=False on an
    idempotent replay."""
    instance, created = await create_instance_row(
        session, user_id, req, idempotency_key=idempotency_key
    )
    if created:
        await session.commit()
        logger.info("instance_create_accepted", instance_id=instance.id, user_id=user_id)
    return instance, created


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
    """User instance list: cursor pagination by instance ID descending, status exact / name fuzzy
    (uuid prefix included).
    Dev boxes only by default; with service_id the revision instances of that service,
    include_released adds released ones.
    """
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
    """Back-fill subscription summaries in batch."""
    await _attach_subscriptions(session, items)


async def list_expiring_instances(
    session: AsyncSession, user_id: int, *, within_days: int
) -> "list[InstanceOut]":
    """Expiring subscription instances: active subscription with expires_at ≤ now+within_days, by
    expiry ascending, no pagination."""
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
    """Single-instance output, same shape as a list item."""
    items = [InstanceOut.model_validate(instance)]
    await attach_instance_details(session, items)
    return items[0]


async def _attach_subscriptions(session: AsyncSession, items: "Sequence[InstanceOut]") -> None:
    """Back-fill the subscription summary of list items: one query."""
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
    """Union of several instances' events: descending cursor-paginated ORM rows (for the
    service-level timeline)."""
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
    """Instance event timeline: descending cursor pagination."""
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


def _reject_service_instance(instance: Instance) -> None:
    """Revision instances of a service reject instance-level lifecycle operations (stop / start /
    restart / release / token reset),
    driven by /services instead; renewal / conversion and read-only endpoints work as usual."""
    if instance.service_id is not None:
        raise conflict(key="orchestrator.serviceInstanceLifecycle")


LIFECYCLE_MAX_PER_HOUR = 60


async def check_lifecycle_rate_limit(user_id: int) -> None:
    """Shared bucket of start / stop / restart (instances and online services alike):
    LIFECYCLE_MAX_PER_HOUR per user per hour."""
    await check_rate_limit(
        f"instance-lifecycle:{user_id}", max_attempts=LIFECYCLE_MAX_PER_HOUR, window_seconds=3600.0
    )


async def stop_instance_row(
    session: AsyncSession, instance: Instance, *, reason: str = "user_stop", actor: str = "user"
) -> Instance:
    """Row-level core of stop: running guard → stopping + outbox, **no commit**."""
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
    """(Re-)occupy the data disk: lock the disk and validate the mount via attach_for_instance when
    it exists, drop the mount point when deleted."""
    if instance.data_disk_id is None:
        return
    disk = await session.get(DataDisk, instance.data_disk_id, with_for_update=True)
    if disk is None or disk.status == "deleted":
        instance.data_disk_id = None
        await session.flush()
        return

    await disks_service.attach_for_instance(session, instance.user_id, disk.id, instance.id)


async def lock_instance_row(session: AsyncSession, instance: Instance) -> Instance:
    """FOR UPDATE on an already fetched instance row and re-read (it cannot vanish within the
    transaction)."""
    locked = await lock_instance(session, instance.id)
    assert locked is not None
    return locked


async def start_instance_row(session: AsyncSession, user_id: int, instance: Instance) -> Instance:
    """Row-level core of start: lock the instance → frozen / status / node / cluster / subscription
    /
    data disk / balance gates in turn
    → starting + outbox, **no commit**."""
    instance = await lock_instance_row(session, instance)
    if instance.status == sm_def.FROZEN:
        raise AppError(ErrorCode.INSTANCE_FROZEN, key="orchestrator.frozenNeedsRecharge")
    recovered = instance.status == sm_def.FAILED
    if instance.status != sm_def.STOPPED and not recovered:
        raise AppError(ErrorCode.INSTANCE_INVALID_TRANSITION, key="orchestrator.startNeedsStopped")
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
        await billing_service.assert_subscription_active(session, instance.id)
    if recovered:
        await transition(session, instance, sm_def.STOPPED, reason="failed_recover", actor="user")
    await _rebind_data_disk(session, instance)
    if instance.market != MARKET_SUBSCRIPTION:
        estimate = hourly_cost(instance.price_hourly, instance.gpu_count)
        await billing_service.assert_can_afford(session, user_id, additional_hourly=estimate)
    await transition(session, instance, sm_def.STARTING, reason="user_start", actor="user")
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
) -> tuple[Instance, SubscriptionQuote, bool]:
    """Renew a subscription instance. Returns (instance, quote, created); created=False = idempotent
    replay.
    Renewing with a different period refreshes `instances.price_hourly`; renewing while frozen
    unfreezes back to stopped.
    """
    instance = await get_instance(session, user_id, uuid)
    instance = await lock_instance_row(session, instance)
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
        return await get_instance(session, user_id, uuid), quoted, False
    policies = await get_runtime_config(session)
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
) -> tuple[Instance, SubscriptionQuote, bool]:
    """Convert an on-demand instance to a subscription. Returns (instance, quote, created);
    created=False = idempotent replay.
    The on-demand bill is settled at the pre-conversion price first, then `market` flips; running /
    stopped only.
    """
    instance = await get_instance(session, user_id, uuid)
    if idempotency_key:
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
    instance = await lock_instance_row(session, instance)
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
    policies = await get_runtime_config(session)
    instance.market = MARKET_SUBSCRIPTION
    instance.price_hourly = price_for(
        row.unit_price, market=MARKET_SUBSCRIPTION, policies=policies, period=period
    )
    await billing_service.assert_can_afford(session, user_id)
    await session.commit()
    logger.info("instance_converted_to_subscription", instance_id=instance.id, period=period)
    return instance, quoted, True


async def convert_to_on_demand(session: AsyncSession, user_id: int, uuid: str) -> Instance:
    """Convert a spot instance to on-demand; already on-demand returns unchanged. Only `market`
    flips, the Pod is untouched;
    the current clock hour is settled entirely at the on-demand price
    (billing.reprice_current_hour), the list price comes from `spec.base_price_hourly`.
    """
    instance = await get_instance(session, user_id, uuid)
    if instance.market == MARKET_ON_DEMAND:
        return instance
    instance = await lock_instance_row(session, instance)
    if instance.market != MARKET_SPOT:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="orchestrator.toOnDemandNotSpot")
    if instance.status not in (sm_def.RUNNING, sm_def.STOPPED):
        raise AppError(
            ErrorCode.INSTANCE_INVALID_TRANSITION,
            key="orchestrator.convertNeedsRunningOrStopped",
            http_status=http_status.HTTP_409_CONFLICT,
        )
    base = Decimal(str(instance.spec.get("base_price_hourly") or instance.price_hourly))
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
    await billing_service.assert_can_afford(session, user_id)
    await session.commit()
    logger.info("spot_converted_to_on_demand", instance_id=instance.id, user_id=user_id)
    return instance


async def set_instance_auto_renew(
    session: AsyncSession, user_id: int, uuid: str, *, enabled: bool
) -> Instance:
    """Toggle auto-renewal."""
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
    """Row-level core of release: status guard → releasing + outbox, **no commit**; releasing /
    released return idempotently."""
    if instance.status in (sm_def.RELEASING, sm_def.RELEASED):
        return instance
    if instance.status not in (
        sm_def.STOPPED,
        sm_def.FROZEN,
        sm_def.FAILED,
        sm_def.CREATING,
        sm_def.STOPPING,
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


def bandwidth_annotations(settings: Settings) -> dict[str, str]:
    """Rate-limit annotation understood by the CNI bandwidth plugin (k3s flannel and Cilium
    bandwidthManager alike); 0 = none."""
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
    data_disk_pvc: str | None = None,
    image_pull_secret: str | None = None,
) -> InstancePodSpec:
    """Build the Pod spec. data_disk_pvc is computed by the caller from the disk uuid
    (data_disk_pvc_name);
    image_pull_secret is the pull-credential Secret name in that ns; the service form reads the
    snapshot columns of the instance row only.
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
    if instance.with_ssh and instance.ssh_port is None:
        raise RuntimeError("build_pod_spec requires allocated ssh_port")
    plain_env, secret_env = instance_env(instance)
    if is_service:
        env = plain_env
        secrets_ = secret_env
    else:
        env = {
            "JUPYTER_ALLOW_ORIGIN": jupyter_origin(instance.uuid, settings),
        }
        secrets_ = {"JUPYTER_TOKEN": _token_plain(instance)}
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
        jupyter_host=jupyter_host(instance.uuid, settings),
        env=env,
        secret_env=secrets_,
        authorized_keys=tuple(instance.authorized_keys),
        node_selector=gpu_req.node_selector,
        data_disk_pvc=data_disk_pvc,
        scheduler_name=gpu_req.scheduler_name,
        annotations={**gpu_req.annotations, **bandwidth_annotations(settings)},
        image_pull_secret=image_pull_secret,
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
        startup_failure_threshold=startup_failure_threshold(settings.creating_timeout_seconds),
    )


def startup_failure_threshold(creating_timeout_seconds: int) -> int:
    """startupProbe failure threshold: total (threshold × period) below the platform creating
    timeout, at least 3."""
    return max(3, creating_timeout_seconds // STARTUP_PROBE_PERIOD_SECONDS - 1)


async def build_pod_spec_with_cluster(
    session: AsyncSession, instance: Instance, *, image_pull_secret: str | None = None
) -> InstancePodSpec:
    """For outbox handlers: with the cluster distribution context and the data-disk subPath (read
    from the disk record)."""
    row = await nodes_service.get_cluster_status(session)
    disk_pvc: str | None = None
    if instance.data_disk_id is not None:
        disk = await session.get(DataDisk, instance.data_disk_id)
        if disk is None:
            raise RuntimeError(f"data disk {instance.data_disk_id} missing for {instance.uuid}")
        disk_pvc = data_disk_pvc_name(disk.uuid)
    return build_pod_spec(
        instance,
        distro=row.distro if row else None,
        data_disk_pvc=disk_pvc,
        image_pull_secret=image_pull_secret,
    )


def build_access(instance: Instance) -> InstanceAccessOut:
    """Access information: fields by form, missing entries left empty."""
    settings = get_settings()
    if instance.status != sm_def.RUNNING:
        raise AppError(ErrorCode.INSTANCE_INVALID_TRANSITION, key="orchestrator.accessNeedsRunning")
    out = InstanceAccessOut()
    if instance.with_ssh:
        out.ssh_host = jupyter_host(instance.uuid, settings)
        out.ssh_port = instance.ssh_port
        out.ssh_command = f"ssh root@{out.ssh_host} -p {instance.ssh_port}"
    if instance.workload_type == WORKLOAD_DEV:
        out.jupyter_url = _new_jupyter_ticket(instance, _token_plain(instance))
    if instance.service_slug:
        out.endpoint_url = f"https://{service_endpoint_host(instance.service_slug, settings)}"
    return out


async def get_access(session: AsyncSession, user_id: int, uuid: str) -> InstanceAccessOut:
    """Fetch the instance (owner check) and assemble the access information."""
    return build_access(await get_instance(session, user_id, uuid))


async def strip_ssh_key_from_instances(session: AsyncSession, user_id: int, public_key: str) -> int:
    """Remove the key from the authorized_keys snapshot of every unreleased instance of the user,
    returning the instance count;
    running Pods pick it up on their next rebuild."""
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
    if instance.status == sm_def.RUNNING:
        await transition(session, instance, sm_def.STOPPING, reason="restart", actor="user")
        enqueue(session, "instance.restart", {"instance_id": instance.id})
    await session.commit()
    return instance


LOGS_MAX_TAIL_LINES = 2000


async def read_instance_logs(
    session: AsyncSession, user_id: int, uuid: str, *, tail_lines: int
) -> "InstanceLogsOut":
    """Read the instance container log (read-only, not audited): non-owner 404; running/stopping
    only, otherwise 409;
    tail_lines truncated at the cap."""
    instance = await get_instance(session, user_id, uuid)
    if instance.status not in (sm_def.RUNNING, sm_def.STOPPING):
        raise conflict(key="orchestrator.logsNeedsRunning")
    await check_rate_limit(f"instance-logs:{user_id}", max_attempts=20, window_seconds=3600.0)
    tail = min(tail_lines, LOGS_MAX_TAIL_LINES)
    try:
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


async def estimate_available_many(session: AsyncSession, skus: list["Sku"]) -> dict[int, int]:
    """Approximate market stock (batch): sku_id → sellable instances. Source is the node inventory,
    no data → 0;
    subscription reservations subtracted (same source as soft admission)."""
    specs = await nodes_service.list_node_specs(session)
    cap = (await get_runtime_config(session)).gpu_node_cpu_instance_vcpu_cap
    reserved = await _reserved_slots_by_sku(session, [s.id for s in skus])
    return {
        sku.id: max(
            0, _sku_free_capacity(sku, specs, gpu_node_vcpu_cap=cap)[1] - reserved.get(sku.id, 0)
        )
        for sku in skus
    }


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
    """Admin instance list (cursor pagination, descending): q by instance name or uuid prefix,
    node_name exact."""
    stmt = select(Instance).order_by(Instance.id.desc())
    if status_filter:
        stmt = stmt.where(Instance.status == status_filter)
    if user_id:
        stmt = stmt.where(Instance.user_id == user_id)
    if node_name:
        stmt = stmt.where(Instance.node_name == node_name)
    q = (q or "").strip()
    if q:
        stmt = stmt.where(
            Instance.uuid.like(f"{like_escape(q)}%", escape="\\")
            | Instance.name.ilike(f"%{like_escape(q)}%", escape="\\")
        )
    page_items, next_cursor = await paginate_by_id(
        session, stmt, id_col=Instance.id, cursor=cursor, limit=limit
    )
    return RawPage(items=page_items, next_cursor=next_cursor)


async def admin_get_instance(session: AsyncSession, instance_uuid: str) -> Instance:
    """Admin fetch by uuid (any tenant); missing → 404."""
    instance = (
        await session.execute(select(Instance).where(Instance.uuid == instance_uuid))
    ).scalar_one_or_none()
    if instance is None:
        raise not_found(key="orchestrator.instanceNotFound")
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
        title=server_copy("orchestrator.admin_force_stop.title"),
        content=server_copy(
            "orchestrator.admin_force_stop.content", name=instance.name, reason=reason
        ),
        severity="warning",
        target_id=instance.uuid,
    )
    await session.commit()
    return instance


async def admin_preempt(session: AsyncSession, instance_uuid: str, *, reason: str) -> Instance:
    """Admin force-reclaim of one spot instance, through the same reclamation path as automatic
    preemption (reason differs from admin_force_stop)."""
    instance = await admin_get_instance(session, instance_uuid)
    if instance.market != MARKET_SPOT:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="orchestrator.preemptNotSpot")
    if instance.status != sm_def.RUNNING:
        raise AppError(
            ErrorCode.INSTANCE_INVALID_TRANSITION, key="orchestrator.forceStopNeedsRunning"
        )
    policies = await get_runtime_config(session)
    await preempt_mod.preempt(
        session,
        [instance],
        grace_seconds=policies.spot_grace_seconds,
        requested_by=0,
        admin_reason=reason,
    )
    await session.commit()
    return instance
