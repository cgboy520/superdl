"""Gateway row → agent spec (deploy/netgw spec v1) and K8s parameters. Pure: the caller supplies
decrypted secrets, forwards, instance uuids and the cluster DNS address."""

import json
from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings
from app.core.k8s.base import (
    NetgwInstanceForward,
    NetgwIntranetForward,
    NetgwSpec,
    netgw_instance_forward_host,
)
from app.modules.netgw import ovpn
from app.modules.netgw.models import KIND_INTRANET, NetGateway, NetGatewayForward
from app.modules.netgw.validation import INTERNAL_PORT_BASE, split_endpoint, split_server_url

SPEC_VERSION = 1


@dataclass(frozen=True)
class RenderInput:
    gateway: NetGateway
    secrets: dict[str, Any]
    forwards: list[NetGatewayForward]
    instance_uuids: dict[int, str]
    tenant_namespace: str
    dns_upstream: str


def _protocol_params(gw: NetGateway, secrets: dict[str, Any]) -> dict[str, Any]:
    c = gw.config["tunnel"]
    if gw.protocol == "wireguard":
        return {
            "private_key": secrets["private_key"],
            "peer_public_key": c["peer_public_key"],
            "preshared_key": secrets.get("preshared_key"),
            "endpoint": c["endpoint"],
            "mtu": c["mtu"],
        }
    if gw.protocol == "ipsec":
        return {
            "version": c["version"],
            "peer": c["peer"],
            "psk": secrets["psk"],
            "local_id": c.get("local_id"),
            "remote_id": c.get("remote_id"),
            "proposals": c["proposals"],
        }
    if gw.protocol == "openvpn":
        return {
            "config": secrets["config"],
            "username": c.get("username"),
            "password": secrets.get("password"),
        }
    return {
        "protocol": c["flavor"],
        "server": c["server"],
        "username": c["username"],
        "password": secrets["password"],
        "servercert": c.get("servercert"),
        "authgroup": c.get("authgroup"),
    }


def egress_ports(gw: NetGateway, secrets: dict[str, Any]) -> tuple[tuple[str, int], ...]:
    """(protocol, port) the gateway dials on the internet."""
    c = gw.config["tunnel"]
    if gw.protocol == "wireguard":
        return (("udp", split_endpoint(c["endpoint"])[1]),)
    if gw.protocol == "ipsec":
        return (("udp", 500), ("udp", 4500))
    if gw.protocol == "openvpn":
        ports = {(proto, port) for _host, port, proto in ovpn.remotes(secrets["config"])}
        return tuple(sorted(ports))
    port = split_server_url(c["server"])[1]
    return (("tcp", port), ("udp", port))


def _intranet(forwards: list[NetGatewayForward]) -> list[NetGatewayForward]:
    return sorted((f for f in forwards if f.kind == KIND_INTRANET), key=lambda f: f.id)


def agent_spec(inp: RenderInput) -> dict[str, Any]:
    gw = inp.gateway
    s = get_settings()
    dns = gw.config.get("dns") or {}
    reverse = [
        {
            "port": f.port,
            "host": netgw_instance_forward_host(gw.uuid, f.id, inp.tenant_namespace),
            "target_port": f.target_port,
        }
        for f in sorted(inp.forwards, key=lambda f: f.id)
        if f.kind != KIND_INTRANET and f.instance_id in inp.instance_uuids
    ]
    return {
        "version": SPEC_VERSION,
        "gateway": gw.uuid,
        "protocol": gw.protocol,
        "remote_cidrs": list(gw.config["remote_cidrs"]),
        "cluster_cidrs": [c for c in (s.tenant_pod_cidr, s.tenant_service_cidr) if c],
        "local_address": gw.config.get("local_address"),
        "dns": {
            "upstream": inp.dns_upstream,
            "servers": list(dns.get("servers") or []),
            "domains": list(dns.get("domains") or []),
        },
        "forwards": [
            {"port": INTERNAL_PORT_BASE + i, "host": f.target_host, "target_port": f.target_port}
            for i, f in enumerate(_intranet(inp.forwards))
        ],
        "reverse": reverse,
        gw.protocol: _protocol_params(gw, inp.secrets),
    }


def k8s_spec(inp: RenderInput) -> NetgwSpec:
    gw = inp.gateway
    s = get_settings()
    return NetgwSpec(
        uuid=gw.uuid,
        tenant_namespace=inp.tenant_namespace,
        socks_name=gw.name,
        image=s.netgw_image,
        spec_json=json.dumps(agent_spec(inp), sort_keys=True, separators=(",", ":")),
        egress_ports=egress_ports(gw, inp.secrets),
        intranet=tuple(
            NetgwIntranetForward(
                forward_id=f.id, name=f.name, port=f.port, listen_port=INTERNAL_PORT_BASE + i
            )
            for i, f in enumerate(_intranet(inp.forwards))
        ),
        instance=tuple(
            NetgwInstanceForward(
                forward_id=f.id, instance_name=inp.instance_uuids[f.instance_id], port=f.target_port
            )
            for f in sorted(inp.forwards, key=lambda f: f.id)
            if f.kind != KIND_INTRANET and f.instance_id in inp.instance_uuids
        ),
        bandwidth_mbps=s.netgw_bandwidth_mbps,
    )
