"""镜像仓库(Harbor)接入:拉取凭据正文/指纹、白名单合成、代理缓存映射、API 探测的分步错误。"""

import json

import httpx
import pytest

from app.core.registry import (
    dockerconfigjson,
    effective_image_allowlist,
    parse_proxy_projects,
    probe_harbor,
    pull_secret_fingerprint,
)


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
    assert effective_image_allowlist({}) == []
    assert effective_image_allowlist({"registry_host": "harbor.example.com"}) == [
        "harbor.example.com/"
    ]
    assert effective_image_allowlist(
        {"registry_host": "harbor.example.com", "image_allowed_registries": "docker.io/\nquay.io/"}
    ) == ["harbor.example.com/", "docker.io/", "quay.io/"]
    # 已手填 Harbor 前缀则不重复
    assert effective_image_allowlist(
        {
            "registry_host": "harbor.example.com",
            "image_allowed_registries": "docker.io/, harbor.example.com/",
        }
    ) == ["docker.io/", "harbor.example.com/"]


def test_allowlist_normalizes_trailing_slash_against_prefix_spoofing():
    """录入的前缀一律补 `/`(匹配方是裸 startswith)。"""
    allowed = effective_image_allowlist({"image_allowed_registries": "docker.io"})
    assert allowed == ["docker.io/"]
    assert not any("docker.io.attacker.example/evil:1".startswith(p) for p in allowed)
    assert any("docker.io/library/pytorch:2.9".startswith(p) for p in allowed)
    # 多余斜杠归一;registry_host 同样归一后再比对
    assert effective_image_allowlist(
        {"registry_host": "harbor.example.com/", "image_allowed_registries": "docker.io//\nquay.io"}
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
async def test_probe_distinguishes_project_step_errors(status, ok, step, needle):
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


async def test_probe_health_failures_stop_before_auth():
    """连不上 / 自检不健康:停在 health 步。"""

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns fail", request=request)

    probe = await probe_harbor(
        host="nope.invalid",
        project="superdl",
        robot="",
        secret="",
        ca_pem="",
        transport=httpx.MockTransport(boom),
    )
    assert probe.ok is False and probe.step == "health" and "ConnectError" in probe.detail
    probe = await probe_harbor(
        host="harbor.example.com",
        project="superdl",
        robot="",
        secret="",
        ca_pem="",
        transport=_transport(200, healthy=False),
    )
    assert probe.ok is False and probe.step == "health" and "unhealthy" in probe.detail
