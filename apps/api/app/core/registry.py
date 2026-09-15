"""镜像仓库(Harbor)接入:拉取凭据 dockerconfigjson 与指纹、镜像来源白名单、代理缓存映射解析、
Harbor API 探测(httpx,transport 参数供测试注入;目标先解析地址,内网/保留网段拒探)。
不依赖 K8s 客户端。"""

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
    """kubernetes.io/dockerconfigjson 的 .dockerconfigjson 正文。"""
    auth = base64.b64encode(f"{username}:{password}".encode()).decode()
    return json.dumps(
        {"auths": {host: {"username": username, "password": password, "auth": auth}}},
        separators=(",", ":"),
    )


def pull_secret_fingerprint(host: str, username: str, password: str) -> str:
    """返回 host、username、password 换行拼接后 SHA-256 的前 16 个十六进制字符。"""
    return hashlib.sha256(f"{host}\n{username}\n{password}".encode()).hexdigest()[:16]


def parse_proxy_projects(text: str) -> dict[str, str]:
    """`<上游>=<Harbor 代理项目>` 每行一条 → {上游: 项目};空行与无 `=` 的行忽略。"""
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
    """镜像引用形态是否合法。创建实例与管理端目录 CRUD 共用同一份判定。"""
    return bool(_IMAGE_REF_RE.fullmatch(image_ref))


def is_pinned_image_ref(image_ref: str) -> bool:
    """引用是否钉到具体版本(带 digest,或带非 latest 的 tag;无 tag 视为 latest)。"""
    if not is_valid_image_ref(image_ref):
        return False
    if "@sha256:" in image_ref:
        return True
    last = image_ref.rsplit("/", 1)[-1]
    if ":" not in last:
        return False
    return last.rsplit(":", 1)[1] != "latest"


def effective_image_allowlist(*, allowed_registries: str, registry_host: str) -> list[str]:
    """镜像来源白名单:配置行(换行/逗号分隔的仓库前缀)∪ Harbor 地址前缀,每条补成 `/` 结尾;
    空列表 = 不限制。平台镜像目录内的引用由调用方放行。"""
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
    """自签/私有 CA 时用其构造校验上下文;否则走系统信任链。"""
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

PROBE_DETAIL_PRIVATE_TARGET = "目标地址属于内网/保留网段,拒绝探测"
PROBE_DETAIL_DNS = "域名解析失败"
PROBE_DETAIL_TIMEOUT = "连接超时"
PROBE_DETAIL_TLS = "TLS 握手失败(证书或 CA 不匹配)"
PROBE_DETAIL_CONNECT = "连接被拒绝或不可达"
PROBE_DETAIL_NETWORK = "请求失败(网络层错误)"


def _hostname_of(host: str) -> str:
    """去掉端口:`h:443` → `h`,`[::1]:443` → `::1`。"""
    if host.startswith("["):
        return host[1 : host.index("]")] if "]" in host else host[1:]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def is_forbidden_probe_address(ip: str) -> bool:
    """回环、链路本地、私网、CGNAT、组播、未指定、保留与 ULA 一律视为内网目标。"""
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
    """解析主机名;任一地址落在禁探网段即返回拒绝原因,解析失败返回 DNS 原因,合规返回 None。"""
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
    """异常按类别归并成固定文案,不回显异常正文。"""
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
        return HarborProbe(False, "project", "401:机器人账户或 Secret 错误", version)
    if r.status_code == 403:
        return HarborProbe(
            False,
            "project",
            "403:机器人无 List Repository 权限(或项目为私有而未填机器人账户)",
            version,
        )
    if r.status_code == 404:
        return HarborProbe(False, "project", f"404:项目 {project} 不存在", version)
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
    """先解析目标地址(内网/保留网段拒探),再探测 Harbor health、systeminfo 和项目仓库;
    配置 robot 时对仓库请求使用 Basic 鉴权。不跟随重定向;连接类错误只回固定分类文案。"""
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
                return HarborProbe(False, "health", f"Harbor 自检 status={health_status}")
            version: str | None = None
            si = await client.get(f"{base}/systeminfo")
            if si.status_code == 200:
                version = (si.json() or {}).get("harbor_version")
            return await _probe_project(client, base, project, robot, secret, version)
    except httpx.HTTPError as exc:
        return HarborProbe(False, "health", _coarse_error_detail(exc))
