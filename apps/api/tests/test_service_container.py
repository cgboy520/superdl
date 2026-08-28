"""服务型实例(workload_type='service')的契约与落库形态。

每条用例对应一处不变量:
- 端点行与 slug:挂了说明用户拿不到公网地址,或内部 uuid 漏进了公网域名
- 端口池:挂了说明每台对外服务白占一个 NodePort —— 端口池只有 30000–32767 一段
- env 密文:挂了说明用户的 HF_TOKEN 之类落进了 Pod spec(进 etcd/审计快照)
- 契约矩阵:挂了说明「传了却被静默忽略」或「平台占用端口被放行」
- dev 分支:挂了说明开发机的 Pod spec 被服务型分支改坏了
"""

import pytest
from cryptography.exceptions import InvalidTag
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.crypto import decrypt_str
from app.core.k8s.fake import FakeOrchestrator
from app.modules.orchestrator import service
from app.modules.orchestrator.models import Instance, PortAllocation, ServiceEndpoint
from app.modules.orchestrator.reconciler import reconcile_once
from tests.helpers import create_test_sku, create_user_with_key, drain, drain_strict, fund_wallet

pytestmark = pytest.mark.usefixtures("fake")

IMAGE = "registry.superdl.local/vllm:0.11.0"


def service_body(sku_id: int, **over) -> dict:
    body = {
        "sku_id": sku_id,
        "gpu_count": 1,
        "image_ref": IMAGE,
        "ssh_key_ids": [],
        "workload_type": "service",
        "service_port": 8000,
    }
    body.update(over)
    return body


async def new_user(client: AsyncClient, sm, phone: str) -> tuple[dict[str, str], int, int, int]:
    """注册 + 充值 + 建 SKU。返回 (headers, user_id, ssh_key_id, sku_id)。"""
    headers, user_id, key_id = await create_user_with_key(client, phone)
    await fund_wallet(sm, user_id)
    return headers, user_id, key_id, await create_test_sku(sm)


async def provision_service(
    client: AsyncClient,
    sm: async_sessionmaker[AsyncSession],
    fake: FakeOrchestrator,
    *,
    phone: str = "13900000301",
    **over,
) -> tuple[dict[str, str], str, int]:
    """建一台 running 的服务型实例。返回 (headers, uuid, user_id)。"""
    headers, user_id, key_id, sku_id = await new_user(client, sm, phone)
    over.setdefault("ssh_key_ids", [key_id] if over.get("with_ssh") else [])
    resp = await client.post(
        "/api/v1/instances", json=service_body(sku_id, **over), headers=headers
    )
    assert resp.status_code == 202, resp.text
    uuid = resp.json()["uuid"]
    await drain_strict(sm)
    fake.mark_ready(f"tenant-{user_id}", uuid)
    await reconcile_once(sm)
    return headers, uuid, user_id


async def load_instance(sm, uuid: str) -> Instance:
    async with sm() as session:
        return (await session.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()


class TestServiceInstanceCreate:
    async def test_endpoint_row_and_slug_shape(self, client, sm, fake):
        """服务实例落一行 service_endpoints,slug 是随机 base32 而非实例 uuid。

        挂了说明内部主键进了公网域名 / TLS SNI / 第三方 Referer。
        """
        _headers, uuid, _user_id = await provision_service(client, sm, fake, health_path="/health")
        async with sm() as session:
            inst = (
                await session.execute(select(Instance).where(Instance.uuid == uuid))
            ).scalar_one()
            endpoint = (
                await session.execute(
                    select(ServiceEndpoint).where(ServiceEndpoint.instance_id == inst.id)
                )
            ).scalar_one()
        assert endpoint.public_slug.startswith("svc-")
        assert len(endpoint.public_slug) == 14  # svc- + 10 位 base32
        assert uuid not in endpoint.public_slug
        assert endpoint.container_port == 8000
        assert endpoint.health_path == "/health"
        assert endpoint.require_api_key is True

    async def test_no_ssh_means_no_port_pool_slot(self, client, sm, fake):
        """with_ssh=False 的服务实例不进端口池。

        挂了说明每台对外服务都白占一个 NodePort —— 端口池 30000–32767 是全平台硬上限,
        实例数远未到配额它就先耗尽了。
        """
        _headers, uuid, user_id = await provision_service(client, sm, fake)
        instance = await load_instance(sm, uuid)
        assert instance.with_ssh is False
        assert instance.ssh_port is None
        async with sm() as session:
            allocations = list((await session.execute(select(PortAllocation))).scalars())
        assert allocations == []
        spec = fake.pods[(f"tenant-{user_id}", uuid)].spec
        assert spec.with_ssh is False
        assert spec.ssh_node_port is None
        assert spec.authorized_keys == ()

    async def test_with_ssh_still_allocates_port(self, client, sm, fake):
        """勾了 SSH 的服务实例照旧占端口池:两条分支必须都活着。"""
        _headers, uuid, user_id = await provision_service(
            client, sm, fake, phone="13900000302", with_ssh=True
        )
        instance = await load_instance(sm, uuid)
        assert instance.with_ssh is True
        assert instance.ssh_port is not None
        spec = fake.pods[(f"tenant-{user_id}", uuid)].spec
        assert spec.with_ssh is True
        assert spec.ssh_node_port == instance.ssh_port
        assert len(spec.authorized_keys) == 1

    async def test_pod_spec_service_fork(self, client, sm, fake):
        """service 形态的 Pod spec:Always 重启 + 用户启动命令 + 对外 Service + 探针。

        restartPolicy 写成 Never 时,用户容器崩一次就把实例判终结。
        """
        _headers, uuid, user_id = await provision_service(
            client,
            sm,
            fake,
            phone="13900000303",
            container_command=["python", "-m", "vllm.entrypoints.openai.api_server"],
            container_args=["--port", "8000"],
            health_path="/health",
        )
        spec = fake.pods[(f"tenant-{user_id}", uuid)].spec
        assert spec.restart_policy == "Always"
        assert spec.command == ("python", "-m", "vllm.entrypoints.openai.api_server")
        assert spec.args == ("--port", "8000")
        assert spec.service_port == 8000
        assert spec.health_path == "/health"
        assert spec.service_host.endswith(f".{get_settings().service_domain_suffix}")
        assert spec.service_host.startswith("svc-")
        # 服务容器不跑 Jupyter:token 不进 Secret,也不注入 JUPYTER_ALLOW_ORIGIN
        assert "JUPYTER_TOKEN" not in spec.secret_env
        assert "JUPYTER_ALLOW_ORIGIN" not in spec.env

    async def test_dev_fork_unchanged(self, client, sm, fake):
        """dev 形态逐字不变:Never + 无对外 Service + Jupyter token 走 Secret。"""
        headers, user_id, key_id, sku_id = await new_user(client, sm, "13900000304")
        resp = await client.post(
            "/api/v1/instances",
            json={
                "sku_id": sku_id,
                "gpu_count": 1,
                "image_ref": IMAGE,
                "ssh_key_ids": [key_id],
            },
            headers=headers,
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["workload_type"] == "dev"
        assert resp.json()["with_ssh"] is True
        await drain_strict(sm)
        spec = fake.pods[(f"tenant-{user_id}", resp.json()["uuid"])].spec
        assert spec.restart_policy == "Never"
        assert spec.service_port is None
        assert spec.service_host is None
        assert spec.command is None and spec.args is None
        assert spec.with_ssh is True and spec.ssh_node_port is not None
        assert "JUPYTER_TOKEN" in spec.secret_env
        assert "JUPYTER_ALLOW_ORIGIN" in spec.env


class TestEnvHandling:
    async def test_secret_env_never_reaches_pod_spec(self, client, sm, fake):
        """标为密文的 env 只走 Secret,明文项才进 Pod spec。

        挂了说明用户的 HF_TOKEN 落进了 Pod spec —— 那份 spec 会进 etcd 与审计快照,
        任何 pods:get 身份(含只读 SA)都读得到。
        """
        _headers, uuid, user_id = await provision_service(
            client,
            sm,
            fake,
            phone="13900000310",
            env={"MAX_MODEL_LEN": "8192", "HF_TOKEN": "hf_super_secret"},
            env_secret_keys=["HF_TOKEN"],
        )
        spec = fake.pods[(f"tenant-{user_id}", uuid)].spec
        assert spec.env == {"MAX_MODEL_LEN": "8192"}
        assert spec.secret_env == {"HF_TOKEN": "hf_super_secret"}
        assert "hf_super_secret" not in str(spec.env)

    async def test_env_is_ciphertext_in_db(self, client, sm, fake):
        """env 整包落密文,键名与值都不出现在库里的那一列。"""
        _headers, uuid, _user_id = await provision_service(
            client,
            sm,
            fake,
            phone="13900000311",
            env={"MAX_MODEL_LEN": "8192", "HF_TOKEN": "hf_super_secret"},
            env_secret_keys=["HF_TOKEN"],
        )
        instance = await load_instance(sm, uuid)
        blob = instance.env_encrypted
        assert blob is not None and blob.startswith("enc:v1:")
        assert "HF_TOKEN" not in blob and "hf_super_secret" not in blob
        # AAD 绑实例 uuid:密文不能跨实例搬运
        plain, secret = service.instance_env(instance)
        assert plain == {"MAX_MODEL_LEN": "8192"}
        assert secret == {"HF_TOKEN": "hf_super_secret"}
        # 断言具体的 InvalidTag 而不是裸 Exception:换个 AAD 必须是**认证失败**,
        # 而不是解析报错——后者说明密文格式变了,那是另一类 bug,不该被这条用例吞掉
        with pytest.raises(InvalidTag):
            decrypt_str(blob, aad="instance-env:0" * 4)

    async def test_no_env_stays_null(self, client, sm, fake):
        """不传 env 的服务实例 env_encrypted 为空,不落一个空密文占位。"""
        _headers, uuid, _ = await provision_service(client, sm, fake, phone="13900000312")
        assert (await load_instance(sm, uuid)).env_encrypted is None


class TestListView:
    """列表页要能内联 slug —— 且**不为它多打接口**。

    挂了说明:整个实例列表页 500,或 slug 没下发、前端只能退回逐行打 /service
    (接口调用不得随行数放大)。`_attach_service_slugs` 只在列表里有服务型实例时才执行,
    其它用例清一色 dev 实例盖不到这条路径。
    """

    async def test_service_instance_lists_with_slug(self, client, sm, fake):
        headers, uuid, _ = await provision_service(client, sm, fake, phone="13900000470")
        resp = await client.get("/api/v1/instances", headers=headers)
        assert resp.status_code == 200, resp.text
        row = next(i for i in resp.json()["items"] if i["uuid"] == uuid)
        assert row["workload_type"] == "service"
        assert row["service_slug"] and row["service_slug"].startswith("svc-")
        # 与端点接口给的是同一个 slug(两条路径不能各说各的)
        endpoint = (await client.get(f"/api/v1/instances/{uuid}/service", headers=headers)).json()
        assert row["service_slug"] == endpoint["slug"]

    async def test_dev_instance_has_no_slug(self, client, sm, fake):
        headers, _user_id, key_id, sku_id = await new_user(client, sm, "13900000471")
        resp = await client.post(
            "/api/v1/instances",
            json={
                "sku_id": sku_id,
                "gpu_count": 1,
                "image_ref": IMAGE,
                "ssh_key_ids": [key_id],
            },
            headers=headers,
        )
        assert resp.status_code == 202, resp.text
        listed = (await client.get("/api/v1/instances", headers=headers)).json()["items"]
        assert [i["service_slug"] for i in listed] == [None]


class TestPinnedImage:
    """服务镜像必须钉死版本(latest / 无 tag 一律拒)。

    挂了说明:一台对外服务能用可变 tag 建出来。服务容器 restartPolicy=Always,一次原地重启
    就会换成另一个版本,而实例状态、事件流水、账单都看不出变化。开发机不适用。
    """

    @pytest.mark.parametrize(
        "image",
        ["registry.example.com/vllm:latest", "registry.example.com/vllm"],
    )
    async def test_service_rejects_mutable_tag(self, client, sm, fake, image):
        headers, _user_id, _key_id, sku_id = await new_user(client, sm, "13900000460")
        resp = await client.post(
            "/api/v1/instances", json=service_body(sku_id, image_ref=image), headers=headers
        )
        # 400 而不是 422:这条判据在 service 层(要读平台配置的白名单),
        # 走的是 AppError 而不是 pydantic 契约层
        assert resp.status_code == 400, resp.text
        assert resp.json()["message_key"] == "orchestrator.imageRefNotPinned"

    @pytest.mark.parametrize(
        "image",
        [
            "registry.example.com/vllm:v0.6.3",
            "registry.example.com/vllm@sha256:" + "a" * 64,
            "registry.example.com:5000/vllm:v1",  # 冒号也出现在仓库端口里,别把它当 tag
        ],
    )
    async def test_service_accepts_pinned(self, client, sm, fake, image):
        headers, _user_id, _key_id, sku_id = await new_user(client, sm, "13900000461")
        resp = await client.post(
            "/api/v1/instances", json=service_body(sku_id, image_ref=image), headers=headers
        )
        assert resp.status_code == 202, resp.text

    async def test_dev_still_accepts_latest(self, client, sm, fake):
        """开发机不受这条约束:它是 restartPolicy=Never + 用户手动重开,
        不存在「无人值守地换了版本」这条路径。"""
        headers, _user_id, key_id, sku_id = await new_user(client, sm, "13900000462")
        resp = await client.post(
            "/api/v1/instances",
            json={
                "sku_id": sku_id,
                "gpu_count": 1,
                "image_ref": "registry.example.com/pytorch:latest",
                "ssh_key_ids": [key_id],
            },
            headers=headers,
        )
        assert resp.status_code == 202, resp.text


class TestCreateContract:
    """契约矩阵。每条挂了都意味着一类错误配置能被建出来。"""

    @pytest.mark.parametrize(
        "field,value",
        [
            ("container_command", ["sleep"]),
            ("container_args", ["1"]),
            ("env", {"A": "1"}),
            ("env_secret_keys", []),
            ("service_port", 8000),
            ("health_path", "/health"),
            ("require_api_key", True),  # 传的就是默认值,也必须拒
            ("with_ssh", False),  # 同上:按值判会把它静默放过
        ],
    )
    async def test_dev_rejects_service_fields(self, client, sm, field, value):
        """dev 传服务字段一律 422,不静默忽略。

        挂了说明用户以为「启动命令已生效」,而实例跑的是镜像原样。
        """
        headers, _user_id, key_id, sku_id = await new_user(client, sm, "13900000320")
        resp = await client.post(
            "/api/v1/instances",
            json={
                "sku_id": sku_id,
                "image_ref": IMAGE,
                "ssh_key_ids": [key_id],
                field: value,
            },
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        assert field in resp.text

    async def test_service_requires_port(self, client, sm):
        headers, _user_id, _key_id, sku_id = await new_user(client, sm, "13900000321")
        body = service_body(sku_id)
        del body["service_port"]
        resp = await client.post("/api/v1/instances", json=body, headers=headers)
        assert resp.status_code == 422, resp.text

    @pytest.mark.parametrize("port", [22, 8888])
    async def test_reserved_ports_rejected(self, client, sm, port):
        """22 = sshd,8888 = JupyterLab:放行会让服务 Service 与平台入口撞 targetPort。"""
        headers, _user_id, _key_id, sku_id = await new_user(client, sm, "13900000322")
        resp = await client.post(
            "/api/v1/instances", json=service_body(sku_id, service_port=port), headers=headers
        )
        assert resp.status_code == 422, resp.text

    async def test_health_path_needs_leading_slash(self, client, sm):
        headers, _user_id, _key_id, sku_id = await new_user(client, sm, "13900000324")
        resp = await client.post(
            "/api/v1/instances",
            json=service_body(sku_id, health_path="health"),
            headers=headers,
        )
        assert resp.status_code == 422, resp.text

    @pytest.mark.parametrize(
        "name",
        ["JUPYTER_TOKEN", "SUPERDL_ANYTHING", "AUTHORIZED_KEYS", "1BAD", "BAD-KEY", "with space"],
    )
    async def test_env_key_blacklist(self, client, sm, name):
        """平台注入项与非法标识符逐条拒。

        放行 JUPYTER_/SUPERDL_/AUTHORIZED_KEYS = 用户能覆盖平台往容器里注入的东西。
        """
        headers, _user_id, _key_id, sku_id = await new_user(client, sm, "13900000325")
        resp = await client.post(
            "/api/v1/instances", json=service_body(sku_id, env={name: "x"}), headers=headers
        )
        assert resp.status_code == 422, resp.text

    async def test_env_secret_keys_must_be_subset(self, client, sm):
        headers, _user_id, _key_id, sku_id = await new_user(client, sm, "13900000326")
        resp = await client.post(
            "/api/v1/instances",
            json=service_body(sku_id, env={"A": "1"}, env_secret_keys=["B"]),
            headers=headers,
        )
        assert resp.status_code == 422, resp.text

    async def test_dev_still_requires_ssh_key(self, client, sm):
        """契约层 min_length 不设限,dev 的「至少一把公钥」由 model_validator 接住。

        挂了说明能建出一台谁也登不上去的开发机(镜像不收口令登录)。
        """
        headers, _user_id, _key_id, sku_id = await new_user(client, sm, "13900000327")
        resp = await client.post(
            "/api/v1/instances",
            json={"sku_id": sku_id, "image_ref": IMAGE, "ssh_key_ids": []},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text

    async def test_service_with_ssh_requires_ssh_key(self, client, sm):
        headers, _user_id, _key_id, sku_id = await new_user(client, sm, "13900000328")
        resp = await client.post(
            "/api/v1/instances",
            json=service_body(sku_id, with_ssh=True, ssh_key_ids=[]),
            headers=headers,
        )
        assert resp.status_code == 422, resp.text


class TestServiceEndpointApi:
    async def test_service_view(self, client, sm, fake):
        headers, uuid, _user_id = await provision_service(
            client, sm, fake, phone="13900000330", health_path="/healthz"
        )
        resp = await client.get(f"/api/v1/instances/{uuid}/service", headers=headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["url"] == f"https://{data['slug']}.{get_settings().service_domain_suffix}"
        assert data["container_port"] == 8000
        assert data["health_path"] == "/healthz"
        assert data["require_api_key"] is True
        assert data["ready"] is True

    async def test_dev_instance_has_no_service_endpoint(self, client, sm, fake):
        headers, _user_id, key_id, sku_id = await new_user(client, sm, "13900000331")
        resp = await client.post(
            "/api/v1/instances",
            json={"sku_id": sku_id, "image_ref": IMAGE, "ssh_key_ids": [key_id]},
            headers=headers,
        )
        uuid = resp.json()["uuid"]
        resp = await client.get(f"/api/v1/instances/{uuid}/service", headers=headers)
        assert resp.status_code == 404, resp.text
        assert resp.json()["code"] == "SERVICE_ENDPOINT_NOT_FOUND"

    async def test_access_shape_per_workload(self, client, sm, fake):
        """接入信息按形态给字段:服务实例没有 Jupyter,也没有 SSH(未勾选时)。"""
        headers, uuid, _ = await provision_service(client, sm, fake, phone="13900000332")
        resp = await client.get(f"/api/v1/instances/{uuid}/access", headers=headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["jupyter_url"] is None
        assert data["ssh_host"] is None and data["ssh_port"] is None
        assert data["endpoint_url"].startswith("https://svc-")

    async def test_access_service_with_ssh_has_both(self, client, sm, fake):
        headers, uuid, _ = await provision_service(
            client, sm, fake, phone="13900000333", with_ssh=True
        )
        data = (await client.get(f"/api/v1/instances/{uuid}/access", headers=headers)).json()
        assert data["ssh_command"].startswith("ssh root@")
        assert data["endpoint_url"].startswith("https://svc-")
        assert data["jupyter_url"] is None


class TestApiKeyCrud:
    async def test_plaintext_only_once(self, client, sm, fake):
        """明文只在创建响应出现;列表接口永远回不出明文(库里就没有)。"""
        headers, uuid, _ = await provision_service(client, sm, fake, phone="13900000340")
        resp = await client.post(
            f"/api/v1/instances/{uuid}/api-keys", json={"name": "prod"}, headers=headers
        )
        assert resp.status_code == 201, resp.text
        created = resp.json()
        assert created["key"].startswith("sk-")
        assert created["key_prefix"] == created["key"][:11]
        assert created["revoked_at"] is None and created["last_used_at"] is None

        listed = (await client.get(f"/api/v1/instances/{uuid}/api-keys", headers=headers)).json()
        assert len(listed) == 1
        assert "key" not in listed[0]
        assert listed[0]["key_prefix"] == created["key_prefix"]

    async def test_revoke_writes_timestamp_and_is_idempotent(self, client, sm, fake):
        """吊销写 revoked_at 不删行:谁在什么时候吊销了哪把,得留得下来。"""
        headers, uuid, _ = await provision_service(client, sm, fake, phone="13900000341")
        key_id = (
            await client.post(
                f"/api/v1/instances/{uuid}/api-keys", json={"name": "k"}, headers=headers
            )
        ).json()["id"]
        first = await client.delete(f"/api/v1/instances/{uuid}/api-keys/{key_id}", headers=headers)
        assert first.status_code == 200, first.text
        assert first.json()["revoked_at"] is not None
        second = await client.delete(f"/api/v1/instances/{uuid}/api-keys/{key_id}", headers=headers)
        assert second.status_code == 200
        assert second.json()["revoked_at"] == first.json()["revoked_at"]
        # 行还在,列表仍看得到
        listed = (await client.get(f"/api/v1/instances/{uuid}/api-keys", headers=headers)).json()
        assert len(listed) == 1

    async def test_other_users_instance_is_404(self, client, sm, fake):
        """非属主一律 404(不暴露存在性),不是 403。"""
        _, uuid, _ = await provision_service(client, sm, fake, phone="13900000342")
        other_headers, *_ = await new_user(client, sm, "13900000343")
        for call in (
            client.get(f"/api/v1/instances/{uuid}/service", headers=other_headers),
            client.get(f"/api/v1/instances/{uuid}/api-keys", headers=other_headers),
            client.post(
                f"/api/v1/instances/{uuid}/api-keys", json={"name": "x"}, headers=other_headers
            ),
        ):
            assert (await call).status_code == 404

    async def test_key_quota(self, client, sm, fake):
        """单实例密钥上限:密钥行永不删,没有上限就是一条无限追加写的口子。"""
        headers, uuid, _ = await provision_service(client, sm, fake, phone="13900000344")
        for i in range(service.MAX_API_KEYS_PER_INSTANCE):
            resp = await client.post(
                f"/api/v1/instances/{uuid}/api-keys", json={"name": f"k{i}"}, headers=headers
            )
            assert resp.status_code == 201, resp.text
        over = await client.post(
            f"/api/v1/instances/{uuid}/api-keys", json={"name": "over"}, headers=headers
        )
        assert over.status_code == 400
        assert over.json()["message_key"] == "orchestrator.apiKeyQuota"


class TestSlugHostParsing:
    """Host → slug 反解。它是鉴权链路的第一环,解错等于整条链路对不上号。"""

    def test_matches_service_suffix_only(self):
        suffix = get_settings().service_domain_suffix
        assert service.endpoint_slug_from_host(f"svc-abc123.{suffix}") == "svc-abc123"
        # 带端口(网关回调里 Host 常带 :443)
        assert service.endpoint_slug_from_host(f"svc-abc123.{suffix}:443") == "svc-abc123"
        # 大小写与结尾点(FQDN 写法)
        assert service.endpoint_slug_from_host(f"SVC-ABC123.{suffix.upper()}.") == "svc-abc123"

    def test_rejects_other_domains(self):
        # Jupyter 域不能当端点别名:否则 <uuid>.app.<域名> 成了鉴权端点的第二个入口
        assert service.endpoint_slug_from_host(f"x.{get_settings().jupyter_domain_suffix}") is None
        assert service.endpoint_slug_from_host("evil.example.com") is None
        assert service.endpoint_slug_from_host(None) is None
        # 多段左标签不是平台签发的形态
        suffix = get_settings().service_domain_suffix
        assert service.endpoint_slug_from_host(f"a.svc-abc123.{suffix}") is None


class TestServiceInstanceReusesLifecycle:
    async def test_stop_start_keeps_endpoint(self, client, sm, fake):
        """关机再开机端点不变:slug 是用户贴出去的地址,换一次等于服务下线。"""
        headers, uuid, user_id = await provision_service(client, sm, fake, phone="13900000350")
        before = (await client.get(f"/api/v1/instances/{uuid}/service", headers=headers)).json()

        assert (await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)).status_code
        await drain(sm)
        await reconcile_once(sm)
        assert (await client.post(f"/api/v1/instances/{uuid}/start", headers=headers)).status_code
        await drain_strict(sm)
        fake.mark_ready(f"tenant-{user_id}", uuid)
        await reconcile_once(sm)

        after = (await client.get(f"/api/v1/instances/{uuid}/service", headers=headers)).json()
        assert after["slug"] == before["slug"]
        # 重新开机也不占端口池
        async with sm() as session:
            assert list((await session.execute(select(PortAllocation))).scalars()) == []
