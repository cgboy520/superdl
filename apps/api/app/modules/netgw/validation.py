"""Gateway input rules that need more than field shapes: address ranges, peer reachability, names,
protocol parameters. Every failure is a VALIDATION_ERROR with a netgw.* key."""

import base64
import binascii
import ipaddress
import re
from collections.abc import Iterable
from urllib.parse import urlsplit

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.k8s.base import EGRESS_BLOCKED_TCP_PORTS, NETGW_HEALTH_PORT, NETGW_SOCKS_PORT
from app.modules.netgw import ovpn

Net = ipaddress.IPv4Network

FORBIDDEN_NETS = tuple(
    ipaddress.IPv4Network(n)
    for n in ("0.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16", "224.0.0.0/4", "240.0.0.0/4")
)
MIN_PREFIX = 8
L3_PREFIX_RANGE = (16, 29)
L3_MAX_ATTACHMENTS = 250
L3_RANGES = tuple(
    ipaddress.IPv4Network(n)
    for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")
)
INTERNAL_PORT_BASE = 20000
INTERNAL_PORT_COUNT = 64
RESERVED_LISTEN_PORTS = frozenset(
    {53, NETGW_SOCKS_PORT, NETGW_HEALTH_PORT}
    | set(range(INTERNAL_PORT_BASE, INTERNAL_PORT_BASE + INTERNAL_PORT_COUNT))
)

NAME_PATTERN = r"^[a-z]([-a-z0-9]{0,38}[a-z0-9])?$"
_RESERVED_NAME_RE = re.compile(
    r"^([0-9a-f]{32}(-.*)?|netgw-.*|jupyter-.*|disk-.*|superdl-.*|kubernetes)$"
)
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOST_RE = re.compile(rf"^(?=.{{1,253}}$){_LABEL}(?:\.{_LABEL})*$")
_TOKEN_RE = re.compile(r"^[\x21-\x7e]{1,256}$")
_ID_RE = re.compile(r"^[A-Za-z0-9.@:_=+/-]{1,128}$")
_PSK_RE = re.compile(r"^[\x20-\x7e]{16,128}$")
_PIN_RE = re.compile(r"^(sha256|pin-sha256):[A-Za-z0-9+/=]{16,128}$")
_PATH_RE = re.compile(r"^(/[A-Za-z0-9._~/-]*)?$")


def _invalid(key: str, **params: object) -> AppError:
    return AppError(ErrorCode.VALIDATION_ERROR, key=f"netgw.{key}", params=params or None)


def check_name(name: str) -> None:
    """DNS-1035 label already enforced by the schema; these would collide with platform objects in
    the tenant namespace."""
    if _RESERVED_NAME_RE.match(name):
        raise _invalid("nameReserved", name=name)


def forbidden_networks() -> tuple[Net, ...]:
    s = get_settings()
    cluster = tuple(
        ipaddress.IPv4Network(c, strict=False)
        for c in (s.tenant_pod_cidr, s.tenant_service_cidr)
        if c
    )
    return FORBIDDEN_NETS + cluster


def parse_remote_cidrs(values: Iterable[str]) -> list[Net]:
    out: list[Net] = []
    for raw in values:
        try:
            net = ipaddress.IPv4Network(raw.strip(), strict=False)
        except ValueError:
            raise _invalid("cidrInvalid", cidr=raw) from None
        if net.prefixlen < MIN_PREFIX:
            raise _invalid("cidrTooWide", cidr=str(net), min=MIN_PREFIX)
        for bad in forbidden_networks():
            if net.overlaps(bad):
                raise _invalid("cidrForbidden", cidr=str(net), range=str(bad))
        for prev in out:
            if net.overlaps(prev):
                raise _invalid("cidrOverlap", cidr=str(net), other=str(prev))
        out.append(net)
    return out


def check_local_address(addr: ipaddress.IPv4Address | None, protocol: str) -> None:
    if protocol in ("wireguard", "ipsec"):
        if addr is None:
            raise _invalid("localAddressRequired")
        if any(addr in n for n in forbidden_networks()):
            raise _invalid("localAddressForbidden", address=str(addr))
    elif addr is not None:
        raise _invalid("localAddressNotAllowed")


def parse_l3_cidr(
    raw: str,
    remote: list[Net],
    local: ipaddress.IPv4Address | None,
    protocol: str,
    *,
    nat: bool,
) -> Net:
    """Instance network of L3: private, /16–/29, clear of the reserved, cluster and tunnelled
    ranges and of the tunnel address; openconnect servers assign one address, so NAT only."""
    try:
        net = ipaddress.IPv4Network(raw.strip(), strict=False)
    except ValueError:
        raise _invalid("l3CidrInvalid", cidr=raw) from None
    low, high = L3_PREFIX_RANGE
    if not low <= net.prefixlen <= high:
        raise _invalid("l3CidrSize", cidr=str(net), min=low, max=high)
    if not any(net.subnet_of(r) for r in L3_RANGES):
        raise _invalid("l3CidrNotPrivate", cidr=str(net))
    for bad in forbidden_networks():
        if net.overlaps(bad):
            raise _invalid("cidrForbidden", cidr=str(net), range=str(bad))
    for other in remote:
        if net.overlaps(other):
            raise _invalid("l3CidrOverlapsRemote", cidr=str(net), other=str(other))
    if local is not None and local in net:
        raise _invalid("l3CidrContainsLocal", cidr=str(net))
    if protocol == "openconnect" and not nat:
        raise _invalid("l3NatRequired")
    return net


def l3_capacity(net: Net) -> int:
    """Instance addresses of an L3 network: all hosts but the hub (the first one)."""
    return min(net.num_addresses - 3, L3_MAX_ATTACHMENTS)


def check_host(host: str) -> None:
    if not _HOST_RE.match(host):
        raise _invalid("hostInvalid", host=host)


def _literal(host: str) -> ipaddress.IPv4Address | None:
    try:
        return ipaddress.IPv4Address(host)
    except ValueError:
        return None


def check_peer_host(host: str, remote: list[Net]) -> None:
    """Peer must be a DNS name or a public IPv4 outside the tunnelled ranges (the gateway egress
    policy blocks private destinations anyway)."""
    check_host(host)
    ip = _literal(host)
    if ip is None:
        return
    if not ip.is_global:
        raise _invalid("peerNotPublic", host=host)
    if any(ip in n for n in remote):
        raise _invalid("peerInsideRemote", host=host)


def check_tcp_port(port: int) -> None:
    if port in EGRESS_BLOCKED_TCP_PORTS:
        raise _invalid("peerPortBlocked", port=port)


def split_endpoint(endpoint: str) -> tuple[str, int]:
    host, sep, port = endpoint.strip().rpartition(":")
    if not sep or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise _invalid("endpointInvalid", endpoint=endpoint)
    return host, int(port)


def check_wg_key(value: str, field: str) -> None:
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raw = b""
    if len(raw) != 32:
        raise _invalid("wireguardKeyInvalid", field=field)


def check_psk(psk: str) -> None:
    if not _PSK_RE.match(psk) or '"' in psk or "\\" in psk:
        raise _invalid("pskInvalid")


def check_identity(value: str | None, field: str) -> None:
    if value is not None and not _ID_RE.match(value):
        raise _invalid("identityInvalid", field=field)


def check_token(value: str | None, field: str) -> None:
    if value is not None and not _TOKEN_RE.match(value):
        raise _invalid("credentialInvalid", field=field)


def check_openvpn_profile(profile: str, remote: list[Net]) -> list[tuple[str, int, str]]:
    """Allow-list parse; every remote must be reachable through the gateway egress policy."""
    try:
        remotes = ovpn.remotes(profile)
    except ovpn.ProfileError as exc:
        raise _invalid("openvpnProfileInvalid", reason=str(exc)) from None
    for host, port, proto in remotes:
        check_peer_host(host, remote)
        if proto == "tcp":
            check_tcp_port(port)
    return remotes


def split_server_url(url: str) -> tuple[str, int]:
    """https://host[:port][/path] → (host, port)."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port or 443
    except ValueError:
        raise _invalid("serverUrlInvalid") from None
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.query
        or parts.fragment
        or parts.username
        or not _PATH_RE.match(parts.path)
    ):
        raise _invalid("serverUrlInvalid")
    return parts.hostname, port


def check_servercert(pin: str | None) -> None:
    if pin is not None and not _PIN_RE.match(pin):
        raise _invalid("serverCertInvalid")


def check_dns(servers: list[ipaddress.IPv4Address], domains: list[str], remote: list[Net]) -> None:
    if bool(servers) != bool(domains):
        raise _invalid("dnsIncomplete")
    for server in servers:
        if not any(server in n for n in remote):
            raise _invalid("dnsServerOutside", server=str(server))
    for domain in domains:
        check_host(domain)


def check_intranet_target(host: str, remote: list[Net]) -> None:
    check_host(host)
    ip = _literal(host)
    if ip is not None and not any(ip in n for n in remote):
        raise _invalid("targetOutsideRemote", host=host)


def check_listen_port(port: int) -> None:
    if port in RESERVED_LISTEN_PORTS:
        raise _invalid("portReserved", port=port)
