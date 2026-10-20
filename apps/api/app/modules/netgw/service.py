"""Private-network gateways: CRUD, forwards, views and the render input for the apply handler.

Every change bumps `generation` and enqueues netgw.apply in the same transaction; the handler
records `applied_generation`. Secrets are AES-GCM encrypted (AAD bound to the gateway uuid) and
never returned.
"""

import base64
import ipaddress
import json
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from fastapi import status as http_status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt_str, encrypt_str
from app.core.errors import AppError, ErrorCode, conflict, not_found
from app.core.idempotency import (
    IDEMPOTENCY_WINDOW,
    find_replay,
    insert_idempotent,
    request_fingerprint,
)
from app.core.k8s import get_orchestrator
from app.core.k8s.base import NETGW_SOCKS_PORT
from app.core.logging import get_logger
from app.core.outbox import enqueue
from app.core.ratelimit import check_rate_limit
from app.modules.netgw import validation as v
from app.modules.netgw.models import (
    KIND_INSTANCE,
    KIND_INTRANET,
    STATUS_DELETING,
    NetGateway,
    NetGatewayForward,
)
from app.modules.netgw.schemas import (
    AdminGatewayOut,
    DnsOut,
    ForwardIn,
    ForwardOut,
    GatewayIn,
    GatewayOut,
    InstanceForwardIn,
    IpsecIn,
    NetgwConfigOut,
    OpenConnectIn,
    OpenVpnIn,
    TunnelIn,
    WireGuardIn,
)
from app.modules.netgw.spec import RenderInput
from app.modules.orchestrator import queries as orchestrator_queries, statemachine as sm_def
from app.modules.orchestrator.schemas import WORKLOAD_DEV, InstanceLogsOut

logger = get_logger(__name__)

APPLY_TASK = "netgw.apply"
DELETE_TASK = "netgw.delete"
LOGS_MAX_TAIL_LINES = 2000
_SECRET_FIELDS = ("preshared_key", "psk", "config", "password")


def tenant_namespace(user_id: int) -> str:
    return f"{get_settings().k8s_namespace_prefix}{user_id}"


def netgw_config() -> NetgwConfigOut:
    s = get_settings()
    return NetgwConfigOut(
        enabled=bool(s.netgw_image),
        egress_ips=list(s.parsed_netgw_egress_ips()),
        max_gateways=s.netgw_max_per_user,
        max_forwards=s.netgw_max_forwards,
        bandwidth_mbps=s.netgw_bandwidth_mbps,
        socks_port=NETGW_SOCKS_PORT,
    )


def _require_enabled() -> None:
    if not get_settings().netgw_image:
        raise conflict(key="netgw.unavailable")


def _aad(gateway_uuid: str) -> str:
    return f"netgw:{gateway_uuid}"


def decode_secrets(gw: NetGateway) -> dict[str, Any]:
    return json.loads(decrypt_str(gw.secrets_encrypted, aad=_aad(gw.uuid)))


def _encode_secrets(secrets: dict[str, Any], gateway_uuid: str) -> str:
    return encrypt_str(json.dumps(secrets, separators=(",", ":")), aad=_aad(gateway_uuid))


def _wireguard_keypair() -> tuple[str, str]:
    key = X25519PrivateKey.generate()
    raw = serialization.Encoding.Raw
    private = key.private_bytes(raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    public = key.public_key().public_bytes(raw, serialization.PublicFormat.Raw)
    return base64.b64encode(private).decode(), base64.b64encode(public).decode()


def _secret(new: str | None, old: dict[str, Any] | None, key: str, *, required: bool) -> str | None:
    """New value wins; null keeps the stored one; a required secret missing on create is an
    error."""
    value = new if new is not None else (old or {}).get(key)
    if required and not value:
        raise AppError(
            ErrorCode.VALIDATION_ERROR, key="netgw.secretRequired", params={"field": key}
        )
    return value


def _wireguard(
    t: WireGuardIn, remote: list[v.Net], old: dict[str, Any] | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    host, _port = v.split_endpoint(t.endpoint)
    v.check_peer_host(host, remote)
    v.check_wg_key(t.peer_public_key, "peer_public_key")
    psk = _secret(t.preshared_key, old, "preshared_key", required=False)
    if psk:
        v.check_wg_key(psk, "preshared_key")
    public = {
        "endpoint": t.endpoint.strip(),
        "peer_public_key": t.peer_public_key,
        "mtu": t.mtu,
        "has_preshared_key": bool(psk),
    }
    secrets: dict[str, Any] = {"preshared_key": psk}
    if old and old.get("private_key"):
        secrets["private_key"] = old["private_key"]
    return public, secrets


def _ipsec(
    t: IpsecIn, remote: list[v.Net], old: dict[str, Any] | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    v.check_peer_host(t.peer, remote)
    psk = _secret(t.psk, old, "psk", required=True)
    assert psk is not None
    v.check_psk(psk)
    v.check_identity(t.local_id, "local_id")
    v.check_identity(t.remote_id, "remote_id")
    public = {
        "version": t.version,
        "peer": t.peer,
        "local_id": t.local_id,
        "remote_id": t.remote_id,
        "proposals": t.proposals,
    }
    return public, {"psk": psk}


def _openvpn(
    t: OpenVpnIn, remote: list[v.Net], old: dict[str, Any] | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    profile = _secret(t.config, old, "config", required=True)
    assert profile is not None
    remotes = v.check_openvpn_profile(profile, remote)
    v.check_token(t.username, "username")
    password = _secret(t.password, old, "password", required=False) if t.username else None
    v.check_token(password, "password")
    public = {
        "remotes": [f"{h}:{p}/{proto}" for h, p, proto in remotes],
        "username": t.username,
        "has_password": bool(password),
    }
    return public, {"config": profile, "password": password}


def _openconnect(
    t: OpenConnectIn, remote: list[v.Net], old: dict[str, Any] | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    host, port = v.split_server_url(t.server)
    v.check_peer_host(host, remote)
    v.check_tcp_port(port)
    v.check_token(t.username, "username")
    password = _secret(t.password, old, "password", required=True)
    v.check_token(password, "password")
    v.check_servercert(t.servercert)
    v.check_identity(t.authgroup, "authgroup")
    public = {
        "flavor": t.flavor,
        "server": t.server.strip(),
        "username": t.username,
        "servercert": t.servercert,
        "authgroup": t.authgroup,
    }
    return public, {"password": password}


def _tunnel(
    t: TunnelIn, remote: list[v.Net], old: dict[str, Any] | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    if isinstance(t, WireGuardIn):
        return _wireguard(t, remote, old)
    if isinstance(t, IpsecIn):
        return _ipsec(t, remote, old)
    if isinstance(t, OpenVpnIn):
        return _openvpn(t, remote, old)
    return _openconnect(t, remote, old)


def _config(
    body: GatewayIn, old_secrets: dict[str, Any] | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(stored config, secrets) of a validated body."""
    protocol = body.tunnel.protocol
    v.check_name(body.name)
    remote = v.parse_remote_cidrs(body.remote_cidrs)
    v.check_local_address(body.local_address, protocol)
    dns = body.dns
    if dns is not None:
        v.check_dns(list(dns.servers), list(dns.domains), remote)
    public, secrets = _tunnel(body.tunnel, remote, old_secrets)
    config = {
        "remote_cidrs": [str(n) for n in remote],
        "local_address": str(body.local_address) if body.local_address else None,
        "dns": (
            {"servers": [str(x) for x in dns.servers], "domains": list(dns.domains)}
            if dns is not None and dns.servers
            else None
        ),
        "tunnel": public,
    }
    return config, secrets


def _fingerprint(user_id: int, body: GatewayIn) -> str:
    """Parameter fingerprint without secret values (only whether each is present)."""
    data = body.model_dump(mode="json")
    tunnel = data["tunnel"]
    for key in _SECRET_FIELDS:
        if key in tunnel:
            tunnel[key] = tunnel[key] is not None
    return request_fingerprint(user_id, json.dumps(data, sort_keys=True))


async def _name_taken(
    session: AsyncSession, user_id: int, name: str, *, gateway_id: int | None = None
) -> bool:
    gw_stmt = select(NetGateway.id).where(NetGateway.user_id == user_id, NetGateway.name == name)
    if gateway_id is not None:
        gw_stmt = gw_stmt.where(NetGateway.id != gateway_id)
    fw_stmt = select(NetGatewayForward.id).where(
        NetGatewayForward.user_id == user_id, NetGatewayForward.name == name
    )
    return (await session.scalar(gw_stmt)) is not None or (
        await session.scalar(fw_stmt)
    ) is not None


async def _require_name_free(
    session: AsyncSession, user_id: int, name: str, *, gateway_id: int | None = None
) -> None:
    if await _name_taken(session, user_id, name, gateway_id=gateway_id):
        raise conflict(key="netgw.nameTaken", params={"name": name})


def _bump(session: AsyncSession, gw: NetGateway) -> None:
    gw.generation += 1
    enqueue(session, APPLY_TASK, {"gateway_id": gw.id})


async def create_gateway(
    session: AsyncSession, user_id: int, body: GatewayIn, idempotency_key: str | None
) -> tuple[NetGateway, bool]:
    """Validate → quota → insert + netgw.apply in one transaction; (gateway, created)."""
    _require_enabled()
    fingerprint = _fingerprint(user_id, body)
    if idempotency_key:
        existing = await find_replay(
            session,
            NetGateway,
            owner_col=NetGateway.user_id,
            owner_id=user_id,
            key=idempotency_key,
            window=IDEMPOTENCY_WINDOW,
            fingerprint=fingerprint,
        )
        if existing is not None:
            return existing, False
    config, secrets = _config(body, None)
    count = await session.scalar(
        select(func.count()).select_from(NetGateway).where(NetGateway.user_id == user_id)
    )
    limit = get_settings().netgw_max_per_user
    if (count or 0) >= limit:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="netgw.gatewayQuota", params={"max": limit})
    await _require_name_free(session, user_id, body.name)
    gateway_uuid = uuid4().hex
    public_key = None
    if body.tunnel.protocol == "wireguard":
        secrets["private_key"], public_key = _wireguard_keypair()
        config["tunnel"]["public_key"] = public_key
    gw = NetGateway(
        uuid=gateway_uuid,
        user_id=user_id,
        name=body.name,
        protocol=body.tunnel.protocol,
        config=config,
        secrets_encrypted=_encode_secrets(secrets, gateway_uuid),
        public_key=public_key,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
    )
    result = await insert_idempotent(
        session,
        gw,
        model=NetGateway,
        owner_col=NetGateway.user_id,
        owner_id=user_id,
        key=idempotency_key,
        fingerprint=fingerprint,
    )
    if result is not gw:
        return result, False
    enqueue(session, APPLY_TASK, {"gateway_id": gw.id})
    await session.commit()
    await session.refresh(gw)
    logger.info("netgw_created", gateway_uuid=gw.uuid, user_id=user_id, protocol=gw.protocol)
    return gw, True


async def _lock_own(session: AsyncSession, user_id: int | None, uuid: str) -> NetGateway:
    stmt = select(NetGateway).where(NetGateway.uuid == uuid).with_for_update()
    if user_id is not None:
        stmt = stmt.where(NetGateway.user_id == user_id)
    gw = await session.scalar(stmt)
    if gw is None:
        raise not_found(key="netgw.notFound")
    return gw


def _require_active(gw: NetGateway) -> None:
    if gw.status == STATUS_DELETING:
        raise conflict(key="netgw.gatewayDeleting")


async def get_gateway(session: AsyncSession, user_id: int, uuid: str) -> NetGateway:
    gw = await session.scalar(
        select(NetGateway).where(NetGateway.uuid == uuid, NetGateway.user_id == user_id)
    )
    if gw is None:
        raise not_found(key="netgw.notFound")
    return gw


async def list_gateways(session: AsyncSession, user_id: int) -> list[NetGateway]:
    return list(
        (
            await session.scalars(
                select(NetGateway).where(NetGateway.user_id == user_id).order_by(NetGateway.id)
            )
        ).all()
    )


async def update_gateway(
    session: AsyncSession, user_id: int, uuid: str, body: GatewayIn
) -> NetGateway:
    """Replace the configuration; the protocol is fixed, null secrets keep the stored ones."""
    gw = await _lock_own(session, user_id, uuid)
    _require_active(gw)
    if body.tunnel.protocol != gw.protocol:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="netgw.protocolImmutable")
    old = decode_secrets(gw)
    config, secrets = _config(body, old)
    if body.name != gw.name:
        await _require_name_free(session, user_id, body.name, gateway_id=gw.id)
        gw.name = body.name
    if gw.protocol == "wireguard":
        config["tunnel"]["public_key"] = gw.public_key
    remote = [ipaddress.IPv4Network(c) for c in config["remote_cidrs"]]
    for fw in await _forwards_of(session, gw.id):
        if fw.kind == KIND_INTRANET and fw.target_host:
            v.check_intranet_target(fw.target_host, remote)
    gw.config = config
    gw.secrets_encrypted = _encode_secrets(secrets, gw.uuid)
    _bump(session, gw)
    await session.commit()
    await session.refresh(gw)
    return gw


async def _mark_deleting(session: AsyncSession, gw: NetGateway) -> None:
    if gw.status == STATUS_DELETING:
        return
    gw.status = STATUS_DELETING
    enqueue(session, DELETE_TASK, {"gateway_id": gw.id})
    await session.commit()
    await session.refresh(gw)


async def delete_gateway(session: AsyncSession, user_id: int, uuid: str) -> NetGateway:
    gw = await _lock_own(session, user_id, uuid)
    await _mark_deleting(session, gw)
    return gw


async def _forwards_of(session: AsyncSession, gateway_id: int) -> list[NetGatewayForward]:
    return list(
        (
            await session.scalars(
                select(NetGatewayForward)
                .where(NetGatewayForward.gateway_id == gateway_id)
                .order_by(NetGatewayForward.id)
            )
        ).all()
    )


async def _check_instance_forward(
    session: AsyncSession, user_id: int, body: InstanceForwardIn, existing: list[NetGatewayForward]
) -> int:
    v.check_listen_port(body.port)
    if any(f.kind == KIND_INSTANCE and f.port == body.port for f in existing):
        raise conflict(key="netgw.portTaken", params={"port": body.port})
    instance = await orchestrator_queries.get_instance(session, user_id, body.instance_uuid)
    if instance.workload_type != WORKLOAD_DEV or instance.status == sm_def.RELEASED:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="netgw.instanceNotEligible")
    return instance.id


async def add_forward(
    session: AsyncSession, user_id: int, uuid: str, body: ForwardIn
) -> NetGateway:
    gw = await _lock_own(session, user_id, uuid)
    _require_active(gw)
    existing = await _forwards_of(session, gw.id)
    limit = get_settings().netgw_max_forwards
    if len(existing) >= limit:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="netgw.forwardQuota", params={"max": limit})
    v.check_name(body.name)
    await _require_name_free(session, user_id, body.name)
    if isinstance(body, InstanceForwardIn):
        instance_id = await _check_instance_forward(session, user_id, body, existing)
        fw = NetGatewayForward(
            gateway_id=gw.id,
            user_id=user_id,
            kind=KIND_INSTANCE,
            name=body.name,
            port=body.port,
            target_port=body.target_port,
            instance_id=instance_id,
        )
    else:
        remote = [ipaddress.IPv4Network(c) for c in gw.config["remote_cidrs"]]
        v.check_intranet_target(body.target_host, remote)
        fw = NetGatewayForward(
            gateway_id=gw.id,
            user_id=user_id,
            kind=KIND_INTRANET,
            name=body.name,
            port=body.port,
            target_host=body.target_host,
            target_port=body.target_port,
        )
    session.add(fw)
    _bump(session, gw)
    await session.commit()
    await session.refresh(gw)
    return gw


async def delete_forward(
    session: AsyncSession, user_id: int, uuid: str, forward_id: int
) -> NetGateway:
    gw = await _lock_own(session, user_id, uuid)
    _require_active(gw)
    fw = await session.scalar(
        select(NetGatewayForward).where(
            NetGatewayForward.id == forward_id, NetGatewayForward.gateway_id == gw.id
        )
    )
    if fw is None:
        raise not_found(key="netgw.forwardNotFound")
    await session.delete(fw)
    _bump(session, gw)
    await session.commit()
    await session.refresh(gw)
    return gw


def _state(gw: NetGateway) -> str:
    if gw.status == STATUS_DELETING:
        return "deleting"
    if gw.applied_generation < gw.generation:
        return "applying"
    return "connected" if gw.tunnel_up else "disconnected"


def _tunnel_out(gw: NetGateway) -> dict[str, Any]:
    return {"protocol": gw.protocol, **gw.config["tunnel"]}


async def gateway_views(session: AsyncSession, gws: Sequence[NetGateway]) -> list[GatewayOut]:
    """Batch: one forwards query, one instance query."""
    ids = [g.id for g in gws]
    forwards: dict[int, list[NetGatewayForward]] = {i: [] for i in ids}
    if ids:
        for fw in (
            await session.scalars(
                select(NetGatewayForward)
                .where(NetGatewayForward.gateway_id.in_(ids))
                .order_by(NetGatewayForward.id)
            )
        ).all():
            forwards[fw.gateway_id].append(fw)
    instance_ids = {f.instance_id for fs in forwards.values() for f in fs if f.instance_id}
    instances = {
        i.id: i for i in await orchestrator_queries.instances_by_ids(session, instance_ids)
    }
    port = NETGW_SOCKS_PORT
    out: list[GatewayOut] = []
    for gw in gws:
        fws = []
        for f in forwards[gw.id]:
            inst = instances.get(f.instance_id) if f.instance_id else None
            fws.append(
                ForwardOut.model_validate(
                    {
                        "id": f.id,
                        "kind": f.kind,
                        "name": f.name,
                        "port": f.port,
                        "target_host": f.target_host,
                        "instance_uuid": inst.uuid if inst else None,
                        "instance_name": inst.name if inst else None,
                        "target_port": f.target_port,
                        "available": f.kind == KIND_INTRANET
                        or (inst is not None and inst.status != sm_def.RELEASED),
                        "created_at": f.created_at,
                    }
                )
            )
        dns = gw.config.get("dns")
        out.append(
            GatewayOut.model_validate(
                {
                    "uuid": gw.uuid,
                    "name": gw.name,
                    "protocol": gw.protocol,
                    "state": _state(gw),
                    "tunnel_up": gw.tunnel_up,
                    "tunnel_changed_at": gw.tunnel_changed_at,
                    "checked_at": gw.checked_at,
                    "remote_cidrs": gw.config["remote_cidrs"],
                    "local_address": gw.config.get("local_address"),
                    "dns": DnsOut(**dns) if dns else None,
                    "tunnel": _tunnel_out(gw),
                    "socks_url": f"socks5h://{gw.name}:{port}",
                    "forwards": fws,
                    "created_at": gw.created_at,
                    "updated_at": gw.updated_at,
                }
            )
        )
    return out


async def gateway_view(session: AsyncSession, gw: NetGateway) -> GatewayOut:
    return (await gateway_views(session, [gw]))[0]


async def read_gateway_logs(
    session: AsyncSession, user_id: int, uuid: str, *, tail_lines: int
) -> InstanceLogsOut:
    """Gateway container log (read-only, not audited): owner only, 20/h per user."""
    gw = await get_gateway(session, user_id, uuid)
    await check_rate_limit(f"netgw-logs:{user_id}", max_attempts=20, window_seconds=3600.0)
    tail = min(tail_lines, LOGS_MAX_TAIL_LINES)
    try:
        raw = await get_orchestrator().read_netgw_logs(gw.uuid, tail_lines=tail + 1)
    except Exception as exc:
        logger.warning("netgw_logs_read_failed", gateway_uuid=uuid, error=str(exc))
        raise AppError(
            ErrorCode.INTERNAL,
            key="netgw.logsUnavailable",
            http_status=http_status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    lines = raw.splitlines()
    truncated = len(lines) > tail
    return InstanceLogsOut(lines=lines[-tail:] if truncated else lines, truncated=truncated)


async def render_input(session: AsyncSession, gw: NetGateway, *, dns_upstream: str) -> RenderInput:
    """Everything the agent spec needs; instance forwards whose instance is released drop out."""
    forwards = await _forwards_of(session, gw.id)
    ids = [f.instance_id for f in forwards if f.instance_id]
    instances = await orchestrator_queries.instances_by_ids(session, ids)
    return RenderInput(
        gateway=gw,
        secrets=decode_secrets(gw),
        forwards=forwards,
        instance_uuids={i.id: i.uuid for i in instances if i.status != sm_def.RELEASED},
        tenant_namespace=tenant_namespace(gw.user_id),
        dns_upstream=dns_upstream,
    )


async def admin_list_gateways(
    session: AsyncSession,
    *,
    user_id: int | None = None,
    uuid: str | None = None,
    limit: int = 500,
) -> list[AdminGatewayOut]:
    stmt = select(NetGateway).order_by(NetGateway.id.desc()).limit(limit)
    if user_id is not None:
        stmt = stmt.where(NetGateway.user_id == user_id)
    if uuid is not None:
        stmt = stmt.where(NetGateway.uuid == uuid)
    gws = list((await session.scalars(stmt)).all())
    views = await gateway_views(session, gws)
    return [
        AdminGatewayOut(**view.model_dump(), user_id=gw.user_id, pod_phase=gw.pod_phase)
        for gw, view in zip(gws, views, strict=True)
    ]


async def admin_delete_gateway(session: AsyncSession, uuid: str) -> NetGateway:
    gw = await _lock_own(session, None, uuid)
    await _mark_deleting(session, gw)
    return gw
