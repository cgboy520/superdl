"""镜像仓库(Harbor)接入:拉取凭据正文/指纹、白名单合成、代理缓存映射、API 探测的分步错误、
探测目标的内网拒探与粗粒度错误文案。"""

import json
import socket
import ssl

import httpx
import pytest

from app.core import registry
from app.core.registry import (
    dockerconfigjson,
    effective_image_allowlist,
    is_forbidden_probe_address,
    is_valid_image_ref,
    parse_proxy_projects,
    probe_harbor,
    pull_secret_fingerprint,
)


class _FakeResolver:
    """替身 getaddrinfo:固定返回给定地址,记录被解析的主机名。"""

    def __init__(self, *ips: str) -> None:
        self.ips = ips
        self.hosts: list[str] = []

    def __call__(self, host: str, port: object, *args: object, **kwargs: object) -> list:
        self.hosts.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in self.ips]


@pytest.fixture
def public_dns(monkeypatch) -> _FakeResolver:
    """所有主机名都解析到公网地址。"""
    fake = _FakeResolver("93.184.216.34")
    monkeypatch.setattr(registry, "_getaddrinfo", fake)
    return fake


def _never_called(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"不应发起请求:{request.url}")


async def _probe(
    host: str = "harbor.example.com",
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    ca_pem: str = "",
) -> registry.HarborProbe:
    return await probe_harbor(
        host=host, project="superdl", robot="", secret="", ca_pem=ca_pem, transport=transport
    )


def test_image_ref_fullmatch_rejects_trailing_newline():
    """`$` 允许结尾换行,fullmatch 不允许。"""
    assert is_valid_image_ref("harbor.example.com/superdl/pytorch:2.9")
    assert not is_valid_image_ref("harbor.example.com/superdl/pytorch:2.9\n")


def test_dockerconfigjson_shape_and_fingerprint():
    """Secret 正文是 kubelet 认的 auths 结构;指纹随 secret 变化且不含明文。"""
    body = json.loads(dockerconfigjson("harbor.example.com", "robot$superdl+pull", "s3cret"))
    entry = body["auths"]["harbor.example.com"]
    assert entry["username"] == "robot$superdl+pull" and entry["password"] == "s3cret"
    assert entry["auth"] == "cm9ib3Qkc3VwZXJkbCtwdWxsOnMzY3JldA=="
    fp1 = pull_secret_fingerprint("harbor.example.com", "robot$superdl+pull", "s3cret")
    fp2 = pull_secret_fingerprint("harbor.example.com", "robot$superdl+pull", "rotated")
    assert fp1 != fp2 and len(fp1) == 16 and "s3cret" not in fp1


def test_allowlist_merges_harbor_host_and_lines():
    """Harbor 地址自动进白名单;逗号/换行都能分;空 = 不限制。"""
    assert effective_image_allowlist(allowed_registries="", registry_host="") == []
    assert effective_image_allowlist(allowed_registries="", registry_host="harbor.example.com") == [
        "harbor.example.com/"
    ]
    assert effective_image_allowlist(
        allowed_registries="docker.io/\nquay.io/", registry_host="harbor.example.com"
    ) == ["harbor.example.com/", "docker.io/", "quay.io/"]
    assert effective_image_allowlist(
        allowed_registries="docker.io/, harbor.example.com/", registry_host="harbor.example.com"
    ) == ["docker.io/", "harbor.example.com/"]


def test_allowlist_normalizes_trailing_slash_against_prefix_spoofing():
    """录入的前缀一律补 `/`(匹配方是裸 startswith)。"""
    allowed = effective_image_allowlist(allowed_registries="docker.io", registry_host="")
    assert allowed == ["docker.io/"]
    assert not any("docker.io.attacker.example/evil:1".startswith(p) for p in allowed)
    assert any("docker.io/library/pytorch:2.9".startswith(p) for p in allowed)
    assert effective_image_allowlist(
        allowed_registries="docker.io//\nquay.io", registry_host="harbor.example.com/"
    ) == ["harbor.example.com/", "docker.io/", "quay.io/"]


def test_parse_proxy_projects():
    assert parse_proxy_projects("docker.io=dockerhub\n\nghcr.io = ghcr\nbad-line\n") == {
        "docker.io": "dockerhub",
        "ghcr.io": "ghcr",
    }


def _transport(project_status: int, *, total: str | None = "7", healthy: bool = True):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/health"):
            return httpx.Response(200, json={"status": "healthy" if healthy else "unhealthy"})
        if path.endswith("/systeminfo"):
            return httpx.Response(200, json={"harbor_version": "v2.12.0"})
        assert path.endswith("/projects/superdl/repositories")
        assert request.url.params["page_size"] == "1"
        headers = {"x-total-count": total} if total else {}
        return httpx.Response(project_status, json=[], headers=headers)

    return httpx.MockTransport(handler)


@pytest.mark.parametrize(
    ("status", "ok", "step", "needle"),
    [
        (200, True, "done", "ok"),
        (401, False, "project", "401"),
        (403, False, "project", "403"),
        (404, False, "project", "404"),
    ],
)
async def test_probe_distinguishes_project_step_errors(public_dns, status, ok, step, needle):
    """探测分步报错(凭据错 / 无权限 / 项目不存在)。"""
    probe = await probe_harbor(
        host="harbor.example.com",
        project="superdl",
        robot="robot$superdl+pull",
        secret="s",
        ca_pem="",
        transport=_transport(status),
    )
    assert probe.ok is ok and probe.step == step and needle in probe.detail
    assert probe.harbor_version == "v2.12.0"
    if ok:
        assert probe.repositories == 7


async def test_probe_health_failures_stop_before_auth(public_dns):
    """连不上 / 自检不健康:停在 health 步;连接类错误只回固定文案,不回显异常正文。"""

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connect to 10.0.0.9 refused", request=request)

    probe = await _probe(transport=httpx.MockTransport(boom))
    assert probe.ok is False and probe.step == "health"
    assert probe.detail == registry.PROBE_DETAIL_CONNECT
    assert "10.0.0.9" not in probe.detail and "ConnectError" not in probe.detail
    probe = await _probe(transport=_transport(200, healthy=False))
    assert probe.ok is False and probe.step == "health" and "unhealthy" in probe.detail


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "240.0.0.1",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "fd12::1",
        "ff02::1",
        "::ffff:10.0.0.1",
    ],
)
def test_forbidden_probe_addresses(ip):
    """回环、私网、链路本地、CGNAT、组播、未指定、保留、ULA 与 v4 映射地址一律拒探。"""
    assert is_forbidden_probe_address(ip)


def test_public_probe_addresses_allowed():
    assert not is_forbidden_probe_address("93.184.216.34")
    assert not is_forbidden_probe_address("2606:2800:220:1:248:1893:25c8:1946")


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "fd00::1"])
async def test_probe_rejects_private_target_before_connecting(monkeypatch, ip):
    """目标解析到内网地址:不发请求即拒绝,文案固定。"""
    monkeypatch.setattr(registry, "_getaddrinfo", _FakeResolver(ip))
    probe = await _probe(host="harbor.internal", transport=httpx.MockTransport(_never_called))
    assert probe.ok is False and probe.step == "health"
    assert probe.detail == registry.PROBE_DETAIL_PRIVATE_TARGET


async def test_probe_rejects_when_any_resolved_address_is_private(monkeypatch):
    """公网与内网地址混合(DNS rebinding 形态):任一命中即拒。"""
    monkeypatch.setattr(registry, "_getaddrinfo", _FakeResolver("93.184.216.34", "10.0.0.5"))
    probe = await _probe(transport=httpx.MockTransport(_never_called))
    assert probe.detail == registry.PROBE_DETAIL_PRIVATE_TARGET


async def test_probe_strips_port_and_brackets_before_resolving(public_dns):
    """解析用的是纯主机名:`host:port` 与 `[v6]:port` 都去掉端口。"""
    await _probe(host="harbor.example.com:8443", transport=_transport(200))
    await _probe(host="[2606:2800:220:1:248:1893:25c8:1946]:443", transport=_transport(200))
    assert public_dns.hosts == ["harbor.example.com", "2606:2800:220:1:248:1893:25c8:1946"]


async def test_probe_dns_failure_is_coarse(monkeypatch):
    def fail(host: str, port: object, *args: object, **kwargs: object) -> list:
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(registry, "_getaddrinfo", fail)
    probe = await _probe(host="nope.invalid", transport=httpx.MockTransport(_never_called))
    assert probe.ok is False and probe.detail == registry.PROBE_DETAIL_DNS


async def test_probe_timeout_and_tls_details_are_coarse(public_dns):
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    probe = await _probe(transport=httpx.MockTransport(slow))
    assert probe.detail == registry.PROBE_DETAIL_TIMEOUT

    def bad_tls(request: httpx.Request) -> httpx.Response:
        try:
            raise ssl.SSLCertVerificationError("certificate verify failed: self signed")
        except ssl.SSLError as exc:
            raise httpx.ConnectError(str(exc), request=request) from exc

    probe = await _probe(transport=httpx.MockTransport(bad_tls))
    assert probe.detail == registry.PROBE_DETAIL_TLS
    assert "self signed" not in probe.detail


async def test_probe_does_not_follow_redirects(public_dns):
    """health 回 302 视为失败,不跟随跳转到别的主机。"""

    def redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://10.0.0.1/api/v2.0/health"})

    probe = await _probe(transport=httpx.MockTransport(redirect))
    assert probe.ok is False and probe.step == "health" and "302" in probe.detail
