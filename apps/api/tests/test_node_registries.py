"""Node registries.yaml generation: Spegel + Harbor proxy-cache mirrors + CA, no credentials; the
advanced override is handed out verbatim;
bootstrap hands out registry_ca_pem."""

import yaml

from app.core.platform_config import runtime_config_from_strings as rc
from app.modules.nodes.service import render_registries_yaml
from tests.helpers import CREATE_BODY, admin_headers, set_platform_setting

CA = "-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----"


def test_default_is_spegel_only_without_harbor():
    """Without Harbor: Spegel P2P only."""
    out = render_registries_yaml(rc({}))
    assert yaml.safe_load(out) == {"mirrors": {"*": {}}}
    assert "30500" not in out


def test_proxy_cache_mirrors_and_ca_without_auth():
    """Proxy cache: each upstream mirrors to Harbor with a rewrite to the proxy project; configs.tls
    only with a non-empty CA; no auth."""
    out = render_registries_yaml(
        rc(
            {
                "registry_host": "harbor.example.com",
                "registry_proxy_projects": "docker.io=dockerhub\nghcr.io=ghcr",
                "registry_ca_pem": CA,
            }
        )
    )
    data = yaml.safe_load(out)
    assert data["mirrors"]["*"] == {}
    assert data["mirrors"]["docker.io"] == {
        "endpoint": ["https://harbor.example.com"],
        "rewrite": {"^(.*)$": "dockerhub/$1"},
    }
    assert data["mirrors"]["ghcr.io"]["rewrite"] == {"^(.*)$": "ghcr/$1"}
    assert data["configs"] == {
        "harbor.example.com": {"tls": {"ca_file": "__RANCHER_DIR__/harbor-ca.crt"}}
    }
    assert "auth" not in out and "password" not in out


def test_no_ca_no_configs_and_override_verbatim():
    out = render_registries_yaml(rc({"registry_host": "harbor.example.com"}))
    assert "configs" not in out and "ca_file" not in out
    assert (
        render_registries_yaml(
            rc({"registry_host": "h", "node_registries_yaml": " mirrors: {}\n "})
        )
        == "mirrors: {}"
    )


async def test_bootstrap_carries_registries_and_ca(client, sm):
    """The enrollment path hands out registries.yaml and the CA, without the robot secret."""
    from app.core.platform_config import set_platform_settings

    await set_platform_setting(sm, "cluster_server_url", "https://10.0.0.10:9345")
    await set_platform_setting(sm, "cluster_join_token", "agent-fixture-0123456789-secrettoken")
    async with sm() as session:
        await set_platform_settings(
            session,
            {
                "registry_host": "harbor.example.com",
                "registry_ca_pem": CA,
                "registry_proxy_projects": "docker.io=dockerhub",
                "registry_robot_name": "robot$superdl+pull",
                "registry_robot_secret": "never-leaves-the-platform",
            },
            updated_by=None,
        )
        await session.commit()
    ah = await admin_headers(sm, client, role="ops")
    created = await client.post("/api/admin/v1/node-enrollments", json=CREATE_BODY, headers=ah)
    assert created.status_code == 201, created.text
    resp = await client.post(
        "/api/v1/node-enroll/bootstrap",
        json={"hostname": CREATE_BODY["hostname"]},
        headers={"Authorization": f"Bearer {created.json()['token']}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["registry_ca_pem"] == CA
    assert "dockerhub/$1" in body["registries_yaml"] and "ca_file" in body["registries_yaml"]
    assert "never-leaves-the-platform" not in resp.text and "robot$" not in resp.text
