"""在线服务聚合根:部署 / 视图 / 生命周期 / 密钥 / 幂等 / 契约 / 与实例层的边界。

每条用例对应一处不变量:
- 部署落库形态:挂了说明服务与版本实例的归属断了(实例不知道自己属于哪个服务),
  或内部 uuid 漏进了公网域名
- 端口池 / env 密文 / Pod spec 分叉:挂了说明服务的版本实例白占 NodePort、密钥落进 etcd,
  或开发机的 Pod spec 被服务分支改坏
- 实例层边界:挂了说明 DELETE /instances 能把服务打成悬空,或服务实例混进实例列表
- 幂等:挂了说明响应丢失后重提会部署出第二个服务(两份 GPU 时费)
- 派生状态表:挂了说明前端对着错的状态给按钮(对已删除的服务显示「停止」)
"""

import asyncio
from typing import Any

import pytest
from cryptography.exceptions import InvalidTag
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.crypto import decrypt_str
from app.modules.orchestrator import service as orchestrator_service
from app.modules.orchestrator.models import Instance, PortAllocation
from app.modules.orchestrator.reconciler import reconcile_once
from app.modules.orchestrator.schemas import InstanceCreate
from app.modules.services import service
from app.modules.services.models import Service, ServiceApiKey
from app.modules.services.schemas import ServiceCreate
from app.modules.services.state import derive_status
from tests.helpers import drain, drain_strict, new_user, provision_service, service_body

pytestmark = pytest.mark.usefixtures("fake")

IMAGE = "registry.superdl.local/vllm:0.11.0"


async def load_instance(sm, uuid: str) -> Instance:
    async with sm() as session:
        return (await session.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()


async def load_service(sm, slug: str) -> Service:
    async with sm() as session:
        return (
            await session.execute(select(Service).where(Service.public_slug == slug))
        ).scalar_one()


class TestDeploy:
    async def test_service_row_and_instance_snapshot(self, client, sm, fake):
        """部署落一行 services + 一台版本实例;slug 是随机 base32 而非实例 uuid;
        暴露规格快照在实例行上(建 Pod 只读它)。"""
        _headers, svc, _ = await provision_service(client, sm, fake, health_path="/health")
        assert svc["slug"].startswith("svc-") and len(svc["slug"]) == 14
        inst = svc["current_instance"]
        assert inst["uuid"] not in svc["slug"]
        assert svc["url"] == f"https://{svc['slug']}.{get_settings().service_domain_suffix}"
        assert svc["status"] == "running" and svc["ready"] is True
        assert svc["revision"] == 1 and svc["desired_state"] == "running"
        assert svc["container"]["service_port"] == 8000
        assert svc["container"]["health_path"] == "/health"
        row = await load_service(sm, svc["slug"])
        instance = await load_instance(sm, inst["uuid"])
        assert row.current_instance_id == instance.id and row.rollout_instance_id is None
        assert instance.service_id == row.id and instance.service_revision == 1
        assert instance.service_slug == svc["slug"]
        assert instance.service_port == 8000 and instance.health_path == "/health"
        assert instance.workload_type == "service"
        # 实例与服务同名:账单 / 管理端实例表里认得出这台是谁
        assert instance.name == svc["name"] == inst["name"]

    async def test_no_ssh_means_no_port_pool_slot(self, client, sm, fake):
        """with_ssh=False 不进端口池:端口池 30000–32767 是全平台硬上限。"""
        _headers, svc, user_id = await provision_service(client, sm, fake)
        uuid = svc["current_instance"]["uuid"]
        instance = await load_instance(sm, uuid)
        assert instance.with_ssh is False and instance.ssh_port is None
        async with sm() as session:
            assert list((await session.execute(select(PortAllocation))).scalars()) == []
        spec = fake.pods[(f"tenant-{user_id}", uuid)].spec
        assert spec.with_ssh is False and spec.ssh_node_port is None
        assert spec.authorized_keys == ()

    async def test_with_ssh_still_allocates_port(self, client, sm, fake):
        """勾了 SSH 的服务照旧占端口池:两条分支必须都活着。"""
        _headers, svc, user_id = await provision_service(
            client, sm, fake, phone="13900000302", with_ssh=True
        )
        uuid = svc["current_instance"]["uuid"]
        instance = await load_instance(sm, uuid)
        assert instance.with_ssh is True and instance.ssh_port is not None
        spec = fake.pods[(f"tenant-{user_id}", uuid)].spec
        assert spec.with_ssh is True and spec.ssh_node_port == instance.ssh_port
        assert len(spec.authorized_keys) == 1
        assert svc["container"]["with_ssh"] is True

    async def test_pod_spec_service_fork(self, client, sm, fake):
        """服务版本实例的 Pod spec:Always 重启 + 用户启动命令 + 对外 Service + 探针。
        restartPolicy 写成 Never 时,用户容器崩一次就把实例判终结。"""
        _headers, svc, user_id = await provision_service(
            client,
            sm,
            fake,
            phone="13900000303",
            container_command=["python", "-m", "vllm.entrypoints.openai.api_server"],
            container_args=["--port", "8000"],
            health_path="/health",
        )
        spec = fake.pods[(f"tenant-{user_id}", svc["current_instance"]["uuid"])].spec
        assert spec.restart_policy == "Always"
        assert spec.command == ("python", "-m", "vllm.entrypoints.openai.api_server")
        assert spec.args == ("--port", "8000")
        assert spec.service_port == 8000 and spec.health_path == "/health"
        assert spec.service_host == f"{svc['slug']}.{get_settings().service_domain_suffix}"
        # 服务容器不跑 Jupyter:token 不进 Secret,也不注入 JUPYTER_ALLOW_ORIGIN
        assert "JUPYTER_TOKEN" not in spec.secret_env
        assert "JUPYTER_ALLOW_ORIGIN" not in spec.env

    async def test_dev_fork_unchanged(self, client, sm, fake):
        """dev 形态逐字不变:Never + 无对外 Service + Jupyter token 走 Secret。"""
        headers, user_id, key_id, sku_id = await new_user(client, sm, "13900000304")
        resp = await client.post(
            "/api/v1/instances",
            json={"sku_id": sku_id, "gpu_count": 1, "image_ref": IMAGE, "ssh_key_ids": [key_id]},
            headers=headers,
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["workload_type"] == "dev" and resp.json()["with_ssh"] is True
        assert resp.json()["service_slug"] is None
        await drain_strict(sm)
        spec = fake.pods[(f"tenant-{user_id}", resp.json()["uuid"])].spec
        assert spec.restart_policy == "Never"
        assert spec.service_port is None and spec.service_host is None
        assert spec.command is None and spec.args is None
        assert spec.with_ssh is True and spec.ssh_node_port is not None
        assert "JUPYTER_TOKEN" in spec.secret_env and "JUPYTER_ALLOW_ORIGIN" in spec.env

    async def test_other_users_service_is_404(self, client, sm, fake):
        """非属主一律 404(不暴露存在性),不是 403。"""
        _, svc, _ = await provision_service(client, sm, fake, phone="13900000342")
        other, *_ = await new_user(client, sm, "13900000343")
        slug = svc["slug"]
        for call in (
            client.get(f"/api/v1/services/{slug}", headers=other),
            client.get(f"/api/v1/services/{slug}/api-keys", headers=other),
            client.post(f"/api/v1/services/{slug}/api-keys", json={"name": "x"}, headers=other),
            client.post(f"/api/v1/services/{slug}/stop", headers=other),
            client.delete(f"/api/v1/services/{slug}", headers=other),
        ):
            assert (await call).status_code == 404


class TestInstanceBoundary:
    """服务的版本实例由服务驱动:实例层看得见,但改不了它的生命周期。"""

    async def test_instance_list_excludes_service_instances(self, client, sm, fake):
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000470")
        uuid = svc["current_instance"]["uuid"]
        listed = (await client.get("/api/v1/instances", headers=headers)).json()["items"]
        assert uuid not in [i["uuid"] for i in listed]
        # 只读端点仍可达(账单 / 监控按实例查)
        detail = await client.get(f"/api/v1/instances/{uuid}", headers=headers)
        assert detail.status_code == 200 and detail.json()["service_slug"] == svc["slug"]

    async def test_instance_lifecycle_endpoints_reject_service_instance(self, client, sm, fake):
        """DELETE / stop / start / restart 打到服务实例一律 409:否则服务会被打成悬空。"""
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000471")
        uuid = svc["current_instance"]["uuid"]
        for call in (
            client.post(f"/api/v1/instances/{uuid}/stop", headers=headers),
            client.post(f"/api/v1/instances/{uuid}/restart", headers=headers),
            client.post(f"/api/v1/instances/{uuid}/reset-jupyter-token", headers=headers),
            client.delete(f"/api/v1/instances/{uuid}", headers=headers),
        ):
            resp = await call
            assert resp.status_code == 409, resp.text
            assert resp.json()["message_key"] == "orchestrator.serviceInstanceLifecycle"

    async def test_dev_create_rejects_service_fields(self, client, sm, fake):
        """POST /instances 不再收服务字段:静默忽略会让用户以为「启动命令已生效」。"""
        headers, _user_id, key_id, sku_id = await new_user(client, sm, "13900000472")
        for extra in ({"service_port": 8000}, {"workload_type": "service"}, {"env": {"A": "1"}}):
            resp = await client.post(
                "/api/v1/instances",
                json={"sku_id": sku_id, "image_ref": IMAGE, "ssh_key_ids": [key_id], **extra},
                headers=headers,
            )
            assert resp.status_code == 422, resp.text

    async def test_access_shape(self, client, sm, fake):
        """实例接入信息按形态给字段:服务实例给端点 URL,不给 Jupyter;不开 SSH 时也不给 SSH。"""
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000332")
        data = (
            await client.get(
                f"/api/v1/instances/{svc['current_instance']['uuid']}/access", headers=headers
            )
        ).json()
        assert data["jupyter_url"] is None
        assert data["ssh_host"] is None and data["ssh_port"] is None
        assert data["endpoint_url"] == svc["url"]


class TestEnvHandling:
    async def test_secret_env_never_reaches_pod_spec(self, client, sm, fake):
        """标为密文的 env 只走 Secret,明文项才进 Pod spec。"""
        _headers, svc, user_id = await provision_service(
            client,
            sm,
            fake,
            phone="13900000310",
            env={"MAX_MODEL_LEN": "8192", "HF_TOKEN": "hf_super_secret"},
            env_secret_keys=["HF_TOKEN"],
        )
        spec = fake.pods[(f"tenant-{user_id}", svc["current_instance"]["uuid"])].spec
        assert spec.env == {"MAX_MODEL_LEN": "8192"}
        assert spec.secret_env == {"HF_TOKEN": "hf_super_secret"}
        # 视图只回明文项的值,密文项只回键名
        assert svc["container"]["env"] == {"MAX_MODEL_LEN": "8192"}
        assert svc["container"]["env_secret_keys"] == ["HF_TOKEN"]

    async def test_env_is_ciphertext_in_db(self, client, sm, fake):
        """env 整包落密文,键名与值都不出现在库里的那一列;AAD 绑实例 uuid。"""
        _headers, svc, _ = await provision_service(
            client,
            sm,
            fake,
            phone="13900000311",
            env={"MAX_MODEL_LEN": "8192", "HF_TOKEN": "hf_super_secret"},
            env_secret_keys=["HF_TOKEN"],
        )
        instance = await load_instance(sm, svc["current_instance"]["uuid"])
        blob = instance.env_encrypted
        assert blob is not None and blob.startswith("enc:v2:")
        assert "HF_TOKEN" not in blob and "hf_super_secret" not in blob
        plain, secret = orchestrator_service.instance_env(instance)
        assert plain == {"MAX_MODEL_LEN": "8192"} and secret == {"HF_TOKEN": "hf_super_secret"}
        with pytest.raises(InvalidTag):
            decrypt_str(blob, aad="instance-env:0" * 4)

    async def test_no_env_stays_null(self, client, sm, fake):
        _headers, svc, _ = await provision_service(client, sm, fake, phone="13900000312")
        assert (await load_instance(sm, svc["current_instance"]["uuid"])).env_encrypted is None


class TestPinnedImage:
    """服务镜像必须钉死版本:restartPolicy=Always 下一次原地重启就会换成另一个版本。"""

    @pytest.mark.parametrize(
        "image", ["registry.example.com/vllm:latest", "registry.example.com/vllm"]
    )
    async def test_rejects_mutable_tag(self, client, sm, fake, image):
        headers, *_, sku_id = await new_user(client, sm, "13900000460")
        resp = await client.post(
            "/api/v1/services", json=service_body(sku_id, image_ref=image), headers=headers
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["message_key"] == "orchestrator.imageRefNotPinned"
        # 失败的部署不留孤儿 services 行
        async with sm() as session:
            assert (
                await session.execute(select(func.count()).select_from(Service))
            ).scalar_one() == 0

    @pytest.mark.parametrize(
        "image",
        [
            "registry.example.com/vllm:v0.6.3",
            "registry.example.com/vllm@sha256:" + "a" * 64,
            "registry.example.com:5000/vllm:v1",
        ],
    )
    async def test_accepts_pinned(self, client, sm, fake, image):
        headers, *_, sku_id = await new_user(client, sm, "13900000461")
        resp = await client.post(
            "/api/v1/services", json=service_body(sku_id, image_ref=image), headers=headers
        )
        assert resp.status_code == 202, resp.text


class TestCreateContract:
    """契约矩阵(纯 schema):每条挂了都意味着一类错误配置能被部署出来。"""

    def test_service_requires_port(self):
        body = service_body(1)
        del body["service_port"]
        with pytest.raises(ValidationError):
            ServiceCreate(**body)

    @pytest.mark.parametrize("port", [22, 8888])
    def test_reserved_ports_rejected(self, port):
        with pytest.raises(ValidationError):
            ServiceCreate(**service_body(1, service_port=port))

    def test_health_path_needs_leading_slash(self):
        with pytest.raises(ValidationError):
            ServiceCreate(**service_body(1, health_path="health"))

    @pytest.mark.parametrize(
        "name",
        [
            "JUPYTER_TOKEN",
            "SUPERDL_ANYTHING",
            "AUTHORIZED_KEYS",
            "NVIDIA_VISIBLE_DEVICES",
            "NVIDIA_DRIVER_CAPABILITIES",
            "1BAD",
            "BAD-KEY",
            "with space",
        ],
    )
    def test_env_key_blacklist(self, name):
        with pytest.raises(ValidationError):
            ServiceCreate(**service_body(1, env={name: "x"}))

    def test_env_secret_keys_must_be_subset(self):
        with pytest.raises(ValidationError):
            ServiceCreate(**service_body(1, env={"A": "1"}, env_secret_keys=["B"]))

    def test_with_ssh_requires_ssh_key(self):
        with pytest.raises(ValidationError):
            ServiceCreate(**service_body(1, with_ssh=True, ssh_key_ids=[]))

    def test_subscription_requires_period(self):
        with pytest.raises(ValidationError):
            ServiceCreate(**service_body(1, market="subscription"))

    def test_dev_still_requires_ssh_key(self):
        """开发机只有密钥登录:一把公钥都不选 = 建出一台谁也登不上去的实例。"""
        with pytest.raises(ValidationError):
            InstanceCreate(sku_id=1, image_ref=IMAGE, ssh_key_ids=[])


class TestIdempotency:
    async def test_same_key_replays_same_service(self, client, sm, fake):
        """同键同参重提回 200 + X-Idempotent-Replay 与同一个服务;库里只有一行。"""
        headers, *_, sku_id = await new_user(client, sm, "13900000480")
        h = {**headers, "Idempotency-Key": "deploy-1"}
        first = await client.post("/api/v1/services", json=service_body(sku_id), headers=h)
        assert first.status_code == 202, first.text
        second = await client.post("/api/v1/services", json=service_body(sku_id), headers=h)
        assert second.status_code == 200, second.text
        assert second.headers.get("x-idempotent-replay") == "true"
        assert second.json()["slug"] == first.json()["slug"]
        async with sm() as session:
            assert (
                await session.execute(select(func.count()).select_from(Service))
            ).scalar_one() == 1

    async def test_same_key_different_params_is_409(self, client, sm, fake):
        headers, *_, sku_id = await new_user(client, sm, "13900000481")
        h = {**headers, "Idempotency-Key": "deploy-2"}
        assert (
            await client.post("/api/v1/services", json=service_body(sku_id), headers=h)
        ).status_code == 202
        again = await client.post(
            "/api/v1/services", json=service_body(sku_id, service_port=9000), headers=h
        )
        assert again.status_code == 409, again.text

    async def test_concurrent_same_key_deploys_once(self, client, sm, fake):
        """并发同键只落一个服务、无孤儿 services 行(撞库那一路连同自己的服务行一起回滚)。"""
        _headers, user_id, _key_id, sku_id = await new_user(client, sm, "13900000482")
        spec = ServiceCreate(**service_body(sku_id))

        async def deploy() -> str:
            async with sm() as session:
                svc, _ = await service.create_service(
                    session, user_id, spec=spec, idempotency_key="race"
                )
                return svc.public_slug

        slugs = await asyncio.gather(deploy(), deploy(), return_exceptions=True)
        ok = [s for s in slugs if isinstance(s, str)]
        assert len(ok) >= 1 and len(set(ok)) == 1, slugs
        async with sm() as session:
            assert (
                await session.execute(select(func.count()).select_from(Service))
            ).scalar_one() == 1
            assert (
                await session.execute(select(func.count()).select_from(Instance))
            ).scalar_one() == 1


class TestServiceApi:
    async def test_patch_name_and_auth_switch(self, client, sm, fake):
        """改名与鉴权开关只改 services 行:不重新部署,实例 uuid 与 slug 都不变。"""
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000330")
        resp = await client.patch(
            f"/api/v1/services/{svc['slug']}",
            json={"name": "renamed", "require_api_key": False},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "renamed" and resp.json()["require_api_key"] is False
        assert resp.json()["current_instance"]["uuid"] == svc["current_instance"]["uuid"]
        assert resp.json()["slug"] == svc["slug"]

    async def test_list_only_mine_and_not_released(self, client, sm, fake):
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000331")
        _, _other, _ = await provision_service(client, sm, fake, phone="13900000334")
        items = (await client.get("/api/v1/services", headers=headers)).json()["items"]
        assert [i["slug"] for i in items] == [svc["slug"]]
        # 按名字 / slug 前缀过滤
        by_slug = (
            await client.get("/api/v1/services", params={"name": svc["slug"][:6]}, headers=headers)
        ).json()["items"]
        assert [i["slug"] for i in by_slug] == [svc["slug"]]
        none = (
            await client.get("/api/v1/services", params={"status": "stopped"}, headers=headers)
        ).json()["items"]
        assert none == []

    async def test_events_revisions_bills(self, client, sm, fake):
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000335")
        slug = svc["slug"]
        events = (await client.get(f"/api/v1/services/{slug}/events", headers=headers)).json()
        assert events["items"], "部署至少产生 creating 与 running 两条事件"
        assert all(e["instance_uuid"] == svc["current_instance"]["uuid"] for e in events["items"])
        assert all(e["revision"] == 1 for e in events["items"])
        revisions = (await client.get(f"/api/v1/services/{slug}/revisions", headers=headers)).json()
        assert [r["uuid"] for r in revisions["items"]] == [svc["current_instance"]["uuid"]]
        bills = await client.get(f"/api/v1/services/{slug}/bills", headers=headers)
        assert bills.status_code == 200 and bills.json()["items"] == []


class TestLifecycle:
    async def test_stop_start_keeps_slug_and_instance(self, client, sm, fake):
        """停止 / 启动只动当前实例:slug 与 API Key 是用户贴出去的地址,换一次等于服务下线。"""
        headers, svc, user_id = await provision_service(client, sm, fake, phone="13900000350")
        slug = svc["slug"]
        stopped = await client.post(f"/api/v1/services/{slug}/stop", headers=headers)
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["status"] == "stopping"
        assert stopped.json()["desired_state"] == "stopped"
        await drain(sm)
        fake.finish_delete(f"tenant-{user_id}", svc["current_instance"]["uuid"])
        await reconcile_once(sm)
        svc2 = (await client.get(f"/api/v1/services/{slug}", headers=headers)).json()
        assert svc2["status"] == "stopped" and svc2["ready"] is False
        # 停机时再停 → 409(实例守卫的语言)
        assert (
            await client.post(f"/api/v1/services/{slug}/stop", headers=headers)
        ).status_code == 400

        started = await client.post(f"/api/v1/services/{slug}/start", headers=headers)
        assert started.status_code == 200, started.text
        assert started.json()["status"] == "deploying"
        await drain_strict(sm)
        fake.mark_ready(f"tenant-{user_id}", svc["current_instance"]["uuid"])
        await reconcile_once(sm)
        svc3 = (await client.get(f"/api/v1/services/{slug}", headers=headers)).json()
        assert svc3["status"] == "running" and svc3["desired_state"] == "running"
        assert svc3["current_instance"]["uuid"] == svc["current_instance"]["uuid"]
        async with sm() as session:
            assert list((await session.execute(select(PortAllocation))).scalars()) == []

    async def test_delete_requires_stopped_then_lands_released(self, client, sm, fake):
        """运行中不能直接删(409);停机后删除 = 释放实例 + 吊销全部密钥,实例 released 即服务终态。"""
        headers, svc, user_id = await provision_service(client, sm, fake, phone="13900000351")
        slug = svc["slug"]
        key = (
            await client.post(
                f"/api/v1/services/{slug}/api-keys", json={"name": "k"}, headers=headers
            )
        ).json()
        denied = await client.delete(f"/api/v1/services/{slug}", headers=headers)
        assert (
            denied.status_code == 409
            and denied.json()["message_key"] == "services.deleteNeedsStopped"
        )

        await client.post(f"/api/v1/services/{slug}/stop", headers=headers)
        await drain(sm)
        fake.finish_delete(f"tenant-{user_id}", svc["current_instance"]["uuid"])
        await reconcile_once(sm)
        deleted = await client.delete(f"/api/v1/services/{slug}", headers=headers)
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["status"] == "releasing"
        # 重复删除幂等
        assert (await client.delete(f"/api/v1/services/{slug}", headers=headers)).status_code == 200
        await drain(sm)
        await reconcile_once(sm)
        final = (await client.get(f"/api/v1/services/{slug}", headers=headers)).json()
        assert final["status"] == "released" and final["released_at"] is not None
        assert (await client.get("/api/v1/services", headers=headers)).json()["items"] == []
        keys = (await client.get(f"/api/v1/services/{slug}/api-keys", headers=headers)).json()
        assert keys[0]["id"] == key["id"] and keys[0]["revoked_at"] is not None
        # 已删除的服务不能再建钥 / 启动
        assert (
            await client.post(
                f"/api/v1/services/{slug}/api-keys", json={"name": "x"}, headers=headers
            )
        ).status_code == 409
        assert (
            await client.post(f"/api/v1/services/{slug}/start", headers=headers)
        ).status_code == 409


class TestDeriveStatus:
    """派生状态表:每一格挂了 = 前端对着错的状态给按钮。"""

    class _Svc:
        def __init__(self, released_at=None):
            self.released_at = released_at

    class _Inst:
        def __init__(self, status, unready=None):
            self.status = status
            self.unready_since = unready

    @pytest.mark.parametrize(
        "released,rollout,current,unready,expected,ready",
        [
            ("t", None, "running", None, "released", False),
            (None, "creating", "running", None, "deploying", False),
            (None, None, "creating", None, "deploying", False),
            (None, None, "starting", None, "deploying", False),
            (None, None, "running", None, "running", True),
            (None, None, "running", "t", "unready", False),
            (None, None, "stopping", None, "stopping", False),
            (None, None, "stopped", None, "stopped", False),
            (None, None, "frozen", None, "frozen", False),
            (None, None, "failed", None, "failed", False),
            (None, None, "releasing", None, "releasing", False),
            (None, None, None, None, "stopped", False),
        ],
    )
    def test_table(self, released, rollout, current, unready, expected, ready):
        svc: Any = self._Svc(released)
        cur: Any = self._Inst(current, unready) if current else None
        rol: Any = self._Inst(rollout) if rollout else None
        assert derive_status(svc, cur, rol) == (expected, ready)


class TestApiKeyCrud:
    async def test_plaintext_only_once(self, client, sm, fake):
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000340")
        slug = svc["slug"]
        resp = await client.post(
            f"/api/v1/services/{slug}/api-keys", json={"name": "prod"}, headers=headers
        )
        assert resp.status_code == 201, resp.text
        created = resp.json()
        assert created["key"].startswith("sk-") and created["key_prefix"] == created["key"][:11]
        listed = (await client.get(f"/api/v1/services/{slug}/api-keys", headers=headers)).json()
        assert len(listed) == 1 and "key" not in listed[0]

    async def test_revoke_writes_timestamp_and_is_idempotent(self, client, sm, fake):
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000341")
        slug = svc["slug"]
        key_id = (
            await client.post(
                f"/api/v1/services/{slug}/api-keys", json={"name": "k"}, headers=headers
            )
        ).json()["id"]
        first = await client.delete(f"/api/v1/services/{slug}/api-keys/{key_id}", headers=headers)
        assert first.status_code == 200 and first.json()["revoked_at"] is not None
        second = await client.delete(f"/api/v1/services/{slug}/api-keys/{key_id}", headers=headers)
        assert second.json()["revoked_at"] == first.json()["revoked_at"]
        assert (
            await client.delete(f"/api/v1/services/{slug}/api-keys/999999", headers=headers)
        ).status_code == 404

    async def test_key_quota(self, client, sm, fake):
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000344")
        slug = svc["slug"]
        for i in range(service.MAX_API_KEYS_PER_SERVICE):
            resp = await client.post(
                f"/api/v1/services/{slug}/api-keys", json={"name": f"k{i}"}, headers=headers
            )
            assert resp.status_code == 201, resp.text
        over = await client.post(
            f"/api/v1/services/{slug}/api-keys", json={"name": "over"}, headers=headers
        )
        assert over.status_code == 400 and over.json()["message_key"] == "services.apiKeyQuota"

    async def test_keys_belong_to_service_not_instance(self, client, sm, fake):
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000345")
        await client.post(
            f"/api/v1/services/{svc['slug']}/api-keys", json={"name": "k"}, headers=headers
        )
        row = await load_service(sm, svc["slug"])
        async with sm() as session:
            key = (await session.execute(select(ServiceApiKey))).scalar_one()
        assert key.service_id == row.id


class TestSlugHostParsing:
    """Host → slug 反解。它是鉴权链路的第一环,解错等于整条链路对不上号。"""

    def test_matches_service_suffix_only(self):
        suffix = get_settings().service_domain_suffix
        assert service.endpoint_slug_from_host(f"svc-abc123.{suffix}") == "svc-abc123"
        assert service.endpoint_slug_from_host(f"svc-abc123.{suffix}:443") == "svc-abc123"
        assert service.endpoint_slug_from_host(f"SVC-ABC123.{suffix.upper()}.") == "svc-abc123"

    def test_rejects_other_domains(self):
        assert service.endpoint_slug_from_host(f"x.{get_settings().jupyter_domain_suffix}") is None
        assert service.endpoint_slug_from_host("evil.example.com") is None
        assert service.endpoint_slug_from_host(None) is None
        suffix = get_settings().service_domain_suffix
        assert service.endpoint_slug_from_host(f"a.svc-abc123.{suffix}") is None
