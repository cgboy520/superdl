"""在线服务(门面):部署 / 停止 / 启动 / 删除 / 密钥 / 网关鉴权回调。

服务只是实例之上的产品聚合根:计费、配额、回收、reconciler 的主体仍是实例,
本模块的每个生命周期动作都委托 orchestrator.service 的 row 级函数,自己只写 services 行。
锁序 instance → service → disk → wallet:先锁当前实例再锁服务行,与结算 / 迁移链路同向。
"""

import base64
import secrets
import time
from collections.abc import Sequence
from dataclasses import dataclass
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
from app.core.pagination import Page, paginate_by_id
from app.core.sqlutil import like_escape
from app.core.timeutil import now_utc
from app.modules.billing import service as billing_service
from app.modules.orchestrator import service as orchestrator_service
from app.modules.orchestrator.schemas import WORKLOAD_SERVICE, InstanceOut
from app.modules.services.models import DESIRED_RUNNING, DESIRED_STOPPED, Service, ServiceApiKey
from app.modules.services.schemas import (
    AdminServiceOut,
    ServiceContainerOut,
    ServiceCreate,
    ServiceEventOut,
    ServiceOut,
)
from app.modules.services.state import RELEASED, derive_status

if TYPE_CHECKING:
    from app.modules.orchestrator.models import Instance, InstanceEvent
    from app.modules.orchestrator.schemas import InstanceLogsOut
    from app.modules.services.schemas import ServiceSpecIn

logger = get_logger(__name__)

# 单服务活跃密钥上限;密钥行永不删(吊销只写 revoked_at),没有上限即一条无限追加写的口子
MAX_API_KEYS_PER_SERVICE = 20
# 端点公网域名左标签前缀,与 deploy 侧 Gateway listener 的 hostname 通配同一形态
ENDPOINT_SLUG_PREFIX = "svc-"
_SLUG_ATTEMPTS = 3


# ---------- slug 与域名 ----------


def service_url(slug: str) -> str:
    return f"https://{orchestrator_service.service_endpoint_host(slug)}"


def endpoint_slug_from_host(host: str | None) -> str | None:
    """从 Host 头反解服务 slug;不匹配本环境的服务域名后缀一律 None(交调用方拒绝)。"""
    if not host:
        return None
    name = host.split(":")[0].strip().rstrip(".").lower()
    suffix = f".{get_settings().service_domain_suffix.lower()}"
    if not name.endswith(suffix):
        return None
    slug = name[: -len(suffix)]
    # 只收单段左标签 + svc- 前缀:少任一条,同后缀部署下 Jupyter 域名就成鉴权端点的别名
    if "." in slug or not slug.startswith(ENDPOINT_SLUG_PREFIX):
        return None
    return slug


def _new_slug() -> str:
    """svc- + 10 位 base32(约 50 bit 熵)。"""
    raw = base64.b32encode(secrets.token_bytes(7)).decode().lower().rstrip("=")
    return f"{ENDPOINT_SLUG_PREFIX}{raw[:10]}"


async def _insert_service(
    session: AsyncSession, *, user_id: int, name: str, protocol: str, require_api_key: bool
) -> Service:
    """插 services 行,slug 撞 UNIQUE 就换一个重试(最多 3 次)。
    每次插入必须包在 SAVEPOINT 里,否则一次碰撞会把整笔部署事务打成 rollback-only。"""
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


# ---------- 部署 ----------


def _spec_kwargs(spec: "ServiceSpecIn") -> dict[str, object]:
    return {
        "sku_id": spec.sku_id,
        "gpu_count": spec.gpu_count,
        "image_ref": spec.image_ref,
        "ssh_key_ids": spec.ssh_key_ids,
        "data_disk_id": spec.data_disk_id,
        "container_command": spec.container_command,
        "container_args": spec.container_args,
        "env": spec.env,
        "env_secret_keys": spec.env_secret_keys,
        "with_ssh": spec.with_ssh,
        "market": spec.market,
        "period": spec.period,
        "period_count": spec.period_count,
    }


async def create_service(
    session: AsyncSession, user_id: int, *, spec: ServiceCreate, idempotency_key: str | None
) -> tuple[Service, bool]:
    """部署服务(202 异步)。返回 (服务, created);created=False = 幂等重放。

    幂等键落在 instances 行(与订阅行同一惯例):services 没有幂等列,重放经实例反查服务;
    并发同键撞库时 insert_idempotent 已把未提交的 services 行一起回滚,不留孤儿。
    """
    kwargs = _spec_kwargs(spec)
    # 指纹不含 slug / service_id(每次尝试都不同),否则同键重试永远判「异参」
    fingerprint = orchestrator_service.instance_fingerprint(
        user_id,
        workload_type=WORKLOAD_SERVICE,
        name=spec.name,
        service_port=spec.service_port,
        health_path=spec.health_path,
        extra=("service", spec.require_api_key, spec.protocol),
        **kwargs,  # type: ignore[arg-type]
    )
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
        name=spec.name,
        idempotency_key=idempotency_key,
        service=orchestrator_service.ServiceBinding(
            service_id=svc.id,
            revision=1,
            slug=svc.public_slug,
            service_port=spec.service_port,
            health_path=spec.health_path,
        ),
        fingerprint=fingerprint,
        **kwargs,  # type: ignore[arg-type]
    )
    if not created:
        # 并发同键:对方已落库(本事务连同 services 行已回滚),按重放返回对方的服务
        assert instance.service_id is not None
        return await _service_by_id(session, instance.service_id), False
    # 实例名与服务同名(指纹里用的是用户给的 name,生成名在指纹之后再落)
    instance.name = svc.name
    svc.current_instance_id = instance.id
    await session.commit()
    logger.info("service_deploy_accepted", service_id=svc.id, instance_id=instance.id)
    return await _reload(session, svc), True


# ---------- 读 ----------


async def _reload(session: AsyncSession, svc: Service) -> Service:
    """commit 后重读:updated_at 走 server 侧 onupdate、SAVEPOINT 内 flush 的行不带 RETURNING
    回填,过期列在 async 会话里懒加载会直接报 MissingGreenlet,出参前必须显式补齐。"""
    await session.refresh(svc)
    return svc


async def _service_by_id(session: AsyncSession, service_id: int) -> Service:
    return (await session.execute(select(Service).where(Service.id == service_id))).scalar_one()


async def get_service(session: AsyncSession, user_id: int, slug: str) -> Service:
    """用户自己的服务(含已删除:账单与历史仍可查);非属主 404,不暴露存在性。"""
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
    rows = await orchestrator_service.instances_by_ids(session, ids)
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
    """批量出参:两次批量查询(实例、包周期概要),与列表长度无关。"""
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
    """用户端服务列表:降序游标分页,name 同时匹配 slug 前缀;已删除的不列。
    status 是派生值,不进 SQL,在本页范围内过滤(一页最多 MAX_LIMIT 行)。"""
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
    """管理端全局服务列表(不限租户):q 匹配名称与 slug 前缀;默认不列已删除。
    total 只在 user_id 过滤时算(租户抽屉要分得开「正好 N 条」与「被截断」)。"""
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


# ---------- 生命周期 ----------


def _require_live(svc: Service) -> None:
    if svc.released_at is not None:
        raise conflict(key="services.released")
    if svc.rollout_instance_id is not None:
        raise conflict(key="services.rolloutInFlight")


async def _lock_current(session: AsyncSession, svc: Service) -> tuple["Instance", Service]:
    """锁序 instance → service:先锁当前实例(与迁移监听器同向),再锁服务行重读守卫。"""
    if svc.current_instance_id is None:
        raise conflict(key="services.released")
    instance = await orchestrator_service.lock_instance(session, svc.current_instance_id)
    if instance is None:
        raise conflict(key="services.released")
    locked = await session.get(Service, svc.id, with_for_update=True, populate_existing=True)
    assert locked is not None  # 上面刚取到,同事务内不可能消失
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
    """改名 / 鉴权开关:只改 services 行。开关翻转即失效本进程鉴权缓存,跨副本最坏一个 TTL。"""
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
    """删除服务 = 释放当前实例 + 吊销全部密钥;released_at 由迁移监听器在实例 released 时写。
    运行中先停(409):释放实例本就要求已停机,这里把原因说成服务的语言。释放中 / 已删除幂等直回。"""
    svc = await get_service(session, user_id, slug)
    if svc.released_at is not None:
        return svc
    if svc.rollout_instance_id is not None:
        raise conflict(key="services.rolloutInFlight")
    instance, svc = await _lock_current(session, svc)
    if instance.status == orchestrator_service.RUNNING:
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


# ---------- 时间线 / 日志 / 账单 / 版本 ----------


async def list_service_events(
    session: AsyncSession, svc: Service, *, cursor: str | None, limit: int | None
) -> Page[ServiceEventOut]:
    """全部版本实例的事件并集(降序游标分页),标出每条属于哪一版。"""
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
    """当前版本的容器日志;owner / 状态 / 限流 / 超时四道闸在 orchestrator 里。"""
    svc = await get_service(session, user_id, slug)
    shown = svc.rollout_instance_id or svc.current_instance_id
    if shown is None:
        raise conflict(key="orchestrator.logsNeedsRunning")
    rows = await orchestrator_service.instances_by_ids(session, [shown])
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
    """版本历史 = 该服务下全部实例(含已释放),按版本号降序。"""
    return await orchestrator_service.list_instances_page(
        session, user_id, service_id=svc.id, include_released=True, cursor=cursor, limit=limit
    )


# ---------- 访问密钥 ----------


async def list_api_keys(session: AsyncSession, user_id: int, slug: str) -> list[ServiceApiKey]:
    """含已吊销的:吊销记录本身是审计线索。"""
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
    """新建访问密钥。返回 (行, 明文);明文只此一次,库里只有 HMAC 摘要。
    不支持 Idempotency-Key:重放要回同一份明文就得把明文留在库里,与只存摘要冲突。"""
    svc = await get_service(session, user_id, slug)
    if svc.released_at is not None:
        raise conflict(key="services.released")
    # 计数必须串行:FOR UPDATE 锁服务行(服务:密钥 = 1:N),并发建钥在服务行上排队;
    # 否则 count-then-insert 两请求同见 19 把、各插一把越过上限
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
        # 前 11 位(sk- + 8 位):够用户在列表里认出是哪一把,又不足以缩小爆破空间
        key_prefix=plaintext[:11],
    )
    session.add(row)
    await session.commit()
    return row, plaintext


async def revoke_api_key(
    session: AsyncSession, user_id: int, slug: str, key_id: int
) -> ServiceApiKey:
    """吊销:写 revoked_at,不删行。重复吊销幂等(不刷新时刻,首次吊销的时点才是审计事实)。"""
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
        # 鉴权缓存主动失效:本进程即刻拒,跨副本最坏一个 TTL(5s)收敛
        invalidate_endpoint_auth_cache(key_id=row.id)
    return row


# ---------- 网关 extAuth 鉴权(缓存 + 校验链) ----------
# extAuth 回调挂在每个 svc-https 请求的同步路径上:无缓存时每请求 3 次 SELECT +
# 一次 UPDATE+commit(last_used_at),数据面流量直接放大成中央库写压。
# 正向结果进程内缓存 5s:
# - 吊销 / 鉴权开关翻转 / 删除服务:主动失效本进程条目,跨副本最坏一个 TTL 收敛;
# - 停机:RUNNING 迁出经 transition 监听器失效(同进程),跨副本 TTL 兜底;
# - 负结果不缓存:爆破每次回源(主键级 SELECT、无写),新建密钥立即可用。
_ENDPOINT_AUTH_CACHE_TTL_SECONDS = 5.0
_ENDPOINT_AUTH_CACHE_MAX = 4096
_LAST_USED_WRITE_INTERVAL_SECONDS = 60.0


@dataclass(frozen=True)
class EndpointAuthResult:
    """鉴权通过的凭据。key_id 为 None = 该服务不要求 API Key(公开端点)。"""

    slug: str
    key_id: int | None


@dataclass(frozen=True)
class _EndpointAuthCacheEntry:
    result: EndpointAuthResult
    service_id: int
    instance_id: int
    key_id: int | None
    expires_at: float  # time.monotonic 口径


_endpoint_auth_cache: dict[tuple[str, str], _EndpointAuthCacheEntry] = {}
_endpoint_key_last_write: dict[int, float] = {}


def _endpoint_denied() -> AppError:
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
        # key 来自任意外网输入,表必须有界:先清过期,仍满则整表清空(宁可回源不可无界)
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
    """主动失效(吊销按 key、停机按实例、开关 / 删除按服务);表 ≤4096,全扫代价可忽略。"""
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
    """网关 extAuth 回调的校验链:服务存在且未删除 → 当前实例 running → 密钥有效且属于该服务。
    任一环节不过都抛同一个 401(同码同文案),区分开等于给第三方一个枚举平台端点的预言机。"""
    if not slug:
        raise _endpoint_denied()
    # candidates 兼读主密钥轮换世代(见 crypto.py);缓存键取当前世代([0]),稳定
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
    # 非 running 一律拒:Pod 可能还在优雅删除期里活着,光靠删 HTTPRoute 收口有窗口。
    # 主键级读且不抛:拒绝也要 401 而非 500
    if await orchestrator_service.instance_status(session, svc.current_instance_id) != "running":
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
    # service_id 比对是「A 的密钥打 B 的服务」的唯一闸门(含同一用户的另一个服务):
    # 密钥是全局唯一的高熵串,查得到不等于用得上
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


# ---------- 迁移监听器(services 行的唯一非请求写入点) ----------

_listener_registered = False


def register_service_listeners() -> None:
    """跟着实例迁移维护 services 行:RUNNING 迁出即失效鉴权缓存;当前实例 released 即写
    released_at(覆盖用户删除、欠费回收、保留期 GC 全部路径)。幂等注册。
    监听器在 transition 的实例行锁之后才碰 services 行(instance → service),不得再锁另一台实例。"""
    global _listener_registered
    if _listener_registered:
        return

    async def _on_transition(
        session: AsyncSession, instance: "Instance", event: "InstanceEvent"
    ) -> None:
        if instance.service_id is None:
            return
        if event.from_status == orchestrator_service.RUNNING:
            invalidate_endpoint_auth_cache(instance_id=instance.id)
        if event.to_status != RELEASED:
            return
        svc = await session.get(Service, instance.service_id, with_for_update=True)
        if (
            svc is not None
            and svc.released_at is None
            and svc.current_instance_id == instance.id
            and svc.rollout_instance_id is None
        ):
            svc.released_at = event.created_at
            await session.flush()

    orchestrator_service.register_transition_listener(_on_transition)
    _listener_registered = True
