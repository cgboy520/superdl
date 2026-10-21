"""Private-network gateways: CRUD, forwards, L3 attachments, views, the render input for the apply
handler and the instance side of an attachment for the instance Pod.

Every change bumps `generation` and enqueues netgw.apply in the same transaction; the handler
records `applied_generation`. Secrets are AES-GCM encrypted (AAD bound to the gateway uuid, or to
the instance uuid for attachment keys) and never returned.
"""

import base64
import ipaddress
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from fastapi import status as http_status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt_str, encrypt_str
from app.core.errors import AppError, ErrorCode, conflict, not_found
from app.core.gpu_adapter import spec_to_gpu_request
from app.core.idempotency import (
    IDEMPOTENCY_WINDOW,
    find_replay,
    insert_idempotent,
    request_fingerprint,
)
from app.core.k8s import get_orchestrator
from app.core.k8s.base import NETGW_SOCKS_PORT, NetgwAttach, netgw_hub_host
from app.core.logging import get_logger
from app.core.outbox import enqueue
from app.core.ratelimit import check_rate_limit
from app.core.timeutil import now_utc
from app.modules.netgw import validation as v
from app.modules.netgw.models import (
    KIND_INSTANCE,
    KIND_INTRANET,
    STATUS_ACTIVE,
    STATUS_DELETING,
    NetGateway,
    NetGatewayAttachment,
    NetGatewayForward,
)
from app.modules.netgw.schemas import (
    AdminGatewayOut,
    AttachmentIn,
    AttachmentOut,
    DnsOut,
    ForwardIn,
    ForwardOut,
    GatewayIn,
    GatewayOut,
    InstanceForwardIn,
    IpsecIn,
    L3Out,
    NetgwConfigOut,
    OpenConnectIn,
    OpenVpnIn,
    TunnelIn,
    WireGuardIn,
)
from app.modules.netgw.spec import ATTACH_VERSION, RenderInput, cluster_cidrs
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


def _attach_aad(instance_uuid: str) -> str:
    return f"netgw-attach:{instance_uuid}"


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
    l3: dict[str, Any] | None = None
    if body.l3 is not None:
        net = v.parse_l3_cidr(body.l3.cidr, remote, body.local_address, protocol, nat=body.l3.nat)
        l3 = {"cidr": str(net), "nat": body.l3.nat, "address": str(next(net.hosts()))}
    config = {
        "remote_cidrs": [str(n) for n in remote],
        "local_address": str(body.local_address) if body.local_address else None,
        "dns": (
            {"servers": [str(x) for x in dns.servers], "domains": list(dns.domains)}
            if dns is not None and dns.servers
            else None
        ),
        "l3": l3,
        "tunnel": public,
    }
    return config, secrets


def _l3_keys(
    config: dict[str, Any],
    secrets: dict[str, Any],
    old_config: dict[str, Any] | None,
    old_secrets: dict[str, Any] | None,
) -> None:
    """Hub key pair kept across updates (attached instances pin its public key); routes_at moves
    whenever the routed networks change, so attached instances show a pending restart."""
    l3 = config.get("l3")
    if l3 is None:
        return
    old_l3 = (old_config or {}).get("l3")
    key = (old_secrets or {}).get("hub_private_key")
    if old_l3 and key:
        secrets["hub_private_key"] = key
        l3["hub_public_key"] = old_l3["hub_public_key"]
    else:
        secrets["hub_private_key"], l3["hub_public_key"] = _wireguard_keypair()
    same = old_l3 is not None and (old_config or {}).get("remote_cidrs") == config["remote_cidrs"]
    l3["routes_at"] = old_l3["routes_at"] if same and old_l3 else now_utc().isoformat()


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
    _l3_keys(config, secrets, None, None)
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
    old_l3, new_l3 = gw.config.get("l3"), config["l3"]
    if (
        old_l3
        and (new_l3 is None or new_l3["cidr"] != old_l3["cidr"])
        and await _attachments_of(session, gw.id)
    ):
        raise conflict(key="netgw.l3InUse")
    _l3_keys(config, secrets, gw.config, old)
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


async def _attachments_of(session: AsyncSession, gateway_id: int) -> list[NetGatewayAttachment]:
    return list(
        (
            await session.scalars(
                select(NetGatewayAttachment)
                .where(NetGatewayAttachment.gateway_id == gateway_id)
                .order_by(NetGatewayAttachment.id)
            )
        ).all()
    )


def _free_address(cidr: str, taken: set[str]) -> str | None:
    net = ipaddress.IPv4Network(cidr)
    hosts = net.hosts()
    next(hosts)
    for i, ip in enumerate(hosts):
        if i >= v.l3_capacity(net):
            break
        if str(ip) not in taken:
            return str(ip)
    return None


def _userns_tier(instance: Any) -> bool:
    """The user-namespaced runc pools; the Kata (VM) tier has no attach path."""
    return spec_to_gpu_request(instance.spec, instance.gpu_count).host_users is False


async def attach_instance(
    session: AsyncSession, user_id: int, uuid: str, body: AttachmentIn
) -> NetGateway:
    """Give an instance an address in the gateway's L3 network and a hub key pair; the gateway
    picks the peer up without a restart, the instance on its next start. Re-attaching to the same
    gateway is a no-op."""
    gw = await _lock_own(session, user_id, uuid)
    _require_active(gw)
    l3 = gw.config.get("l3")
    if l3 is None:
        raise conflict(key="netgw.l3Disabled")
    instance = await orchestrator_queries.get_instance(session, user_id, body.instance_uuid)
    if instance.workload_type != WORKLOAD_DEV or instance.status in (
        sm_def.RELEASING,
        sm_def.RELEASED,
    ):
        raise AppError(ErrorCode.VALIDATION_ERROR, key="netgw.instanceNotEligible")
    if not _userns_tier(instance):
        raise AppError(ErrorCode.VALIDATION_ERROR, key="netgw.l3InstanceUnsupported")
    existing = await session.scalar(
        select(NetGatewayAttachment).where(NetGatewayAttachment.instance_id == instance.id)
    )
    if existing is not None:
        if existing.gateway_id == gw.id:
            return gw
        raise conflict(key="netgw.instanceAttached")
    address = _free_address(l3["cidr"], {a.address for a in await _attachments_of(session, gw.id)})
    if address is None:
        raise conflict(key="netgw.l3Full")
    private, public = _wireguard_keypair()
    session.add(
        NetGatewayAttachment(
            gateway_id=gw.id,
            user_id=user_id,
            instance_id=instance.id,
            address=address,
            public_key=public,
            private_key_encrypted=encrypt_str(private, aad=_attach_aad(instance.uuid)),
        )
    )
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise conflict(key="netgw.instanceAttached") from None
    _bump(session, gw)
    await session.commit()
    await session.refresh(gw)
    logger.info("netgw_attached", gateway_uuid=gw.uuid, instance_uuid=instance.uuid)
    return gw


async def detach_instance(
    session: AsyncSession, user_id: int, uuid: str, instance_uuid: str
) -> NetGateway:
    gw = await _lock_own(session, user_id, uuid)
    _require_active(gw)
    instance = await orchestrator_queries.get_instance(session, user_id, instance_uuid)
    att = await session.scalar(
        select(NetGatewayAttachment).where(
            NetGatewayAttachment.gateway_id == gw.id,
            NetGatewayAttachment.instance_id == instance.id,
        )
    )
    if att is None:
        raise not_found(key="netgw.attachmentNotFound")
    await session.delete(att)
    _bump(session, gw)
    await session.commit()
    await session.refresh(gw)
    logger.info("netgw_detached", gateway_uuid=gw.uuid, instance_uuid=instance.uuid)
    return gw


async def prune_attachments(session: AsyncSession, gws: Sequence[NetGateway]) -> int:
    """Patrol (rows already locked, no commit): drop attachments whose instance is released and
    re-apply their gateways."""
    by_gw = {g.id: g for g in gws if g.status != STATUS_DELETING}
    if not by_gw:
        return 0
    atts = list(
        (
            await session.scalars(
                select(NetGatewayAttachment).where(NetGatewayAttachment.gateway_id.in_(by_gw))
            )
        ).all()
    )
    instances = {
        i.id: i
        for i in await orchestrator_queries.instances_by_ids(session, {a.instance_id for a in atts})
    }
    gone = [
        a
        for a in atts
        if (i := instances.get(a.instance_id)) is None or i.status == sm_def.RELEASED
    ]
    for att in gone:
        await session.delete(att)
    for gateway_id in {a.gateway_id for a in gone}:
        _bump(session, by_gw[gateway_id])
    return len(gone)


async def instance_attach(session: AsyncSession, instance: Any) -> NetgwAttach | None:
    """Instance side of the instance's attachment for its next Pod: None without one, when its
    gateway is deleting or has L3 off, or when the feature is off."""
    s = get_settings()
    if not s.netgw_image:
        return None
    row = (
        await session.execute(
            select(NetGatewayAttachment, NetGateway)
            .join(NetGateway, NetGateway.id == NetGatewayAttachment.gateway_id)
            .where(NetGatewayAttachment.instance_id == instance.id)
        )
    ).first()
    if row is None:
        return None
    att, gw = row
    l3 = gw.config.get("l3")
    if gw.status != STATUS_ACTIVE or l3 is None:
        return None
    config = {
        "version": ATTACH_VERSION,
        "address": att.address,
        "private_key": decrypt_str(att.private_key_encrypted, aad=_attach_aad(instance.uuid)),
        "peer_public_key": l3["hub_public_key"],
        "routes": list(gw.config["remote_cidrs"]),
        "cluster_cidrs": cluster_cidrs(),
        "endpoint_host": netgw_hub_host(gw.uuid),
    }
    return NetgwAttach(
        gateway_uuid=gw.uuid,
        image=s.netgw_image,
        config_json=json.dumps(config, sort_keys=True, separators=(",", ":")),
    )


def _state(gw: NetGateway) -> str:
    if gw.status == STATUS_DELETING:
        return "deleting"
    if gw.applied_generation < gw.generation:
        return "applying"
    return "connected" if gw.tunnel_up else "disconnected"


def _tunnel_out(gw: NetGateway) -> dict[str, Any]:
    return {"protocol": gw.protocol, **gw.config["tunnel"]}


_POD_HOLDING = (sm_def.CREATING, sm_def.STARTING, sm_def.RUNNING)


def _l3_out(
    gw: NetGateway,
    atts: list[NetGatewayAttachment],
    instances: dict[int, Any],
    pod_builds: dict[int, Any],
) -> L3Out | None:
    """pending_restart: the instance holds a Pod built before the attachment or before the last
    change of the routed networks."""
    l3 = gw.config.get("l3")
    if l3 is None:
        return None
    routes_at = datetime.fromisoformat(l3["routes_at"])
    out: list[AttachmentOut] = []
    for att in atts:
        inst = instances.get(att.instance_id)
        if inst is None or inst.status == sm_def.RELEASED:
            continue
        built = pod_builds.get(inst.id)
        pending = inst.status in _POD_HOLDING and (
            built is None or built < max(att.created_at, routes_at)
        )
        out.append(
            AttachmentOut(
                instance_uuid=inst.uuid,
                instance_name=inst.name,
                address=att.address,
                pending_restart=pending,
                created_at=att.created_at,
            )
        )
    return L3Out(
        cidr=l3["cidr"],
        nat=l3["nat"],
        address=l3["address"],
        max_attachments=v.l3_capacity(ipaddress.IPv4Network(l3["cidr"])),
        attachments=out,
    )


async def gateway_views(session: AsyncSession, gws: Sequence[NetGateway]) -> list[GatewayOut]:
    """Batch: one forwards query, one attachments query, one instance query, one Pod-build
    query."""
    ids = [g.id for g in gws]
    forwards: dict[int, list[NetGatewayForward]] = {i: [] for i in ids}
    attachments: dict[int, list[NetGatewayAttachment]] = {i: [] for i in ids}
    if ids:
        for fw in (
            await session.scalars(
                select(NetGatewayForward)
                .where(NetGatewayForward.gateway_id.in_(ids))
                .order_by(NetGatewayForward.id)
            )
        ).all():
            forwards[fw.gateway_id].append(fw)
        for att in (
            await session.scalars(
                select(NetGatewayAttachment)
                .where(NetGatewayAttachment.gateway_id.in_(ids))
                .order_by(NetGatewayAttachment.id)
            )
        ).all():
            attachments[att.gateway_id].append(att)
    attached_ids = {a.instance_id for atts in attachments.values() for a in atts}
    instance_ids = {f.instance_id for fs in forwards.values() for f in fs if f.instance_id}
    instances = {
        i.id: i
        for i in await orchestrator_queries.instances_by_ids(session, instance_ids | attached_ids)
    }
    pod_builds = await orchestrator_queries.last_pod_builds(session, attached_ids)
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
                    "l3": _l3_out(gw, attachments[gw.id], instances, pod_builds),
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
    """Everything the agent spec needs; forwards and attachments of released instances drop
    out."""
    forwards = await _forwards_of(session, gw.id)
    attachments = await _attachments_of(session, gw.id)
    ids = {f.instance_id for f in forwards if f.instance_id} | {a.instance_id for a in attachments}
    instances = await orchestrator_queries.instances_by_ids(session, ids)
    uuids = {i.id: i.uuid for i in instances if i.status != sm_def.RELEASED}
    return RenderInput(
        gateway=gw,
        secrets=decode_secrets(gw),
        forwards=forwards,
        instance_uuids=uuids,
        tenant_namespace=tenant_namespace(gw.user_id),
        dns_upstream=dns_upstream,
        attachments=[a for a in attachments if a.instance_id in uuids],
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
