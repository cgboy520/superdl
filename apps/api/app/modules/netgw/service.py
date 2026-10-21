"""Private-network gateways: CRUD, forwards, L3 attachments, views, the render input for the apply
handler and the instance side of an attachment for the instance Pod.

Every change bumps `generation` and enqueues netgw.apply in the same transaction; the handler
records `applied_generation`. Secrets are AES-GCM encrypted (AAD bound to the gateway uuid, or to
the instance uuid for attachment keys) and never returned.

Owner scopes: a personal gateway (org_id null) is reached only through its user's personal scope;
an organization gateway only through the organization scope (owners and admins, organization row
locked first: organization → gateway → attachments). Organization gateways are L3 only and attach
members' instances; a member can always detach their own instance.
"""

import base64
import ipaddress
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from fastapi import status as http_status
from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt_str, encrypt_str
from app.core.errors import AppError, ErrorCode, conflict, not_found
from app.core.gpu_adapter import spec_to_gpu_request
from app.core.handles import mask_handle
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
from app.core.servercopy import copy as server_copy
from app.core.timeutil import now_utc
from app.modules.account import service as account_service
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
    MyAttachmentOut,
    NetgwConfigOut,
    OpenConnectIn,
    OpenVpnIn,
    OrgGatewayOut,
    OrgInstanceOut,
    TunnelIn,
    WireGuardIn,
)
from app.modules.netgw.spec import ATTACH_VERSION, RenderInput, cluster_cidrs
from app.modules.notify import service as notify_service
from app.modules.orchestrator import queries as orchestrator_queries, statemachine as sm_def
from app.modules.orchestrator.schemas import WORKLOAD_DEV, InstanceLogsOut
from app.modules.orgs import service as orgs_service

logger = get_logger(__name__)

APPLY_TASK = "netgw.apply"
DELETE_TASK = "netgw.delete"
LOGS_MAX_TAIL_LINES = 2000
_SECRET_FIELDS = ("preshared_key", "psk", "config", "password")


def tenant_namespace(user_id: int) -> str:
    return f"{get_settings().k8s_namespace_prefix}{user_id}"


@dataclass(frozen=True)
class Personal:
    user_id: int


@dataclass(frozen=True)
class OrgScope:
    org_id: int


type Scope = Personal | OrgScope


def _in_scope(scope: Scope) -> ColumnElement[bool]:
    """The single owner filter: a personal scope never matches an organization gateway, even one
    its user created."""
    if isinstance(scope, Personal):
        return and_(NetGateway.user_id == scope.user_id, NetGateway.org_id.is_(None))
    return NetGateway.org_id == scope.org_id


def netgw_config() -> NetgwConfigOut:
    s = get_settings()
    return NetgwConfigOut(
        enabled=bool(s.netgw_image),
        egress_ips=list(s.parsed_netgw_egress_ips()),
        max_gateways=s.netgw_max_per_user,
        max_org_gateways=s.netgw_max_per_org,
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


def _fingerprint(user_id: int, body: GatewayIn, org_id: int | None = None) -> str:
    """Parameter fingerprint without secret values (only whether each is present); the owner
    scope is part of it, so one key never replays across scopes."""
    data = body.model_dump(mode="json")
    tunnel = data["tunnel"]
    for key in _SECRET_FIELDS:
        if key in tunnel:
            tunnel[key] = tunnel[key] is not None
    payload = json.dumps(data, sort_keys=True)
    if org_id is None:
        return request_fingerprint(user_id, payload)
    return request_fingerprint(user_id, f"org:{org_id}", payload)


async def _name_taken(
    session: AsyncSession, scope: Scope, name: str, *, gateway_id: int | None = None
) -> bool:
    """Personal names share one space with the user's forwards (DNS names in the tenant
    namespace); organization gateway names are labels unique within the organization."""
    gw_stmt = select(NetGateway.id).where(_in_scope(scope), NetGateway.name == name)
    if gateway_id is not None:
        gw_stmt = gw_stmt.where(NetGateway.id != gateway_id)
    if (await session.scalar(gw_stmt)) is not None:
        return True
    if isinstance(scope, OrgScope):
        return False
    fw_stmt = select(NetGatewayForward.id).where(
        NetGatewayForward.user_id == scope.user_id, NetGatewayForward.name == name
    )
    return (await session.scalar(fw_stmt)) is not None


async def _require_name_free(
    session: AsyncSession, scope: Scope, name: str, *, gateway_id: int | None = None
) -> None:
    if await _name_taken(session, scope, name, gateway_id=gateway_id):
        key = "netgw.nameTaken" if isinstance(scope, Personal) else "netgw.orgNameTaken"
        raise conflict(key=key, params={"name": name})


def _bump(session: AsyncSession, gw: NetGateway) -> None:
    gw.generation += 1
    enqueue(session, APPLY_TASK, {"gateway_id": gw.id})


async def create_gateway(
    session: AsyncSession, user_id: int, body: GatewayIn, idempotency_key: str | None
) -> tuple[NetGateway, bool]:
    """Personal gateway: validate → quota → insert + netgw.apply in one transaction; (gateway,
    created)."""
    return await _create(session, user_id, Personal(user_id), body, idempotency_key)


async def _create(
    session: AsyncSession,
    user_id: int,
    scope: Scope,
    body: GatewayIn,
    idempotency_key: str | None,
) -> tuple[NetGateway, bool]:
    _require_enabled()
    org_id = scope.org_id if isinstance(scope, OrgScope) else None
    fingerprint = _fingerprint(user_id, body, org_id)
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
            if existing.org_id != org_id:
                raise conflict(key="common.idempotencyKeyMismatch")
            return existing, False
    config, secrets = _config(body, None)
    count = await session.scalar(
        select(func.count()).select_from(NetGateway).where(_in_scope(scope))
    )
    s = get_settings()
    limit, quota_key = (
        (s.netgw_max_per_user, "netgw.gatewayQuota")
        if org_id is None
        else (s.netgw_max_per_org, "netgw.orgGatewayQuota")
    )
    if (count or 0) >= limit:
        raise AppError(ErrorCode.VALIDATION_ERROR, key=quota_key, params={"max": limit})
    await _require_name_free(session, scope, body.name)
    gateway_uuid = uuid4().hex
    _l3_keys(config, secrets, None, None)
    public_key = None
    if body.tunnel.protocol == "wireguard":
        secrets["private_key"], public_key = _wireguard_keypair()
        config["tunnel"]["public_key"] = public_key
    gw = NetGateway(
        uuid=gateway_uuid,
        user_id=user_id,
        org_id=org_id,
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


async def _lock_gateway(session: AsyncSession, scope: Scope | None, uuid: str) -> NetGateway:
    """FOR UPDATE the gateway within the scope (None: any, admin side); outside it → 404."""
    stmt = select(NetGateway).where(NetGateway.uuid == uuid).with_for_update()
    if scope is not None:
        stmt = stmt.where(_in_scope(scope))
    gw = await session.scalar(stmt)
    if gw is None:
        raise not_found(key="netgw.notFound")
    return gw


def _require_active(gw: NetGateway) -> None:
    if gw.status == STATUS_DELETING:
        raise conflict(key="netgw.gatewayDeleting")


async def _get_scoped(session: AsyncSession, scope: Scope, uuid: str) -> NetGateway:
    gw = await session.scalar(select(NetGateway).where(NetGateway.uuid == uuid, _in_scope(scope)))
    if gw is None:
        raise not_found(key="netgw.notFound")
    return gw


async def _list_scoped(session: AsyncSession, scope: Scope) -> list[NetGateway]:
    return list(
        (
            await session.scalars(
                select(NetGateway).where(_in_scope(scope)).order_by(NetGateway.id)
            )
        ).all()
    )


async def get_gateway(session: AsyncSession, user_id: int, uuid: str) -> NetGateway:
    return await _get_scoped(session, Personal(user_id), uuid)


async def list_gateways(session: AsyncSession, user_id: int) -> list[NetGateway]:
    return await _list_scoped(session, Personal(user_id))


async def update_gateway(
    session: AsyncSession, user_id: int, uuid: str, body: GatewayIn
) -> NetGateway:
    """Replace the configuration; the protocol is fixed, null secrets keep the stored ones."""
    scope = Personal(user_id)
    return await _update(session, scope, await _lock_gateway(session, scope, uuid), body)


async def _update(
    session: AsyncSession, scope: Scope, gw: NetGateway, body: GatewayIn
) -> NetGateway:
    _require_active(gw)
    if body.tunnel.protocol != gw.protocol:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="netgw.protocolImmutable")
    old = decode_secrets(gw)
    config, secrets = _config(body, old)
    if body.name != gw.name:
        await _require_name_free(session, scope, body.name, gateway_id=gw.id)
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
    gw = await _lock_gateway(session, Personal(user_id), uuid)
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
    gw = await _lock_gateway(session, Personal(user_id), uuid)
    _require_active(gw)
    existing = await _forwards_of(session, gw.id)
    limit = get_settings().netgw_max_forwards
    if len(existing) >= limit:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="netgw.forwardQuota", params={"max": limit})
    v.check_name(body.name)
    await _require_name_free(session, Personal(user_id), body.name)
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
    gw = await _lock_gateway(session, Personal(user_id), uuid)
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
    gw = await _lock_gateway(session, Personal(user_id), uuid)
    _check_l3_gateway(gw)
    instance = await orchestrator_queries.get_instance(session, user_id, body.instance_uuid)
    if not await _attach(session, gw, instance):
        return gw
    await session.commit()
    await session.refresh(gw)
    return gw


def _check_l3_gateway(gw: NetGateway) -> None:
    _require_active(gw)
    if gw.config.get("l3") is None:
        raise conflict(key="netgw.l3Disabled")


def _check_attachable(instance: Any) -> None:
    if instance.workload_type != WORKLOAD_DEV or instance.status in (
        sm_def.RELEASING,
        sm_def.RELEASED,
    ):
        raise AppError(ErrorCode.VALIDATION_ERROR, key="netgw.instanceNotEligible")
    if not _userns_tier(instance):
        raise AppError(ErrorCode.VALIDATION_ERROR, key="netgw.l3InstanceUnsupported")


async def _attach(session: AsyncSession, gw: NetGateway, instance: Any) -> bool:
    """Insert the attachment (owned by the instance's user) and bump, no commit; False when the
    instance is already attached to this gateway."""
    _check_l3_gateway(gw)
    _check_attachable(instance)
    existing = await session.scalar(
        select(NetGatewayAttachment).where(NetGatewayAttachment.instance_id == instance.id)
    )
    if existing is not None:
        if existing.gateway_id == gw.id:
            return False
        raise conflict(key="netgw.instanceAttached")
    address = _free_address(
        gw.config["l3"]["cidr"], {a.address for a in await _attachments_of(session, gw.id)}
    )
    if address is None:
        raise conflict(key="netgw.l3Full")
    private, public = _wireguard_keypair()
    session.add(
        NetGatewayAttachment(
            gateway_id=gw.id,
            user_id=instance.user_id,
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
    logger.info("netgw_attached", gateway_uuid=gw.uuid, instance_uuid=instance.uuid)
    return True


async def _detach(session: AsyncSession, gw: NetGateway, instance_id: int) -> None:
    att = await session.scalar(
        select(NetGatewayAttachment).where(
            NetGatewayAttachment.gateway_id == gw.id,
            NetGatewayAttachment.instance_id == instance_id,
        )
    )
    if att is None:
        raise not_found(key="netgw.attachmentNotFound")
    await session.delete(att)
    _bump(session, gw)
    logger.info("netgw_detached", gateway_uuid=gw.uuid, instance_id=instance_id)


async def detach_instance(
    session: AsyncSession, user_id: int, uuid: str, instance_uuid: str
) -> NetGateway:
    gw = await _lock_gateway(session, Personal(user_id), uuid)
    _require_active(gw)
    instance = await orchestrator_queries.get_instance(session, user_id, instance_uuid)
    await _detach(session, gw, instance.id)
    await session.commit()
    await session.refresh(gw)
    return gw


# ---------------------------------------------------------------- organization gateways


async def _org_admin(session: AsyncSession, user_id: int) -> Any:
    """The caller's organization row, locked (owners and admins only)."""
    org, _ = await orgs_service.require_org_role(session, user_id, orgs_service.ADMINS, lock=True)
    return org


async def list_org_gateways(session: AsyncSession, user_id: int) -> list[NetGateway]:
    org, _ = await orgs_service.require_org_role(session, user_id, orgs_service.ADMINS)
    return await _list_scoped(session, OrgScope(org.id))


async def get_org_gateway(session: AsyncSession, user_id: int, uuid: str) -> NetGateway:
    org, _ = await orgs_service.require_org_role(session, user_id, orgs_service.ADMINS)
    return await _get_scoped(session, OrgScope(org.id), uuid)


def _require_l3(body: GatewayIn) -> None:
    if body.l3 is None:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="netgw.orgL3Required")


async def create_org_gateway(
    session: AsyncSession, user_id: int, body: GatewayIn, idempotency_key: str | None
) -> tuple[NetGateway, bool]:
    """Organization gateway (L3 required) under the organization lock; (gateway, created)."""
    _require_l3(body)
    org = await _org_admin(session, user_id)
    return await _create(session, user_id, OrgScope(org.id), body, idempotency_key)


async def update_org_gateway(
    session: AsyncSession, user_id: int, uuid: str, body: GatewayIn
) -> NetGateway:
    _require_l3(body)
    org = await _org_admin(session, user_id)
    scope = OrgScope(org.id)
    return await _update(session, scope, await _lock_gateway(session, scope, uuid), body)


async def delete_org_gateway(session: AsyncSession, user_id: int, uuid: str) -> NetGateway:
    org = await _org_admin(session, user_id)
    gw = await _lock_gateway(session, OrgScope(org.id), uuid)
    await _mark_deleting(session, gw)
    return gw


async def _member_instance(session: AsyncSession, org_id: int, instance_uuid: str) -> Any:
    """A member's instance; a non-member's is indistinguishable from a missing one (404)."""
    instance = await orchestrator_queries.instance_by_uuid(session, instance_uuid)
    members = (await orgs_service.active_member_ids(session, [org_id]))[org_id]
    if instance is None or instance.user_id not in members:
        raise not_found(key="orchestrator.instanceNotFound")
    return instance


async def _notify_member(
    session: AsyncSession, instance: Any, gw: NetGateway, org_name: str, key: str
) -> None:
    await notify_service.notify(
        session,
        instance.user_id,
        type_="instance",
        title=server_copy(f"netgw.{key}.title", name=instance.name),
        content=server_copy(
            f"netgw.{key}.content", name=instance.name, gateway=gw.name, org=org_name
        ),
        target_id=instance.uuid,
    )


async def attach_org_instance(
    session: AsyncSession, user_id: int, uuid: str, body: AttachmentIn
) -> NetGateway:
    """Attach a member's instance (owners and admins); the member is notified. Re-attaching to
    the same gateway is a no-op."""
    org = await _org_admin(session, user_id)
    gw = await _lock_gateway(session, OrgScope(org.id), uuid)
    _check_l3_gateway(gw)
    instance = await _member_instance(session, org.id, body.instance_uuid)
    if await _attach(session, gw, instance):
        await _notify_member(session, instance, gw, org.name, "org_attached")
        await session.commit()
        await session.refresh(gw)
    return gw


async def detach_org_instance(
    session: AsyncSession, user_id: int, uuid: str, instance_uuid: str
) -> NetGateway:
    org = await _org_admin(session, user_id)
    gw = await _lock_gateway(session, OrgScope(org.id), uuid)
    _require_active(gw)
    instance = await orchestrator_queries.instance_by_uuid(session, instance_uuid)
    if instance is None:
        raise not_found(key="netgw.attachmentNotFound")
    await _detach(session, gw, instance.id)
    await _notify_member(session, instance, gw, org.name, "org_detached")
    await session.commit()
    await session.refresh(gw)
    return gw


async def org_instances(session: AsyncSession, user_id: int) -> list[OrgInstanceOut]:
    """Members' unreleased dev instances with whether each can be attached (owners and admins);
    no access data."""
    org, _ = await orgs_service.require_org_role(session, user_id, orgs_service.ADMINS)
    members = (await orgs_service.active_member_ids(session, [org.id]))[org.id]
    instances = await orchestrator_queries.dev_instances_of_users(session, members)
    labels = await orgs_service.member_labels(session, org.id, members)
    rows = (
        await session.execute(
            select(NetGatewayAttachment.instance_id, NetGateway.uuid, NetGateway.org_id)
            .join(NetGateway, NetGateway.id == NetGatewayAttachment.gateway_id)
            .where(NetGatewayAttachment.instance_id.in_([i.id for i in instances]))
        )
    ).tuples()
    attached = {iid: (gw_uuid if gw_org == org.id else None) for iid, gw_uuid, gw_org in rows}
    out: list[OrgInstanceOut] = []
    for inst in instances:
        label = labels.get(inst.user_id)
        if label is None:
            continue
        reason = (
            "attached" if inst.id in attached else (None if _userns_tier(inst) else "kata_tier")
        )
        out.append(
            OrgInstanceOut.model_validate(
                {
                    "instance_uuid": inst.uuid,
                    "instance_name": inst.name,
                    "status": inst.status,
                    "member_id": label.member_id,
                    "member_label": label.label,
                    "attachable": reason is None,
                    "reason": reason,
                    "attached_to": attached.get(inst.id),
                }
            )
        )
    return out


async def my_attachments(session: AsyncSession, user_id: int) -> list[MyAttachmentOut]:
    """The caller's instances attached to any gateway, personal or organization."""
    rows = (
        (
            await session.execute(
                select(NetGatewayAttachment, NetGateway)
                .join(NetGateway, NetGateway.id == NetGatewayAttachment.gateway_id)
                .where(NetGatewayAttachment.user_id == user_id, NetGateway.status == STATUS_ACTIVE)
                .order_by(NetGatewayAttachment.id)
            )
        )
        .tuples()
        .all()
    )
    instances = {
        i.id: i
        for i in await orchestrator_queries.instances_by_ids(
            session, {a.instance_id for a, _ in rows}
        )
    }
    pod_builds = await orchestrator_queries.last_pod_builds(session, set(instances))
    out: list[MyAttachmentOut] = []
    for att, gw in rows:
        inst = instances.get(att.instance_id)
        if inst is None or inst.status == sm_def.RELEASED or gw.config.get("l3") is None:
            continue
        out.append(
            MyAttachmentOut.model_validate(
                {
                    "gateway_uuid": gw.uuid,
                    "gateway_name": gw.name,
                    "scope": "personal" if gw.org_id is None else "org",
                    "instance_uuid": inst.uuid,
                    "instance_name": inst.name,
                    "address": att.address,
                    "pending_restart": _pending_restart(gw, att, inst, pod_builds),
                    "created_at": att.created_at,
                }
            )
        )
    return out


async def detach_own(session: AsyncSession, user_id: int, instance_uuid: str) -> None:
    """The instance's owner detaches it from whatever gateway holds it; an organization gateway's
    row is locked after its organization's."""
    instance = await orchestrator_queries.get_instance(session, user_id, instance_uuid)
    att = await session.scalar(
        select(NetGatewayAttachment).where(NetGatewayAttachment.instance_id == instance.id)
    )
    if att is None:
        raise not_found(key="netgw.attachmentNotFound")
    org_id = await session.scalar(select(NetGateway.org_id).where(NetGateway.id == att.gateway_id))
    if org_id is not None:
        await orgs_service.lock_org(session, org_id)
    gw = await session.scalar(
        select(NetGateway).where(NetGateway.id == att.gateway_id).with_for_update()
    )
    if gw is None:
        raise not_found(key="netgw.attachmentNotFound")
    await _detach(session, gw, instance.id)
    await session.commit()


async def _drop_members(session: AsyncSession, org_id: int, user_ids: set[int]) -> None:
    """Members removed (organization row locked by the caller): their attachments on the
    organization's gateways go in the same transaction."""
    gws = list(
        (
            await session.scalars(
                select(NetGateway)
                .where(NetGateway.org_id == org_id, NetGateway.status == STATUS_ACTIVE)
                .order_by(NetGateway.id)
                .with_for_update()
            )
        ).all()
    )
    if not gws or not user_ids:
        return
    by_id = {g.id: g for g in gws}
    atts = (
        await session.scalars(
            select(NetGatewayAttachment).where(
                NetGatewayAttachment.gateway_id.in_(by_id),
                NetGatewayAttachment.user_id.in_(user_ids),
            )
        )
    ).all()
    for att in atts:
        await session.delete(att)
    for gateway_id in {a.gateway_id for a in atts}:
        _bump(session, by_id[gateway_id])
    await session.flush()


async def _drop_org(session: AsyncSession, org_id: int) -> None:
    """Organization dissolved: its gateways are torn down."""
    gws = (
        await session.scalars(
            select(NetGateway)
            .where(NetGateway.org_id == org_id, NetGateway.status == STATUS_ACTIVE)
            .order_by(NetGateway.id)
            .with_for_update()
        )
    ).all()
    for gw in gws:
        gw.status = STATUS_DELETING
        enqueue(session, DELETE_TASK, {"gateway_id": gw.id})
    await session.flush()


def register_org_listeners() -> None:
    """Called from wiring: membership changes reach organization gateways in their own
    transaction (idempotent)."""
    orgs_service.register_members_removed_listener(_drop_members)
    orgs_service.register_org_dissolved_listener(_drop_org)


async def prune_attachments(session: AsyncSession, gws: Sequence[NetGateway]) -> int:
    """Patrol (rows already locked, no commit): drop attachments whose instance is released or, on
    an organization gateway, whose owner is no longer a member; re-apply their gateways."""
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
    members = await orgs_service.active_member_ids(
        session, {g.org_id for g in by_gw.values() if g.org_id is not None}
    )

    def stale(a: NetGatewayAttachment) -> bool:
        inst = instances.get(a.instance_id)
        if inst is None or inst.status == sm_def.RELEASED:
            return True
        org_id = by_gw[a.gateway_id].org_id
        return org_id is not None and a.user_id not in members[org_id]

    gone = [a for a in atts if stale(a)]
    for att in gone:
        await session.delete(att)
    for gateway_id in {a.gateway_id for a in gone}:
        _bump(session, by_gw[gateway_id])
    return len(gone)


async def instance_attach(session: AsyncSession, instance: Any) -> NetgwAttach | None:
    """Instance side of the instance's attachment for its next Pod: None without one, when its
    gateway is deleting or has L3 off, when its owner has left the gateway's organization, or
    when the feature is off."""
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
    if gw.org_id is not None:
        member = await orgs_service.membership_of(session, instance.user_id)
        if member is None or member.org_id != gw.org_id:
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


def _pending_restart(
    gw: NetGateway, att: NetGatewayAttachment, inst: Any, pod_builds: dict
) -> bool:
    """The instance holds a Pod built before the attachment or before the last change of the
    routed networks."""
    routes_at = datetime.fromisoformat(gw.config["l3"]["routes_at"])
    built = pod_builds.get(inst.id)
    return inst.status in _POD_HOLDING and (built is None or built < max(att.created_at, routes_at))


def _l3_out(
    gw: NetGateway,
    atts: list[NetGatewayAttachment],
    instances: dict[int, Any],
    pod_builds: dict[int, Any],
    labels: dict[int, Any] | None = None,
) -> L3Out | None:
    """labels (organization gateways): user_id → member label; attachments of non-members (about
    to be pruned) are left out."""
    l3 = gw.config.get("l3")
    if l3 is None:
        return None
    out: list[AttachmentOut] = []
    for att in atts:
        inst = instances.get(att.instance_id)
        if inst is None or inst.status == sm_def.RELEASED:
            continue
        label = labels.get(att.user_id) if labels is not None else None
        if labels is not None and label is None:
            continue
        out.append(
            AttachmentOut(
                instance_uuid=inst.uuid,
                instance_name=inst.name,
                address=att.address,
                pending_restart=_pending_restart(gw, att, inst, pod_builds),
                created_at=att.created_at,
                member_id=label.member_id if label else None,
                member_label=label.label if label else None,
            )
        )
    return L3Out(
        cidr=l3["cidr"],
        nat=l3["nat"],
        address=l3["address"],
        max_attachments=v.l3_capacity(ipaddress.IPv4Network(l3["cidr"])),
        attachments=out,
    )


@dataclass(frozen=True)
class _ViewParts:
    base: dict[str, Any]
    forwards: list[ForwardOut]


async def _view_parts(session: AsyncSession, gws: Sequence[NetGateway]) -> list[_ViewParts]:
    """Batch: one forwards query, one attachments query, one instance query, one Pod-build
    query, one member-label query per organization."""
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
    labels: dict[int, dict[int, Any]] = {}
    for org_id in {g.org_id for g in gws if g.org_id is not None}:
        users = {a.user_id for g in gws if g.org_id == org_id for a in attachments[g.id]}
        labels[org_id] = await orgs_service.member_labels(session, org_id, users)
    out: list[_ViewParts] = []
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
        member_labels = labels.get(gw.org_id) if gw.org_id is not None else None
        base = {
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
            "l3": _l3_out(gw, attachments[gw.id], instances, pod_builds, member_labels),
            "tunnel": _tunnel_out(gw),
            "created_at": gw.created_at,
            "updated_at": gw.updated_at,
        }
        out.append(_ViewParts(base=base, forwards=fws))
    return out


def _socks_url(gw: NetGateway) -> str:
    return f"socks5h://{gw.name}:{NETGW_SOCKS_PORT}"


async def gateway_views(session: AsyncSession, gws: Sequence[NetGateway]) -> list[GatewayOut]:
    parts = await _view_parts(session, gws)
    return [
        GatewayOut.model_validate({**p.base, "socks_url": _socks_url(gw), "forwards": p.forwards})
        for gw, p in zip(gws, parts, strict=True)
    ]


async def gateway_view(session: AsyncSession, gw: NetGateway) -> GatewayOut:
    return (await gateway_views(session, [gw]))[0]


async def org_gateway_views(
    session: AsyncSession, gws: Sequence[NetGateway]
) -> list[OrgGatewayOut]:
    parts = await _view_parts(session, gws)
    emails = await account_service.emails_by_user(session, {g.user_id for g in gws})
    return [
        OrgGatewayOut.model_validate({**p.base, "created_by": mask_handle(emails.get(gw.user_id))})
        for gw, p in zip(gws, parts, strict=True)
    ]


async def org_gateway_view(session: AsyncSession, gw: NetGateway) -> OrgGatewayOut:
    return (await org_gateway_views(session, [gw]))[0]


async def _read_logs(gw: NetGateway, user_id: int, tail_lines: int) -> InstanceLogsOut:
    await check_rate_limit(f"netgw-logs:{user_id}", max_attempts=20, window_seconds=3600.0)
    tail = min(tail_lines, LOGS_MAX_TAIL_LINES)
    try:
        raw = await get_orchestrator().read_netgw_logs(gw.uuid, tail_lines=tail + 1)
    except Exception as exc:
        logger.warning("netgw_logs_read_failed", gateway_uuid=gw.uuid, error=str(exc))
        raise AppError(
            ErrorCode.INTERNAL,
            key="netgw.logsUnavailable",
            http_status=http_status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    lines = raw.splitlines()
    truncated = len(lines) > tail
    return InstanceLogsOut(lines=lines[-tail:] if truncated else lines, truncated=truncated)


async def read_gateway_logs(
    session: AsyncSession, user_id: int, uuid: str, *, tail_lines: int
) -> InstanceLogsOut:
    """Gateway container log (read-only, not audited): owner only, 20/h per user."""
    gw = await get_gateway(session, user_id, uuid)
    return await _read_logs(gw, user_id, tail_lines)


async def read_org_gateway_logs(
    session: AsyncSession, user_id: int, uuid: str, *, tail_lines: int
) -> InstanceLogsOut:
    """Organization gateway log: owners and admins, the same per-user budget."""
    gw = await get_org_gateway(session, user_id, uuid)
    return await _read_logs(gw, user_id, tail_lines)


async def render_input(session: AsyncSession, gw: NetGateway, *, dns_upstream: str) -> RenderInput:
    """Everything the agent spec needs; forwards and attachments of released instances (and, on
    an organization gateway, of non-members) drop out."""
    forwards = await _forwards_of(session, gw.id)
    attachments = await _attachments_of(session, gw.id)
    if gw.org_id is not None:
        members = (await orgs_service.active_member_ids(session, [gw.org_id]))[gw.org_id]
        attachments = [a for a in attachments if a.user_id in members]
    ids = {f.instance_id for f in forwards if f.instance_id} | {a.instance_id for a in attachments}
    instances = [
        i
        for i in await orchestrator_queries.instances_by_ids(session, ids)
        if i.status != sm_def.RELEASED
    ]
    uuids = {i.id: i.uuid for i in instances}
    return RenderInput(
        gateway=gw,
        secrets=decode_secrets(gw),
        forwards=forwards,
        instance_uuids=uuids,
        tenant_namespace=tenant_namespace(gw.user_id) if gw.org_id is None else None,
        dns_upstream=dns_upstream,
        attachments=[a for a in attachments if a.instance_id in uuids],
        instance_namespaces={i.id: i.k8s_namespace for i in instances},
    )


async def admin_list_gateways(
    session: AsyncSession,
    *,
    user_id: int | None = None,
    uuid: str | None = None,
    org_uuid: str | None = None,
    limit: int = 500,
) -> list[AdminGatewayOut]:
    stmt = select(NetGateway).order_by(NetGateway.id.desc()).limit(limit)
    if user_id is not None:
        stmt = stmt.where(NetGateway.user_id == user_id)
    if uuid is not None:
        stmt = stmt.where(NetGateway.uuid == uuid)
    if org_uuid is not None:
        org_id = await orgs_service.org_id_by_uuid(session, org_uuid)
        if org_id is None:
            return []
        stmt = stmt.where(NetGateway.org_id == org_id)
    gws = list((await session.scalars(stmt)).all())
    parts = await _view_parts(session, gws)
    orgs = await orgs_service.org_refs(session, {g.org_id for g in gws if g.org_id is not None})
    out: list[AdminGatewayOut] = []
    for gw, p in zip(gws, parts, strict=True):
        org = orgs.get(gw.org_id) if gw.org_id is not None else None
        out.append(
            AdminGatewayOut.model_validate(
                {
                    **p.base,
                    "socks_url": _socks_url(gw) if gw.org_id is None else None,
                    "forwards": p.forwards,
                    "user_id": gw.user_id,
                    "pod_phase": gw.pod_phase,
                    "scope": "personal" if gw.org_id is None else "org",
                    "org_uuid": org[0] if org else None,
                    "org_name": org[1] if org else None,
                }
            )
        )
    return out


async def admin_delete_gateway(session: AsyncSession, uuid: str) -> NetGateway:
    gw = await _lock_gateway(session, None, uuid)
    await _mark_deleting(session, gw)
    return gw
