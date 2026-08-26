"""镜像仓库(Harbor)接入的纯函数与探测:拉取凭据 dockerconfigjson 与指纹、镜像来源白名单、
代理缓存映射解析、Harbor API 连通性探测。

不依赖 K8s 客户端:编排层只消费这里产出的字符串(Secret 内容/指纹),节点侧只消费 registries.yaml。
探测走 httpx,transport 参数供测试注入 MockTransport(与 core/aliyun.rpc_call 同构)。
"""

import base64
import hashlib
import json
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

import httpx

# 平台托管的拉取凭据 Secret 名:superdl ns(平台镜像 + 预热 Job)与每个租户 ns 各一份,
# 内容由配置中心 registry_* 生成,指纹变了才覆写
PULL_SECRET_NAME = "superdl-registry-pull"


def dockerconfigjson(host: str, username: str, password: str) -> str:
    """kubernetes.io/dockerconfigjson 的 .dockerconfigjson 正文。"""
    auth = base64.b64encode(f"{username}:{password}".encode()).decode()
    return json.dumps(
        {"auths": {host: {"username": username, "password": password, "auth": auth}}},
        separators=(",", ":"),
    )


def pull_secret_fingerprint(host: str, username: str, password: str) -> str:
    """凭据指纹(sha256 前 16 位):写在 Secret annotation 上,轮换后指纹变化才触发覆写;
    不可逆,可安全出现在日志与 UI。"""
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


def effective_image_allowlist(cfg: Mapping[str, str]) -> list[str]:
    """创建实例的镜像来源白名单:配置行(换行/逗号分隔的仓库前缀)∪ Harbor 地址前缀。
    空列表 = 不限制。平台镜像目录内的引用由调用方另行放行。"""
    raw = (cfg.get("image_allowed_registries") or "").replace(",", "\n")
    prefixes = [line.strip() for line in raw.splitlines() if line.strip()]
    host = (cfg.get("registry_host") or "").strip()
    if host and f"{host}/" not in prefixes:
        prefixes.insert(0, f"{host}/")
    return prefixes


def ssl_verify(ca_pem: str) -> ssl.SSLContext | bool:
    """自签/私有 CA 时用其构造校验上下文;否则走系统信任链。"""
    return ssl.create_default_context(cadata=ca_pem) if ca_pem.strip() else True


@dataclass(frozen=True)
class HarborProbe:
    ok: bool
    step: Literal["health", "project", "done"]  # 失败发生在哪一步(可行动的错误)
    detail: str
    harbor_version: str | None = None
    repositories: int | None = None  # 机器人在平台项目里可见的仓库数


async def probe_harbor(
    *,
    host: str,
    project: str,
    robot: str,
    secret: str,
    ca_pem: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> HarborProbe:
    """两步探测:① GET /api/v2.0/health(免鉴权:验 DNS/TLS/CA 与 Harbor 自检);
    ② GET /api/v2.0/projects/{project}/repositories?page_size=1(机器人 Basic 鉴权:
    401 = 凭据错、403 = 无 List Repository 权限、404 = 项目不存在)。"""
    base = f"https://{host}/api/v2.0"
    try:
        async with httpx.AsyncClient(
            timeout=10, verify=ssl_verify(ca_pem), transport=transport
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
                return HarborProbe(
                    False, "project", f"GET repositories → HTTP {r.status_code}", version
                )
            total = r.headers.get("x-total-count", "")
            return HarborProbe(True, "done", "ok", version, int(total) if total.isdigit() else None)
    except httpx.HTTPError as exc:  # DNS / TLS / 超时:连 Harbor 都没碰到
        return HarborProbe(False, "health", f"连接失败:{exc.__class__.__name__}: {exc}")
