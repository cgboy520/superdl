"""节点 registries.yaml 生成:Spegel + Harbor 代理缓存 mirror + CA,不含凭据;高级覆盖原样下发;
bootstrap 下发 registry_ca_pem。"""

import yaml

from app.modules.nodes.service import render_registries_yaml
from tests.helpers import admin_headers
from tests.test_node_enroll import CREATE_BODY, set_cluster_config

CA = "-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----"


def test_default_is_spegel_only_without_harbor():
    """未配 Harbor:只剩 Spegel P2P,不指向任何 NodePort。"""
    out = render_registries_yaml({})
    assert yaml.safe_load(out) == {"mirrors": {"*": {}}}
    assert "30500" not in out


def test_proxy_cache_mirrors_and_ca_without_auth():
    """代理缓存:每个上游 mirror 到 Harbor 并 rewrite 到代理项目;CA 非空才有 configs.tls;
    永远没有 auth——凭据泄漏到节点就是这条挂了。"""
    out = render_registries_yaml(
        {
            "registry_host": "harbor.example.com",
            "registry_proxy_projects": "docker.io=dockerhub\nghcr.io=ghcr",
            "registry_ca_pem": CA,
        }
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
    out = render_registries_yaml({"registry_host": "harbor.example.com"})
    assert "configs" not in out and "ca_file" not in out
    assert (
        render_registries_yaml({"registry_host": "h", "node_registries_yaml": " mirrors: {}\n "})
        == "mirrors: {}"
    )


async def test_bootstrap_carries_registries_and_ca(client, sm):
    """注册链路下发渲染后的 registries.yaml 与 CA,但不带机器人 Secret(拉取凭据不出注册链路)。"""
    from app.core.platform_config import set_platform_settings

    await set_cluster_config(sm)
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
