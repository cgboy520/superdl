"""Online service lifecycle, access keys and gateway auth; lock order instance → service → disk →
wallet."""

import base64
import secrets
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from fastapi import status as http_status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import hash_api_key, hash_api_key_candidates
from app.core.errors import AppError, ErrorCode, conflict, not_found
from app.core.logging import get_logger
from app.core.metrics import ENDPOINT_AUTH_DENIED_TOTAL
from app.core.outbox import enqueue
from app.core.pagination import Page, paginate_by_id
from app.core.pricing import MARKET_SUBSCRIPTION
from app.core.servercopy import copy as server_copy
from app.core.sqlutil import like_escape
from app.core.timeutil import now_utc
from app.modules.billing import service as billing_service
from app.modules.notify import service as notify_service
from app.modules.orchestrator import (
    queries as orchestrator_queries,
    service as orchestrator_service,
    statemachine as sm_def,
    transitions as orchestrator_transitions,
)
from app.modules.orchestrator.schemas import InstanceOut
from app.modules.orchestrator.service import InstanceRequest, ServiceBinding
from app.modules.services.models import DESIRED_RUNNING, DESIRED_STOPPED, Service, ServiceApiKey
from app.modules.services.schemas import (
    AdminServiceOut,
    ServiceContainerOut,
    ServiceCreate,
    ServiceEventOut,
    ServiceOut,
    ServiceRevisionCreate,
)
from app.modules.services.state import RELEASED, derive_status

if TYPE_CHECKING:
    from app.modules.orchestrator.models import Instance, InstanceEvent
    from app.modules.orchestrator.schemas import InstanceLogsOut
    from app.modules.services.schemas import ServiceSpecIn

logger = get_logger(__name__)

MAX_API_KEYS_PER_SERVICE = 20
ENDPOINT_SLUG_PREFIX = "svc-"
_SLUG_ATTEMPTS = 3


def service_url(slug: str) -> str:
    return f"https://{orchestrator_service.service_endpoint_host(slug)}"


def endpoint_slug_from_host(host: str | None) -> str | None:
    """Resolve the service slug from the Host header; None when it does not match this
    environment's service domain suffix."""
    if not host:
        return None
    name = host.split(":")[0].strip().rstrip(".").lower()
    suffix = f".{get_settings().service_domain_suffix.lower()}"
    if not name.endswith(suffix):
        return None
    slug = name[: -len(suffix)]
    if "." in slug or not slug.startswith(ENDPOINT_SLUG_PREFIX):
        return None
    return slug


def _new_slug() -> str:
    """svc- + 10 base32 characters (about 50 bits of entropy)."""
    raw = base64.b32encode(secrets.token_bytes(7)).decode().lower().rstrip("=")
    return f"{ENDPOINT_SLUG_PREFIX}{raw[:10]}"


async def _insert_service(
    session: AsyncSession, *, user_id: int, name: str, protocol: str, require_api_key: bool
) -> Service:
    """Insert the services row; on a slug UNIQUE collision pick another and retry (at most 3 times);
    each insert is wrapped in a SAVEPOINT."""
    for attempt in range(_SLUG_ATTEMPTS):
        svc = Service(
            public_slug=_new_slug(),
            user_id=user_id,
            name=name,
            protocol=protocol,
            require_api_key=require_api_key,
            desired_state=DESIRED_RUNNING,
        )
        try:
            async with session.begin_nested():
                session.add(svc)
                await session.flush()
        except IntegrityError:
            if attempt == _SLUG_ATTEMPTS - 1:
                raise
            logger.warning("service_slug_collision", user_id=user_id)
            continue
        return svc
    raise AssertionError("unreachable")  # pragma: no cover


def _instance_request(spec: "ServiceSpecIn", *, name: str | None) -> InstanceRequest:
    """Service form → instance creation request (a non-null service_port means service form)."""
    return InstanceRequest(
        sku_id=spec.sku_id,
        gpu_count=spec.gpu_count,
        image_ref=spec.image_ref,
        ssh_key_ids=tuple(spec.ssh_key_ids),
        name=name,
        data_disk_id=spec.data_disk_id,
        container_command=tuple(spec.container_command) if spec.container_command else None,
        container_args=tuple(spec.container_args) if spec.container_args else None,
        env=spec.env,
        env_secret_keys=tuple(spec.env_secret_keys) if spec.env_secret_keys else None,
        with_ssh=spec.with_ssh,
        market=spec.market,
        period=spec.period,
        period_count=spec.period_count,
        service_port=spec.service_port,
        health_path=spec.health_path,
    )


async def create_service(
    session: AsyncSession, user_id: int, *, spec: ServiceCreate, idempotency_key: str | None
) -> tuple[Service, bool]:
    """Submit the service and instance deployment request, returning (service, created); a replay by
    the instance idempotency key returns created=False."""
    req = _instance_request(spec, name=spec.name)
    fingerprint = req.fingerprint(user_id, extra=("service", spec.require_api_key, spec.protocol))
    if idempotency_key:
        existing = await orchestrator_service.find_instance_replay(
            session, user_id, key=idempotency_key, fingerprint=fingerprint
        )
        if existing is not None and existing.service_id is not None:
            return await _service_by_id(session, existing.service_id), False

    svc = await _insert_service(
        session,
        user_id=user_id,
        name=spec.name or f"service-{uuid4().hex[:6]}",
        protocol=spec.protocol,
        require_api_key=spec.require_api_key,
    )
    instance, created = await orchestrator_service.create_instance_row(
        session,
        user_id,
        req,
        idempotency_key=idempotency_key,
        service=ServiceBinding(service_id=svc.id, revision=1, slug=svc.public_slug),
        fingerprint=fingerprint,
    )
    if not created:
        assert instance.service_id is not None
        return await _service_by_id(session, instance.service_id), False
    instance.name = svc.name
    svc.current_instance_id = instance.id
    await session.commit()
    logger.info("service_deploy_accepted", service_id=svc.id, instance_id=instance.id)
    return await _reload(session, svc), True


RETIRE_TASK_TYPE = "service.retire"
_ROLLOUT_SETTLED = (
    sm_def.RUNNING,
    sm_def.STOPPED,
    sm_def.FAILED,
)


async def create_revision(
    session: AsyncSession,
    user_id: int,
    slug: str,
    *,
    spec: ServiceRevisionCreate,
    idempotency_key: str | None,
) -> tuple[Service, bool]:
    """Revision update (recreate): the new revision instance lands in creating, a running old
    revision stops; once the new revision is running the transition
    listener flips current and enqueues service.retire. slug / URL / API keys do not change.
    Returns (service, created); created=False = idempotent replay.
    Quotas and soft admission hand the old revision's share to the new one, the balance is not
    handed over; subscriptions are always 409.
    """
    svc = await get_service(session, user_id, slug)
    req = _instance_request(spec, name=svc.name)
    fingerprint = req.fingerprint(
        user_id, extra=("revision", svc.id, tuple(sorted(spec.env_secret_keep)))
    )
    if idempotency_key:
        existing = await orchestrator_service.find_instance_replay(
            session, user_id, key=idempotency_key, fingerprint=fingerprint
        )
        if existing is not None and existing.service_id == svc.id:
            return svc, False
    _require_live(svc)
    if spec.market == MARKET_SUBSCRIPTION:
        raise conflict(key="services.rolloutSubscriptionUnsupported")
    old, svc = await _lock_current(session, svc)
    if old.market == MARKET_SUBSCRIPTION:
        raise conflict(key="services.rolloutSubscriptionUnsupported")
    if old.status not in _ROLLOUT_SETTLED:
        raise conflict(key="services.rolloutNeedsSettled")

    env: dict[str, str] = dict(spec.env or {})
    secret_keys = set(spec.env_secret_keys or ())
    if spec.env_secret_keep:
        _plain, old_secret = orchestrator_service.instance_env(old)
        unknown = sorted(k for k in spec.env_secret_keep if k not in old_secret)
        if unknown:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                key="services.envKeepUnknown",
                params={"keys": ", ".join(unknown)},
            )
        for key in spec.env_secret_keep:
            if key not in env:
                env[key] = old_secret[key]
                secret_keys.add(key)
    req = replace(
        req, env=env or None, env_secret_keys=tuple(sorted(secret_keys)) if secret_keys else None
    )

    new, created = await orchestrator_service.create_instance_row(
        session,
        user_id,
        req,
        idempotency_key=idempotency_key,
        service=ServiceBinding(service_id=svc.id, revision=svc.revision + 1, slug=svc.public_slug),
        exclude_instance_id=old.id,
        fingerprint=fingerprint,
    )
    if not created:
        return svc, False
    svc.revision += 1
    svc.rollout_instance_id = new.id
    svc.desired_state = DESIRED_RUNNING
    if old.status == sm_def.RUNNING:
        await orchestrator_service.stop_instance_row(session, old, reason="rollout")
    await session.commit()
    logger.info(
        "service_rollout_accepted",
        service_id=svc.id,
        revision=svc.revision,
        old_instance_id=old.id,
        new_instance_id=new.id,
    )
    return await _reload(session, svc), True


async def _reload(session: AsyncSession, svc: Service) -> Service:
    """Refresh the service row and its database-generated fields."""
    await session.refresh(svc)
    return svc


async def _service_by_id(session: AsyncSession, service_id: int) -> Service:
    return (await session.execute(select(Service).where(Service.id == service_id))).scalar_one()


async def get_service(session: AsyncSession, user_id: int, slug: str) -> Service:
    """The user's own service (deleted included); non-owner 404."""
    svc = (
        await session.execute(
            select(Service).where(Service.public_slug == slug, Service.user_id == user_id)
        )
    ).scalar_one_or_none()
    if svc is None:
        raise not_found(key="services.notFound")
    return svc


async def _instances_of(
    session: AsyncSession, services: Sequence[Service]
) -> dict[int, "Instance"]:
    ids = {i for s in services for i in (s.current_instance_id, s.rollout_instance_id) if i}
    rows = await orchestrator_queries.instances_by_ids(session, ids)
    return {r.id: r for r in rows}


def _container_of(instance: "Instance") -> ServiceContainerOut:
    plain_env, secret_env = orchestrator_service.instance_env(instance)
    return ServiceContainerOut(
        image_ref=instance.image_ref,
        container_command=list(instance.container_command) if instance.container_command else None,
        container_args=list(instance.container_args) if instance.container_args else None,
        env=plain_env,
        env_secret_keys=sorted(secret_env),
        service_port=instance.service_port,
        health_path=instance.health_path,
        with_ssh=instance.with_ssh,
    )


async def build_views(
    session: AsyncSession,
    services: Sequence[Service],
    *,
    instances: dict[int, "Instance"] | None = None,
) -> list[ServiceOut]:
    """Assemble service status, instance details and container config in batch; secret environment
    variables return the key name only."""
    by_id = instances if instances is not None else await _instances_of(session, services)
    instance_outs = [InstanceOut.model_validate(i) for i in by_id.values()]
    await orchestrator_service.attach_instance_details(session, instance_outs)
    outs_by_id = {o.id: o for o in instance_outs}
    views: list[ServiceOut] = []
    for svc in services:
        current = by_id.get(svc.current_instance_id) if svc.current_instance_id else None
        rollout = by_id.get(svc.rollout_instance_id) if svc.rollout_instance_id else None
        status, ready = derive_status(svc, current, rollout)
        shown = rollout or current
        views.append(
            ServiceOut(
                id=svc.id,
                slug=svc.public_slug,
                name=svc.name,
                url=service_url(svc.public_slug),
                protocol=svc.protocol,
                require_api_key=svc.require_api_key,
                desired_state=svc.desired_state,
                status=status,
                ready=ready,
                revision=svc.revision,
                current_instance=outs_by_id.get(current.id) if current else None,
                rollout_instance=outs_by_id.get(rollout.id) if rollout else None,
                container=_container_of(shown) if shown else None,
                created_at=svc.created_at,
                updated_at=svc.updated_at,
                released_at=svc.released_at,
            )
        )
    return views


async def service_view(session: AsyncSession, svc: Service) -> ServiceOut:
    return (await build_views(session, [svc]))[0]


async def list_services_page(
    session: AsyncSession,
    user_id: int,
    *,
    status: str | None = None,
    name: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> Page[ServiceOut]:
    """User service list: descending cursor pagination, name also matches the slug prefix; deleted
    services are not listed.
    status is derived and filtered within the page."""
    stmt = (
        select(Service)
        .where(Service.user_id == user_id, Service.released_at.is_(None))
        .order_by(Service.id.desc())
    )
    name = (name or "").strip()
    if name:
        stmt = stmt.where(
            Service.name.ilike(f"%{like_escape(name)}%", escape="\\")
            | Service.public_slug.like(f"{like_escape(name)}%", escape="\\")
        )
    page_items, next_cursor = await paginate_by_id(
        session, stmt, id_col=Service.id, cursor=cursor, limit=limit
    )
    views = await build_views(session, page_items)
    if status:
        views = [v for v in views if v.status == status]
    return Page[ServiceOut](items=views, next_cursor=next_cursor)


async def admin_list_services_page(
    session: AsyncSession,
    *,
    user_id: int | None = None,
    q: str | None = None,
    include_released: bool = False,
    cursor: str | None = None,
    limit: int | None = None,
) -> Page[AdminServiceOut]:
    """Admin global service list: q matches name and slug prefix; deleted services are not listed by
    default; total is computed only when filtering by user_id."""
    stmt = select(Service).order_by(Service.id.desc())
    if user_id:
        stmt = stmt.where(Service.user_id == user_id)
    if not include_released:
        stmt = stmt.where(Service.released_at.is_(None))
    q = (q or "").strip()
    if q:
        stmt = stmt.where(
            Service.name.ilike(f"%{like_escape(q)}%", escape="\\")
            | Service.public_slug.like(f"{like_escape(q)}%", escape="\\")
        )
    page_items, next_cursor = await paginate_by_id(
        session, stmt, id_col=Service.id, cursor=cursor, limit=limit
    )
    by_id = await _instances_of(session, page_items)
    views = await build_views(session, page_items, instances=by_id)
    items: list[AdminServiceOut] = []
    for svc, view in zip(page_items, views, strict=True):
        current = by_id.get(svc.current_instance_id) if svc.current_instance_id else None
        items.append(
            AdminServiceOut(
                **view.model_dump(),
                user_id=svc.user_id,
                node_name=current.node_name if current else None,
            )
        )
    total: int | None = None
    if user_id:
        total = await session.scalar(
            select(func.count()).select_from(stmt.order_by(None).subquery())
        )
    return Page[AdminServiceOut](items=items, next_cursor=next_cursor, total=total)


def _require_live(svc: Service) -> None:
    if svc.released_at is not None:
        raise conflict(key="services.released")
    if svc.rollout_instance_id is not None:
        raise conflict(key="services.rolloutInFlight")


async def _lock_current(session: AsyncSession, svc: Service) -> tuple["Instance", Service]:
    """Lock order instance → service: lock the current instance first, then the service row and
    re-read the guards."""
    if svc.current_instance_id is None:
        raise conflict(key="services.released")
    instance = await orchestrator_queries.lock_instance(session, svc.current_instance_id)
    if instance is None:
        raise conflict(key="services.released")
    locked = await session.get(Service, svc.id, with_for_update=True, populate_existing=True)
    assert locked is not None
    _require_live(locked)
    return instance, locked


async def patch_service(
    session: AsyncSession,
    user_id: int,
    slug: str,
    *,
    name: str | None,
    require_api_key: bool | None,
) -> Service:
    """Rename / auth switch: only the services row changes; flipping the switch invalidates this
    process's auth cache."""
    svc = await get_service(session, user_id, slug)
    if name is not None:
        svc.name = name
    if require_api_key is not None and require_api_key != svc.require_api_key:
        svc.require_api_key = require_api_key
        invalidate_endpoint_auth_cache(service_id=svc.id)
    await session.commit()
    return await _reload(session, svc)


async def stop_service(session: AsyncSession, user_id: int, slug: str) -> Service:
    svc = await get_service(session, user_id, slug)
    _require_live(svc)
    instance, svc = await _lock_current(session, svc)
    await orchestrator_service.stop_instance_row(session, instance)
    svc.desired_state = DESIRED_STOPPED
    await session.commit()
    return await _reload(session, svc)


async def start_service(session: AsyncSession, user_id: int, slug: str) -> Service:
    svc = await get_service(session, user_id, slug)
    _require_live(svc)
    instance, svc = await _lock_current(session, svc)
    await orchestrator_service.start_instance_row(session, user_id, instance)
    svc.desired_state = DESIRED_RUNNING
    await session.commit()
    return await _reload(session, svc)


async def delete_service(session: AsyncSession, user_id: int, slug: str) -> Service:
    """Delete the service = release the current instance + revoke every key; released_at is written
    by the transition listener.
    409 while running; releasing / deleted return idempotently."""
    svc = await get_service(session, user_id, slug)
    if svc.released_at is not None:
        return svc
    if svc.rollout_instance_id is not None:
        raise conflict(key="services.rolloutInFlight")
    instance, svc = await _lock_current(session, svc)
    if instance.status == sm_def.RUNNING:
        raise AppError(
            ErrorCode.INSTANCE_NOT_STOPPED,
            key="services.deleteNeedsStopped",
            http_status=http_status.HTTP_409_CONFLICT,
        )
    await orchestrator_service.release_instance_row(session, instance, actor="user")
    await session.execute(
        update(ServiceApiKey)
        .where(ServiceApiKey.service_id == svc.id, ServiceApiKey.revoked_at.is_(None))
        .values(revoked_at=now_utc())
    )
    invalidate_endpoint_auth_cache(service_id=svc.id)
    await session.commit()
    return await _reload(session, svc)


async def list_service_events(
    session: AsyncSession, svc: Service, *, cursor: str | None, limit: int | None
) -> Page[ServiceEventOut]:
    """Union of every revision instance's events (descending cursor pagination), marked with the
    revision."""
    rows = await orchestrator_service.instances_of_service(session, svc.id)
    by_id = {r.id: r for r in rows}
    raw = await orchestrator_service.list_events_raw(
        session, list(by_id), cursor=cursor, limit=limit
    )
    items = []
    for e in raw.items:
        inst = by_id[e.instance_id]
        items.append(
            ServiceEventOut(
                **_event_fields(e), instance_uuid=inst.uuid, revision=inst.service_revision
            )
        )
    return Page[ServiceEventOut](items=items, next_cursor=raw.next_cursor)


def _event_fields(e: "InstanceEvent") -> dict[str, Any]:
    return {
        "id": e.id,
        "from_status": e.from_status,
        "to_status": e.to_status,
        "reason": e.reason,
        "actor": e.actor,
        "event_metadata": e.event_metadata,
        "created_at": e.created_at,
    }


async def read_service_logs(
    session: AsyncSession, user_id: int, slug: str, *, tail_lines: int
) -> "InstanceLogsOut":
    """Read the rollout revision's container log, the current revision without a rollout; validation
    is delegated to the orchestrator service."""
    svc = await get_service(session, user_id, slug)
    shown = svc.rollout_instance_id or svc.current_instance_id
    if shown is None:
        raise conflict(key="orchestrator.logsNeedsRunning")
    rows = await orchestrator_queries.instances_by_ids(session, [shown])
    if not rows:
        raise conflict(key="orchestrator.logsNeedsRunning")
    return await orchestrator_service.read_instance_logs(
        session, user_id, rows[0].uuid, tail_lines=tail_lines
    )


async def service_bills_page(
    session: AsyncSession, user_id: int, svc: Service, *, cursor: str | None, limit: int | None
):
    rows = await orchestrator_service.instances_of_service(session, svc.id)
    return await billing_service.hourly_bills_page(
        session, user_id, instance_ids=[r.id for r in rows], cursor=cursor, limit=limit
    )


async def list_revisions(
    session: AsyncSession, user_id: int, svc: Service, *, cursor: str | None, limit: int | None
):
    """Paginated revision instances of the service, released instances included."""
    return await orchestrator_service.list_instances_page(
        session, user_id, service_id=svc.id, include_released=True, cursor=cursor, limit=limit
    )


async def list_api_keys(session: AsyncSession, user_id: int, slug: str) -> list[ServiceApiKey]:
    """Key list, revoked included."""
    svc = await get_service(session, user_id, slug)
    return list(
        (
            await session.execute(
                select(ServiceApiKey)
                .where(ServiceApiKey.service_id == svc.id)
                .order_by(ServiceApiKey.id.desc())
            )
        ).scalars()
    )


async def create_api_key(
    session: AsyncSession, user_id: int, slug: str, *, name: str
) -> tuple[ServiceApiKey, str]:
    """Under the service row lock check the active key quota, create the key and commit, returning
    (row, plaintext).

    The plaintext is returned once; the database stores only the HMAC digest and prefix; no
    idempotency key.
    """
    svc = await get_service(session, user_id, slug)
    if svc.released_at is not None:
        raise conflict(key="services.released")
    await session.execute(select(Service.id).where(Service.id == svc.id).with_for_update())
    live = (
        await session.execute(
            select(func.count())
            .select_from(ServiceApiKey)
            .where(ServiceApiKey.service_id == svc.id, ServiceApiKey.revoked_at.is_(None))
        )
    ).scalar_one()
    if live >= MAX_API_KEYS_PER_SERVICE:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="services.apiKeyQuota",
            params={"max": MAX_API_KEYS_PER_SERVICE},
        )
    plaintext = f"sk-{secrets.token_urlsafe(32)}"
    row = ServiceApiKey(
        user_id=user_id,
        service_id=svc.id,
        name=name,
        key_hash=hash_api_key(plaintext),
        key_prefix=plaintext[:11],
    )
    session.add(row)
    await session.commit()
    return row, plaintext


async def revoke_api_key(
    session: AsyncSession, user_id: int, slug: str, key_id: int
) -> ServiceApiKey:
    """Revoke: write revoked_at, keep the row; repeated revocation is idempotent (the timestamp is
    not refreshed)."""
    svc = await get_service(session, user_id, slug)
    row = (
        await session.execute(
            select(ServiceApiKey).where(
                ServiceApiKey.id == key_id, ServiceApiKey.service_id == svc.id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise not_found(key="services.apiKeyNotFound")
    if row.revoked_at is None:
        row.revoked_at = now_utc()
        await session.commit()
        invalidate_endpoint_auth_cache(key_id=row.id)
    return row


_ENDPOINT_AUTH_CACHE_TTL_SECONDS = 5.0
_ENDPOINT_AUTH_CACHE_MAX = 4096
_LAST_USED_WRITE_INTERVAL_SECONDS = 60.0


@dataclass(frozen=True)
class EndpointAuthResult:
    """Credentials that passed auth; key_id None = public endpoint."""

    slug: str
    key_id: int | None


@dataclass(frozen=True)
class _EndpointAuthCacheEntry:
    result: EndpointAuthResult
    service_id: int
    instance_id: int
    key_id: int | None
    expires_at: float


_endpoint_auth_cache: dict[tuple[str, str], _EndpointAuthCacheEntry] = {}
_endpoint_key_last_write: dict[int, float] = {}


def _endpoint_denied() -> AppError:
    ENDPOINT_AUTH_DENIED_TOTAL.inc()
    return AppError(
        ErrorCode.API_KEY_INVALID,
        key="services.apiKeyInvalid",
        http_status=http_status.HTTP_401_UNAUTHORIZED,
    )


def _cache_get(slug: str, key_hash: str) -> _EndpointAuthCacheEntry | None:
    entry = _endpoint_auth_cache.get((slug, key_hash))
    if entry is None or entry.expires_at <= time.monotonic():
        return None
    return entry


def _cache_put(slug: str, key_hash: str, entry: _EndpointAuthCacheEntry) -> None:
    cache = _endpoint_auth_cache
    if len(cache) >= _ENDPOINT_AUTH_CACHE_MAX:
        now = time.monotonic()
        for k in [k for k, v in cache.items() if v.expires_at <= now]:
            del cache[k]
        if len(cache) >= _ENDPOINT_AUTH_CACHE_MAX:
            cache.clear()
    cache[(slug, key_hash)] = entry


def invalidate_endpoint_auth_cache(
    *,
    key_id: int | None = None,
    instance_id: int | None = None,
    service_id: int | None = None,
) -> None:
    """Active invalidation (revocation by key, shutdown by instance, switch / deletion by
    service)."""
    doomed = [
        k
        for k, v in _endpoint_auth_cache.items()
        if (key_id is not None and v.key_id == key_id)
        or (instance_id is not None and v.instance_id == instance_id)
        or (service_id is not None and v.service_id == service_id)
    ]
    for k in doomed:
        del _endpoint_auth_cache[k]


def clear_endpoint_auth_cache() -> None:
    """Clear the in-process auth cache and the last-used write throttle records."""
    _endpoint_auth_cache.clear()
    _endpoint_key_last_write.clear()


async def _touch_key_last_used(session: AsyncSession, key_id: int | None) -> None:
    """Throttled direct write of last_used_at: at most one UPDATE+commit per key per process per
    60 s."""
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
    """Check the service is not deleted, the current instance is running and the key the service
    requires; public services need no key, every failure is 401.

    Successful results are cached for _ENDPOINT_AUTH_CACHE_TTL_SECONDS on the monotonic clock,
    failures are not cached;
    active invalidation on revocation and status changes affects this process only, other replicas
    may serve the cache until it expires.
    """
    if not slug:
        raise _endpoint_denied()
    key_hashes = hash_api_key_candidates(key) if key else []
    key_hash = key_hashes[0] if key_hashes else ""
    cached = _cache_get(slug, key_hash)
    if cached is not None:
        await _touch_key_last_used(session, cached.key_id)
        return cached.result
    svc = (
        await session.execute(
            select(Service).where(Service.public_slug == slug, Service.released_at.is_(None))
        )
    ).scalar_one_or_none()
    if svc is None or svc.current_instance_id is None:
        raise _endpoint_denied()
    if await orchestrator_queries.instance_status(session, svc.current_instance_id) != "running":
        raise _endpoint_denied()
    expires = time.monotonic() + _ENDPOINT_AUTH_CACHE_TTL_SECONDS
    if not svc.require_api_key:
        result = EndpointAuthResult(slug=svc.public_slug, key_id=None)
        _cache_put(
            slug,
            key_hash,
            _EndpointAuthCacheEntry(result, svc.id, svc.current_instance_id, None, expires),
        )
        return result
    if not key:
        raise _endpoint_denied()
    row = (
        await session.execute(
            select(ServiceApiKey).where(ServiceApiKey.key_hash.in_(key_hashes)).limit(1)
        )
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None or row.service_id != svc.id:
        raise _endpoint_denied()
    result = EndpointAuthResult(slug=svc.public_slug, key_id=row.id)
    _cache_put(
        slug,
        key_hash,
        _EndpointAuthCacheEntry(result, svc.id, svc.current_instance_id, row.id, expires),
    )
    await _touch_key_last_used(session, row.id)
    return result


_listener_registered = False


def register_service_listeners() -> None:
    """Maintain the services row alongside instance transitions: leaving RUNNING invalidates the
    auth cache; a candidate revision running flips current
    and enqueues the release of the old revision; a candidate revision failed clears the candidate
    and notifies; the current instance released writes released_at. Idempotent registration.
    Lock order instance → service, never lock another instance."""
    global _listener_registered
    if _listener_registered:
        return

    async def _on_transition(
        session: AsyncSession, instance: "Instance", event: "InstanceEvent"
    ) -> None:
        if instance.service_id is None:
            return
        if event.from_status == sm_def.RUNNING:
            invalidate_endpoint_auth_cache(instance_id=instance.id)
        to = event.to_status
        if to not in (
            sm_def.RUNNING,
            sm_def.FAILED,
            RELEASED,
        ):
            return
        svc = await session.get(Service, instance.service_id, with_for_update=True)
        if svc is None:
            return
        if to == sm_def.RUNNING and svc.rollout_instance_id == instance.id:
            old_id = svc.current_instance_id
            svc.current_instance_id = instance.id
            svc.rollout_instance_id = None
            invalidate_endpoint_auth_cache(service_id=svc.id)
            if old_id is not None and old_id != instance.id:
                enqueue(session, RETIRE_TASK_TYPE, {"service_id": svc.id, "instance_id": old_id})
            await session.flush()
        elif to == sm_def.FAILED and svc.rollout_instance_id == instance.id:
            svc.rollout_instance_id = None
            await notify_service.notify(
                session,
                svc.user_id,
                type_="service",
                title=server_copy("services.rollout_failed.title"),
                content=server_copy(
                    "services.rollout_failed.content",
                    name=svc.name,
                    revision=instance.service_revision,
                ),
                severity="warning",
                dedup_key=f"rollout_failed:{instance.id}",
                target_id=svc.public_slug,
            )
            await session.flush()
        elif (
            to == RELEASED
            and svc.released_at is None
            and svc.current_instance_id == instance.id
            and svc.rollout_instance_id is None
        ):
            svc.released_at = event.created_at
            await session.flush()

    orchestrator_transitions.register_transition_listener(_on_transition)
    _listener_registered = True
