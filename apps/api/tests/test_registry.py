"""Image registry (Harbor) integration: pull-credential body / fingerprint, allow-list assembly,
proxy-cache mapping, step-by-step probe errors,
private-range refusal of probe targets and coarse error copy."""

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
    """Stand-in getaddrinfo: returns the given addresses and records the resolved hostnames."""

    def __init__(self, *ips: str) -> None:
        self.ips = ips
        self.hosts: list[str] = []

    def __call__(self, host: str, port: object, *args: object, **kwargs: object) -> list:
        self.hosts.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in self.ips]


@pytest.fixture
def public_dns(monkeypatch) -> _FakeResolver:
    """Every hostname resolves to a public address."""
    fake = _FakeResolver("93.184.216.34")
    monkeypatch.setattr(registry, "_getaddrinfo", fake)
    return fake


def _never_called(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"no request expected: {request.url}")


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
    """`$` allows a trailing newline, fullmatch does not."""
    assert is_valid_image_ref("harbor.example.com/superdl/pytorch:2.9")
    assert not is_valid_image_ref("harbor.example.com/superdl/pytorch:2.9\n")


def test_dockerconfigjson_shape_and_fingerprint():
    """The Secret body is the auths structure the kubelet understands; the fingerprint changes with
    the secret and carries no plaintext."""
    body = json.loads(dockerconfigjson("harbor.example.com", "robot$superdl+pull", "s3cret"))
    entry = body["auths"]["harbor.example.com"]
    assert entry["username"] == "robot$superdl+pull" and entry["password"] == "s3cret"
    assert entry["auth"] == "cm9ib3Qkc3VwZXJkbCtwdWxsOnMzY3JldA=="
    fp1 = pull_secret_fingerprint("harbor.example.com", "robot$superdl+pull", "s3cret")
    fp2 = pull_secret_fingerprint("harbor.example.com", "robot$superdl+pull", "rotated")
    assert fp1 != fp2 and len(fp1) == 16 and "s3cret" not in fp1


def test_allowlist_merges_harbor_host_and_lines():
    """The Harbor address joins the allow-list automatically; commas / newlines both split; empty =
    unrestricted."""
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
    """Entered prefixes always get a trailing `/` (the matcher is a bare startswith)."""
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
    """Probe errors step by step (wrong credentials / no permission / project missing)."""
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
    """Unreachable / unhealthy: stops at the health step; connection errors return fixed copy, never
    the exception text."""

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
    """Loopback, private, link-local, CGNAT, multicast, unspecified, reserved, ULA and v4-mapped
    addresses are all refused."""
    assert is_forbidden_probe_address(ip)


def test_public_probe_addresses_allowed():
    assert not is_forbidden_probe_address("93.184.216.34")
    assert not is_forbidden_probe_address("2606:2800:220:1:248:1893:25c8:1946")


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "fd00::1"])
async def test_probe_rejects_private_target_before_connecting(monkeypatch, ip):
    """A target resolving to a private address: refused without a request, fixed copy."""
    monkeypatch.setattr(registry, "_getaddrinfo", _FakeResolver(ip))
    probe = await _probe(host="harbor.internal", transport=httpx.MockTransport(_never_called))
    assert probe.ok is False and probe.step == "health"
    assert probe.detail == registry.PROBE_DETAIL_PRIVATE_TARGET


async def test_probe_rejects_when_any_resolved_address_is_private(monkeypatch):
    """Public and private addresses mixed (DNS rebinding shape): any hit refuses."""
    monkeypatch.setattr(registry, "_getaddrinfo", _FakeResolver("93.184.216.34", "10.0.0.5"))
    probe = await _probe(transport=httpx.MockTransport(_never_called))
    assert probe.detail == registry.PROBE_DETAIL_PRIVATE_TARGET


async def test_probe_strips_port_and_brackets_before_resolving(public_dns):
    """Resolution uses the bare hostname: `host:port` and `[v6]:port` both drop the port."""
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
    """A 302 from health counts as failure; redirects to another host are not followed."""

    def redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://10.0.0.1/api/v2.0/health"})

    probe = await _probe(transport=httpx.MockTransport(redirect))
    assert probe.ok is False and probe.step == "health" and "302" in probe.detail
