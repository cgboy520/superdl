"""Gateway spec (/etc/netgw/spec.json): rendered by the platform, validated again here before any
of it reaches a config file or a command line."""

import base64
import binascii
import ipaddress
import json
import re
from dataclasses import dataclass
from typing import Any

from netgw_agent import ovpn

SPEC_VERSION = 1
PROTOCOLS = ("wireguard", "ipsec", "openvpn", "openconnect")
OPENCONNECT_PROTOCOLS = ("anyconnect", "array", "f5", "fortinet", "gp", "pulse")
IPSEC_PROPOSALS = ("default", "compatible", "modern")
SOCKS_PORT = 1080
HEALTH_PORT = 9101
RESERVED_PORTS = frozenset({53, SOCKS_PORT, HEALTH_PORT})
HUB_PORT = 51820
MAX_CIDRS = 16
MAX_FORWARDS = 64
MAX_PEERS = 256
L3_PREFIX_RANGE = (16, 29)
FORBIDDEN_NETS = tuple(
    ipaddress.ip_network(n)
    for n in ("0.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16", "224.0.0.0/4", "240.0.0.0/4")
)

_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOST_RE = re.compile(rf"^(?=.{{1,253}}$){_LABEL}(?:\.{_LABEL})*$")
_TOKEN_RE = re.compile(r"^[\x21-\x7e]{1,256}$")
_ID_RE = re.compile(r"^[A-Za-z0-9.@:_=+/-]{1,128}$")
_PSK_RE = re.compile(r"^[\x20-\x7e]{16,128}$")
_UUID_RE = re.compile(r"^[0-9a-f]{32}$")
_PIN_RE = re.compile(r"^(sha256|pin-sha256):[A-Za-z0-9+/=]{16,128}$")
_URL_RE = re.compile(rf"^https://{_LABEL}(?:\.{_LABEL})*(?::[0-9]{{1,5}})?(?:/[A-Za-z0-9._~/-]*)?$")


class SpecError(ValueError):
    """The spec is malformed or asks for something the agent refuses to do."""


@dataclass(frozen=True)
class Forward:
    """One TCP relay: listen on `port`, connect to `host:target_port`."""

    port: int
    host: str
    target_port: int


@dataclass(frozen=True)
class L3:
    """Whole-subnet access: instances attach from `cidr` to the hub (WireGuard on `address`, the
    first host); the hub forwards them to remote_cidrs, routed or masqueraded (`nat`)."""

    cidr: ipaddress.IPv4Network
    address: ipaddress.IPv4Address
    nat: bool
    private_key: str


@dataclass(frozen=True)
class Peer:
    """One attached instance: its hub key, its address in l3.cidr, the DNS name of its Pod."""

    public_key: str
    address: ipaddress.IPv4Address
    host: str


@dataclass(frozen=True)
class Spec:
    gateway: str
    protocol: str
    remote_cidrs: tuple[ipaddress.IPv4Network, ...]
    cluster_cidrs: tuple[ipaddress.IPv4Network, ...]
    local_address: ipaddress.IPv4Address | None
    dns_upstream: ipaddress.IPv4Address
    dns_servers: tuple[ipaddress.IPv4Address, ...]
    dns_domains: tuple[str, ...]
    forwards: tuple[Forward, ...]
    reverse: tuple[Forward, ...]
    params: dict[str, Any]
    l3: L3 | None = None


def _req(obj: dict[str, Any], key: str, kind: type) -> Any:
    value = obj.get(key)
    if not isinstance(value, kind) or isinstance(value, bool) and kind is int:
        raise SpecError(f"{key}: expected {kind.__name__}")
    return value


def _port(value: Any, key: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65535:
        raise SpecError(f"{key}: port must be 1-65535")
    return value


def ipv4(value: Any, key: str) -> ipaddress.IPv4Address:
    try:
        return ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, ValueError):
        raise SpecError(f"{key}: not an IPv4 address") from None


def is_host(value: str) -> bool:
    """DNS name (no trailing dot) or dotted-quad IPv4."""
    return bool(_HOST_RE.match(value))


def _host(value: Any, key: str) -> str:
    if not isinstance(value, str) or not is_host(value):
        raise SpecError(f"{key}: not a host name or IPv4 address")
    return value


def _endpoint(value: Any, key: str) -> tuple[str, int]:
    if not isinstance(value, str) or value.count(":") != 1:
        raise SpecError(f"{key}: expected host:port")
    host, _, port = value.partition(":")
    if not port.isdigit():
        raise SpecError(f"{key}: expected host:port")
    return _host(host, key), _port(int(port), key)


def wg_key(value: Any, key: str) -> str:
    try:
        raw = base64.b64decode(value, validate=True) if isinstance(value, str) else b""
    except binascii.Error:
        raw = b""
    if len(raw) != 32:
        raise SpecError(f"{key}: not a WireGuard key")
    return value


def networks(values: Any, key: str, *, limit: int) -> tuple[ipaddress.IPv4Network, ...]:
    if not isinstance(values, list) or not values or len(values) > limit:
        raise SpecError(f"{key}: expected 1-{limit} networks")
    out: list[ipaddress.IPv4Network] = []
    for v in values:
        try:
            out.append(ipaddress.IPv4Network(v))
        except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError, TypeError):
            raise SpecError(f"{key}: {v!r} is not an IPv4 network") from None
    return tuple(out)


def _forwards(values: Any, key: str) -> tuple[Forward, ...]:
    if not isinstance(values, list) or len(values) > MAX_FORWARDS:
        raise SpecError(f"{key}: expected at most {MAX_FORWARDS} entries")
    out: list[Forward] = []
    for i, item in enumerate(values):
        if not isinstance(item, dict):
            raise SpecError(f"{key}[{i}]: expected an object")
        port = _port(item.get("port"), f"{key}[{i}].port")
        if port in RESERVED_PORTS:
            raise SpecError(f"{key}[{i}].port: {port} is reserved")
        host = item.get("host")
        if not isinstance(host, str) or not is_host(host.removesuffix(".")):
            raise SpecError(f"{key}[{i}].host: not a host name or IPv4 address")
        out.append(Forward(port, host, _port(item.get("target_port"), f"{key}[{i}].target_port")))
    ports = [f.port for f in out]
    if len(set(ports)) != len(ports):
        raise SpecError(f"{key}: duplicate listen port")
    return tuple(out)


def _check_wireguard(p: dict[str, Any]) -> None:
    wg_key(p.get("private_key"), "wireguard.private_key")
    wg_key(p.get("peer_public_key"), "wireguard.peer_public_key")
    if p.get("preshared_key") is not None:
        wg_key(p["preshared_key"], "wireguard.preshared_key")
    _endpoint(p.get("endpoint"), "wireguard.endpoint")
    mtu = p.get("mtu", 1420)
    if not isinstance(mtu, int) or not 1280 <= mtu <= 1500:
        raise SpecError("wireguard.mtu: must be 1280-1500")


def _check_ipsec(p: dict[str, Any]) -> None:
    if p.get("version") not in (1, 2):
        raise SpecError("ipsec.version: must be 1 or 2")
    _host(p.get("peer"), "ipsec.peer")
    psk = p.get("psk")
    if not isinstance(psk, str) or not _PSK_RE.match(psk) or '"' in psk or "\\" in psk:
        raise SpecError("ipsec.psk: 16-128 printable ASCII characters without quotes or backslash")
    for key in ("local_id", "remote_id"):
        value = p.get(key)
        if value is not None and (not isinstance(value, str) or not _ID_RE.match(value)):
            raise SpecError(f"ipsec.{key}: invalid identity")
    if p.get("proposals", "compatible") not in IPSEC_PROPOSALS:
        raise SpecError("ipsec.proposals: unknown preset")


def _check_openvpn(p: dict[str, Any]) -> None:
    config = p.get("config")
    if not isinstance(config, str):
        raise SpecError("openvpn.config: expected text")
    try:
        ovpn.parse(config)
    except ovpn.ProfileError as exc:
        raise SpecError(f"openvpn.config: {exc}") from None
    for key in ("username", "password"):
        value = p.get(key)
        if value is not None and (not isinstance(value, str) or not _TOKEN_RE.match(value)):
            raise SpecError(f"openvpn.{key}: 1-256 printable ASCII characters without spaces")


def _check_openconnect(p: dict[str, Any]) -> None:
    if p.get("protocol") not in OPENCONNECT_PROTOCOLS:
        raise SpecError("openconnect.protocol: unknown protocol")
    server = p.get("server")
    if not isinstance(server, str) or not _URL_RE.match(server):
        raise SpecError("openconnect.server: expected https://host[:port][/path]")
    for key in ("username", "password"):
        value = p.get(key)
        if not isinstance(value, str) or not _TOKEN_RE.match(value):
            raise SpecError(f"openconnect.{key}: 1-256 printable ASCII characters without spaces")
    pin = p.get("servercert")
    if pin is not None and (not isinstance(pin, str) or not _PIN_RE.match(pin)):
        raise SpecError("openconnect.servercert: expected sha256:<fingerprint>")
    group = p.get("authgroup")
    if group is not None and (not isinstance(group, str) or not _ID_RE.match(group)):
        raise SpecError("openconnect.authgroup: invalid group name")


_CHECKS = {
    "wireguard": _check_wireguard,
    "ipsec": _check_ipsec,
    "openvpn": _check_openvpn,
    "openconnect": _check_openconnect,
}


def _check_overlaps(
    remote: tuple[ipaddress.IPv4Network, ...], cluster: tuple[ipaddress.IPv4Network, ...]
) -> None:
    for net in remote:
        for bad in (*FORBIDDEN_NETS, *cluster):
            if net.overlaps(bad):
                raise SpecError(f"remote_cidrs: {net} overlaps {bad}")


def _dns(raw: Any, remote: tuple[ipaddress.IPv4Network, ...]) -> tuple[Any, ...]:
    if not isinstance(raw, dict):
        raise SpecError("dns: expected an object")
    upstream = ipv4(raw.get("upstream"), "dns.upstream")
    servers = tuple(ipv4(s, "dns.servers") for s in raw.get("servers") or [])
    domains = tuple(_host(d, "dns.domains") for d in raw.get("domains") or [])
    if len(servers) > 3 or len(domains) > 8:
        raise SpecError("dns: at most 3 servers and 8 domains")
    if bool(servers) != bool(domains):
        raise SpecError("dns: servers and domains go together")
    if any(not any(s in n for n in remote) for s in servers):
        raise SpecError("dns.servers: must be inside remote_cidrs")
    return upstream, servers, domains


def _l3(
    raw: Any,
    protocol: str,
    taken: tuple[ipaddress.IPv4Network, ...],
    local: ipaddress.IPv4Address | None,
) -> L3 | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise SpecError("l3: expected an object")
    try:
        cidr = ipaddress.IPv4Network(raw.get("cidr"))
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError, TypeError):
        raise SpecError("l3.cidr: not an IPv4 network") from None
    low, high = L3_PREFIX_RANGE
    if not low <= cidr.prefixlen <= high:
        raise SpecError(f"l3.cidr: prefix must be /{low}-/{high}")
    for bad in (*FORBIDDEN_NETS, *taken):
        if cidr.overlaps(bad):
            raise SpecError(f"l3.cidr: {cidr} overlaps {bad}")
    if local is not None and local in cidr:
        raise SpecError("l3.cidr: contains local_address")
    address = ipv4(raw.get("address"), "l3.address")
    if address != next(cidr.hosts()):
        raise SpecError("l3.address: must be the first host of l3.cidr")
    nat = raw.get("nat")
    if not isinstance(nat, bool):
        raise SpecError("l3.nat: expected a boolean")
    if protocol == "openconnect" and not nat:
        raise SpecError("l3.nat: openconnect servers assign one address, nat is required")
    return L3(cidr, address, nat, wg_key(raw.get("private_key"), "l3.private_key"))


def parse_peers(raw: Any, l3: L3) -> tuple[Peer, ...]:
    """peers.json: {"peers": [{"public_key", "address", "host"}]}; addresses unique inside
    l3.cidr, never the hub address."""
    items = raw.get("peers") if isinstance(raw, dict) else None
    if not isinstance(items, list) or len(items) > MAX_PEERS:
        raise SpecError(f"peers: expected at most {MAX_PEERS} entries")
    out: list[Peer] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise SpecError(f"peers[{i}]: expected an object")
        key = wg_key(item.get("public_key"), f"peers[{i}].public_key")
        address = ipv4(item.get("address"), f"peers[{i}].address")
        if address not in l3.cidr or address in (
            l3.address,
            l3.cidr.network_address,
            l3.cidr.broadcast_address,
        ):
            raise SpecError(f"peers[{i}].address: not an instance address of l3.cidr")
        host = item.get("host")
        if not isinstance(host, str) or not is_host(host.removesuffix(".")):
            raise SpecError(f"peers[{i}].host: not a host name")
        out.append(Peer(key, address, host))
    if len({p.address for p in out}) != len(out) or len({p.public_key for p in out}) != len(out):
        raise SpecError("peers: duplicate address or key")
    return tuple(out)


def parse(raw: dict[str, Any]) -> Spec:
    if raw.get("version") != SPEC_VERSION:
        raise SpecError(f"version: expected {SPEC_VERSION}")
    gateway = _req(raw, "gateway", str)
    if not _UUID_RE.match(gateway):
        raise SpecError("gateway: expected a 32-hex uuid")
    protocol = raw.get("protocol")
    if protocol not in PROTOCOLS:
        raise SpecError("protocol: unknown protocol")
    remote = networks(raw.get("remote_cidrs"), "remote_cidrs", limit=MAX_CIDRS)
    cluster = networks(raw.get("cluster_cidrs"), "cluster_cidrs", limit=4)
    _check_overlaps(remote, cluster)
    local = raw.get("local_address")
    local_addr = None if local is None else ipv4(local, "local_address")
    if protocol in ("wireguard", "ipsec") and local_addr is None:
        raise SpecError(f"local_address: required for {protocol}")
    if local_addr is not None and any(local_addr in n for n in (*FORBIDDEN_NETS, *cluster)):
        raise SpecError("local_address: inside a forbidden range")
    upstream, servers, domains = _dns(raw.get("dns"), remote)
    forwards = _forwards(raw.get("forwards") or [], "forwards")
    reverse = _forwards(raw.get("reverse") or [], "reverse")
    if {f.port for f in forwards} & {f.port for f in reverse}:
        raise SpecError("forwards and reverse share a listen port")
    params = _req(raw, protocol, dict)
    _CHECKS[protocol](params)
    l3 = _l3(raw.get("l3"), protocol, (*remote, *cluster), local_addr)
    return Spec(
        gateway=gateway,
        protocol=protocol,
        remote_cidrs=remote,
        cluster_cidrs=cluster,
        local_address=local_addr,
        dns_upstream=upstream,
        dns_servers=servers,
        dns_domains=domains,
        forwards=forwards,
        reverse=reverse,
        params=params,
        l3=l3,
    )


def load(path: str) -> Spec:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise SpecError("spec: expected an object")
    return parse(raw)
