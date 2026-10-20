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
MAX_CIDRS = 16
MAX_FORWARDS = 64
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


def _req(obj: dict[str, Any], key: str, kind: type) -> Any:
    value = obj.get(key)
    if not isinstance(value, kind) or isinstance(value, bool) and kind is int:
        raise SpecError(f"{key}: expected {kind.__name__}")
    return value


def _port(value: Any, key: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65535:
        raise SpecError(f"{key}: port must be 1-65535")
    return value


def _ipv4(value: Any, key: str) -> ipaddress.IPv4Address:
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


def _wg_key(value: Any, key: str) -> str:
    try:
        raw = base64.b64decode(value, validate=True) if isinstance(value, str) else b""
    except binascii.Error:
        raw = b""
    if len(raw) != 32:
        raise SpecError(f"{key}: not a WireGuard key")
    return value


def _networks(values: Any, key: str, *, limit: int) -> tuple[ipaddress.IPv4Network, ...]:
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
    _wg_key(p.get("private_key"), "wireguard.private_key")
    _wg_key(p.get("peer_public_key"), "wireguard.peer_public_key")
    if p.get("preshared_key") is not None:
        _wg_key(p["preshared_key"], "wireguard.preshared_key")
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
    upstream = _ipv4(raw.get("upstream"), "dns.upstream")
    servers = tuple(_ipv4(s, "dns.servers") for s in raw.get("servers") or [])
    domains = tuple(_host(d, "dns.domains") for d in raw.get("domains") or [])
    if len(servers) > 3 or len(domains) > 8:
        raise SpecError("dns: at most 3 servers and 8 domains")
    if bool(servers) != bool(domains):
        raise SpecError("dns: servers and domains go together")
    if any(not any(s in n for n in remote) for s in servers):
        raise SpecError("dns.servers: must be inside remote_cidrs")
    return upstream, servers, domains


def parse(raw: dict[str, Any]) -> Spec:
    if raw.get("version") != SPEC_VERSION:
        raise SpecError(f"version: expected {SPEC_VERSION}")
    gateway = _req(raw, "gateway", str)
    if not _UUID_RE.match(gateway):
        raise SpecError("gateway: expected a 32-hex uuid")
    protocol = raw.get("protocol")
    if protocol not in PROTOCOLS:
        raise SpecError("protocol: unknown protocol")
    remote = _networks(raw.get("remote_cidrs"), "remote_cidrs", limit=MAX_CIDRS)
    cluster = _networks(raw.get("cluster_cidrs"), "cluster_cidrs", limit=4)
    _check_overlaps(remote, cluster)
    local = raw.get("local_address")
    local_addr = None if local is None else _ipv4(local, "local_address")
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
    )


def load(path: str) -> Spec:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise SpecError("spec: expected an object")
    return parse(raw)
