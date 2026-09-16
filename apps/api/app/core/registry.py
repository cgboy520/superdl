"""Image registry (Harbor) integration: pull-credential dockerconfigjson and fingerprint, image
source allow-list, proxy-cache mapping parser,
Harbor API probe (httpx, transport parameter for test injection; the target is resolved first and
private / reserved ranges are refused).
No dependency on the K8s client."""

import asyncio
import base64
import hashlib
import ipaddress
import json
import re
import socket
import ssl
from dataclasses import dataclass
from typing import Literal

import httpx

PULL_SECRET_NAME = "superdl-registry-pull"
PULL_SECRET_FINGERPRINT_ANNOTATION = "superdl.io/pull-secret-fingerprint"


def dockerconfigjson(host: str, username: str, password: str) -> str:
    """The .dockerconfigjson body of kubernetes.io/dockerconfigjson."""
    auth = base64.b64encode(f"{username}:{password}".encode()).decode()
    return json.dumps(
        {"auths": {host: {"username": username, "password": password, "auth": auth}}},
        separators=(",", ":"),
    )


def pull_secret_fingerprint(host: str, username: str, password: str) -> str:
    """First 16 hex characters of the SHA-256 over host, username and password joined by
    newlines."""
    return hashlib.sha256(f"{host}\n{username}\n{password}".encode()).hexdigest()[:16]


def parse_proxy_projects(text: str) -> dict[str, str]:
    """`<upstream>=<Harbor proxy project>` per line → {upstream: project}; blank lines and lines
    without `=` are ignored."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        upstream, project = line.split("=", 1)
        if upstream.strip() and project.strip():
            out[upstream.strip()] = project.strip()
    return out


_IMAGE_REF_RE = re.compile(
    r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?"
    r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
    r"(?::[A-Za-z0-9_][A-Za-z0-9._-]{0,127})?"
    r"(?:@sha256:[0-9a-f]{64})?$"
)


def is_valid_image_ref(image_ref: str) -> bool:
    """Whether the image reference shape is valid. Shared by instance creation and the admin catalog
    CRUD."""
    return bool(_IMAGE_REF_RE.fullmatch(image_ref))


def is_pinned_image_ref(image_ref: str) -> bool:
    """Whether the reference pins a version (digest, or a non-latest tag; no tag counts as
    latest)."""
    if not is_valid_image_ref(image_ref):
        return False
    if "@sha256:" in image_ref:
        return True
    last = image_ref.rsplit("/", 1)[-1]
    if ":" not in last:
        return False
    return last.rsplit(":", 1)[1] != "latest"


def effective_image_allowlist(*, allowed_registries: str, registry_host: str) -> list[str]:
    """Image source allow-list: configured lines (registry prefixes separated by newlines /
    commas) ∪ the Harbor address prefix, each normalised to end with `/`;
    an empty list = unrestricted. References from the platform image catalog are allowed by the
    caller."""
    raw = allowed_registries.replace(",", "\n")
    prefixes: list[str] = []
    for line in raw.splitlines():
        prefix = line.strip().rstrip("/")
        if prefix and f"{prefix}/" not in prefixes:
            prefixes.append(f"{prefix}/")
    host = registry_host.strip().rstrip("/")
    if host and f"{host}/" not in prefixes:
        prefixes.insert(0, f"{host}/")
    return prefixes


def ssl_verify(ca_pem: str) -> ssl.SSLContext | bool:
    """With a self-signed / private CA build the verification context from it; otherwise use the
    system trust chain."""
    return ssl.create_default_context(cadata=ca_pem) if ca_pem.strip() else True


@dataclass(frozen=True)
class HarborProbe:
    ok: bool
    step: Literal["health", "project", "done"]
    detail: str
    harbor_version: str | None = None
    repositories: int | None = None


_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")
_getaddrinfo = socket.getaddrinfo

PROBE_DETAIL_PRIVATE_TARGET = "target address is in a private / reserved range, probe refused"
PROBE_DETAIL_DNS = "DNS resolution failed"
PROBE_DETAIL_TIMEOUT = "connection timed out"
PROBE_DETAIL_TLS = "TLS handshake failed (certificate or CA mismatch)"
PROBE_DETAIL_CONNECT = "connection refused or unreachable"
PROBE_DETAIL_NETWORK = "request failed (network error)"


def _hostname_of(host: str) -> str:
    """Strip the port: `h:443` → `h`, `[::1]:443` → `::1`."""
    if host.startswith("["):
        return host[1 : host.index("]")] if "]" in host else host[1:]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def is_forbidden_probe_address(ip: str) -> bool:
    """Loopback, link-local, private, CGNAT, multicast, unspecified, reserved and ULA all count as
    internal targets."""
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if isinstance(addr, ipaddress.IPv4Address) and addr in _CGNAT_NET:
        return True
    return (
        addr.is_loopback
        or addr.is_link_local
        or addr.is_private
        or addr.is_multicast
        or addr.is_unspecified
        or addr.is_reserved
        or not addr.is_global
    )


async def _resolve_probe_target(host: str) -> str | None:
    """Resolve the hostname; any address in a refused range returns the refusal reason, a resolution
    failure returns the DNS reason, a clean target returns None."""
    hostname = _hostname_of(host.strip())
    try:
        infos = await asyncio.to_thread(_getaddrinfo, hostname, None)
    except (socket.gaierror, UnicodeError, ValueError):
        return PROBE_DETAIL_DNS
    addresses = {str(info[4][0]) for info in infos}
    if not addresses:
        return PROBE_DETAIL_DNS
    if any(is_forbidden_probe_address(ip) for ip in addresses):
        return PROBE_DETAIL_PRIVATE_TARGET
    return None


def _coarse_error_detail(exc: httpx.HTTPError) -> str:
    """Fold exceptions into fixed copy by category, never echoing the exception text."""
    if isinstance(exc, httpx.TimeoutException):
        return PROBE_DETAIL_TIMEOUT
    if isinstance(exc, httpx.ConnectError):
        cause = exc.__cause__ or exc.__context__
        if isinstance(cause, ssl.SSLError):
            return PROBE_DETAIL_TLS
        return PROBE_DETAIL_CONNECT
    return PROBE_DETAIL_NETWORK


async def _probe_project(
    client: httpx.AsyncClient, base: str, project: str, robot: str, secret: str, version: str | None
) -> HarborProbe:
    r = await client.get(
        f"{base}/projects/{project}/repositories",
        params={"page_size": 1},
        auth=(robot, secret) if robot else None,
    )
    if r.status_code == 401:
        return HarborProbe(False, "project", "401: wrong robot account or secret", version)
    if r.status_code == 403:
        return HarborProbe(
            False,
            "project",
            "403: the robot lacks List Repository (or the project is private and no robot is set)",
            version,
        )
    if r.status_code == 404:
        return HarborProbe(False, "project", f"404: project {project} does not exist", version)
    if r.status_code != 200:
        return HarborProbe(False, "project", f"GET repositories → HTTP {r.status_code}", version)
    total = r.headers.get("x-total-count", "")
    return HarborProbe(True, "done", "ok", version, int(total) if total.isdigit() else None)


async def probe_harbor(
    *,
    host: str,
    project: str,
    robot: str,
    secret: str,
    ca_pem: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> HarborProbe:
    """Resolve the target first (private / reserved ranges refused), then probe Harbor health,
    systeminfo and the project repositories;
    with a robot configured the repository request uses Basic auth. Redirects are not followed;
    connection errors return fixed category copy only."""
    rejected = await _resolve_probe_target(host)
    if rejected is not None:
        return HarborProbe(False, "health", rejected)
    base = f"https://{host}/api/v2.0"
    try:
        async with httpx.AsyncClient(
            timeout=10, verify=ssl_verify(ca_pem), transport=transport, follow_redirects=False
        ) as client:
            r = await client.get(f"{base}/health")
            if r.status_code != 200:
                return HarborProbe(False, "health", f"GET /api/v2.0/health → HTTP {r.status_code}")
            health_status = (r.json() or {}).get("status")
            if health_status != "healthy":
                return HarborProbe(False, "health", f"Harbor health check status={health_status}")
            version: str | None = None
            si = await client.get(f"{base}/systeminfo")
            if si.status_code == 200:
                version = (si.json() or {}).get("harbor_version")
            return await _probe_project(client, base, project, robot, secret, version)
    except httpx.HTTPError as exc:
        return HarborProbe(False, "health", _coarse_error_detail(exc))
