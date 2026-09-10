"""在线服务版本更新(recreate):每条用例对应一处不变量。

- happy path:挂了说明翻转 / 释放链断了 —— 端点会指着已停的旧版本,或旧实例永远不释放(白付 GPU 时费)
- slug / URL / Key 不变:挂了说明用户贴出去的地址与凭据随更新失效(等于服务下线)
- 密文沿用:挂了说明前端拿不到明文却也无法「不改」密文,每次更新都得重填密钥
- 零重复扣款:挂了说明旧版本尾账出了两次
- 在途 / 未落定 / 包周期 409:挂了说明能叠着起两个候选版本,或包周期预付款被白丢
- 失败回退:挂了说明新版本起不来时服务卡在「部署中」,用户连回滚(启动上一版本)都不行
- 配额:挂了说明 max_instances=1 的用户永远换不了版本
- 幂等:挂了说明响应丢失后重提会起第三个版本
"""

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.modules.billing.models import BillHourly
from app.modules.notify.models import Notification
from app.modules.orchestrator.models import Instance, InstanceEvent
from app.modules.orchestrator.reconciler import reconcile_once
from app.modules.services.models import Service
from tests.helpers import drain, provision_service

pytestmark = pytest.mark.usefixtures("fake")

AUTH_URL = "/api/internal/v1/endpoint-auth"
V1_IMAGE = "registry.superdl.local/vllm:0.11.0"
V2_IMAGE = "registry.superdl.local/vllm:0.12.0"


def revision_body(svc: dict, **over) -> dict:
    body = {
        "sku_id": svc["current_instance"]["sku_id"],
        "gpu_count": svc["current_instance"]["gpu_count"],
        "image_ref": V2_IMAGE,
        "ssh_key_ids": [],
        "service_port": svc["container"]["service_port"],
        "health_path": svc["container"]["health_path"],
    }
    body.update(over)
    return body


async def _instance(sm, uuid: str) -> Instance:
    async with sm() as session:
        return (await session.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()


async def _settle_rollout(client, sm, fake, headers, svc: dict, rollout: dict) -> dict:
    """推进一次 recreate:旧版本关机 → 新版本就绪 → 翻转 → 释放旧版本。返回最终服务视图。"""
    ns = f"tenant-{svc['_user_id']}"
    old_uuid = svc["current_instance"]["uuid"]
    new_uuid = rollout["rollout_instance"]["uuid"]
    await drain(sm)  # instance.stop(旧) + instance.create(新)
    fake.finish_delete(ns, old_uuid)
    fake.mark_ready(ns, new_uuid)
    await reconcile_once(sm)  # 旧 → stopped;新 → running(监听器翻转 + 入队 retire)
    await drain(sm)  # service.retire → 旧 releasing
    await reconcile_once(sm)  # 旧 → released
    return (await client.get(f"/api/v1/services/{svc['slug']}", headers=headers)).json()


async def _provision(client, sm, fake, phone: str, **over):
    headers, svc, user_id = await provision_service(
        client,
        sm,
        fake,
        phone=phone,
        env={"MAX_MODEL_LEN": "4096", "HF_TOKEN": "hf_secret_v1"},
        env_secret_keys=["HF_TOKEN"],
        health_path="/health",
        **over,
    )
    svc["_user_id"] = user_id
    return headers, svc, user_id


class TestRecreate:
    async def test_happy_path_keeps_slug_key_and_secret(self, client, sm, fake):
        headers, svc, user_id = await _provision(client, sm, fake, "13900000401")
        slug = svc["slug"]
        old_uuid = svc["current_instance"]["uuid"]
        key = (
            await client.post(
                f"/api/v1/services/{slug}/api-keys", json={"name": "k"}, headers=headers
            )
        ).json()
        host = {"host": f"{slug}.{get_settings().service_domain_suffix}"}
        bearer = {**host, "authorization": f"Bearer {key['key']}"}

        resp = await client.post(
            f"/api/v1/services/{slug}/revisions",
            json=revision_body(svc, env={"MAX_MODEL_LEN": "8192"}, env_secret_keep=["HF_TOKEN"]),
            headers=headers,
        )
        assert resp.status_code == 202, resp.text
        rollout = resp.json()
        assert rollout["status"] == "deploying" and rollout["revision"] == 2
        assert rollout["current_instance"]["uuid"] == old_uuid
        new_uuid = rollout["rollout_instance"]["uuid"]
        assert new_uuid != old_uuid
        # 旧版本同事务关机,事件 reason 是 rollout 而不是 user_stop
        old = await _instance(sm, old_uuid)
        assert old.status == "stopping"
        async with sm() as session:
            reasons = list(
                (
                    await session.execute(
                        select(InstanceEvent.reason).where(InstanceEvent.instance_id == old.id)
                    )
                ).scalars()
            )
        assert reasons[-1] == "rollout"
        # 在途期间:再更新 / 停止 / 启动 / 删除全部 409
        for call in (
            client.post(
                f"/api/v1/services/{slug}/revisions", json=revision_body(svc), headers=headers
            ),
            client.post(f"/api/v1/services/{slug}/stop", headers=headers),
            client.post(f"/api/v1/services/{slug}/start", headers=headers),
            client.delete(f"/api/v1/services/{slug}", headers=headers),
        ):
            r = await call
            assert r.status_code == 409 and r.json()["message_key"] == "services.rolloutInFlight"

        final = await _settle_rollout(client, sm, fake, headers, svc, rollout)
        assert final["status"] == "running" and final["ready"] is True
        assert final["revision"] == 2 and final["rollout_instance"] is None
        assert final["current_instance"]["uuid"] == new_uuid
        assert final["slug"] == slug and final["url"] == svc["url"]
        # 密文沿用:新 Pod 的 Secret 里是 v1 的值;明文项是新值;回显仍只给键名
        pod_spec = fake.pods[(f"tenant-{user_id}", new_uuid)].spec
        assert pod_spec.secret_env["HF_TOKEN"] == "hf_secret_v1"
        assert pod_spec.env["MAX_MODEL_LEN"] == "8192"
        assert final["container"]["env_secret_keys"] == ["HF_TOKEN"]
        assert final["container"]["image_ref"] == V2_IMAGE
        # Key 跨版本存活:网关回调仍 200,且指向同一个 slug
        ok = await client.post(AUTH_URL, headers=bearer)
        assert ok.status_code == 200 and ok.headers["x-superdl-endpoint"] == slug
        # 旧版本已释放,reason 是 rollout_retire
        old = await _instance(sm, old_uuid)
        assert old.status == "released"
        async with sm() as session:
            old_reasons = list(
                (
                    await session.execute(
                        select(InstanceEvent.reason)
                        .where(InstanceEvent.instance_id == old.id)
                        .order_by(InstanceEvent.id)
                    )
                ).scalars()
            )
            # 零重复扣款:旧版本最多一条尾账
            old_bills = await session.scalar(
                select(func.count()).select_from(BillHourly).where(BillHourly.instance_id == old.id)
            )
            svc_row = (
                await session.execute(select(Service).where(Service.public_slug == slug))
            ).scalar_one()
        assert old_reasons[-2:] == ["rollout_retire", "released"]
        assert (old_bills or 0) <= 1
        assert svc_row.current_instance_id != old.id and svc_row.released_at is None
        # 版本历史两条(含已释放),降序;事件并集标出版本;实例列表仍不含服务实例
        revisions = (await client.get(f"/api/v1/services/{slug}/revisions", headers=headers)).json()
        assert [r["uuid"] for r in revisions["items"]] == [new_uuid, old_uuid]
        events = (await client.get(f"/api/v1/services/{slug}/events", headers=headers)).json()
        assert {e["revision"] for e in events["items"]} == {1, 2}
        assert (await client.get("/api/v1/instances", headers=headers)).json()["items"] == []

    async def test_retire_replay_is_noop(self, client, sm, fake):
        """service.retire 至少一次投递:旧版本已释放后再来一条,不能报错也不能动当前版本。"""
        headers, svc, _ = await _provision(client, sm, fake, "13900000402")
        rollout = (
            await client.post(
                f"/api/v1/services/{svc['slug']}/revisions",
                json=revision_body(svc),
                headers=headers,
            )
        ).json()
        final = await _settle_rollout(client, sm, fake, headers, svc, rollout)
        from app.core.outbox import enqueue

        async with sm() as session:
            svc_row = (
                await session.execute(select(Service).where(Service.public_slug == svc["slug"]))
            ).scalar_one()
            old = await _instance(sm, svc["current_instance"]["uuid"])
            enqueue(session, "service.retire", {"service_id": svc_row.id, "instance_id": old.id})
            # 指向当前版本的 retire 也必须是 no-op(翻转被回滚时会出现这种任务)
            enqueue(
                session,
                "service.retire",
                {"service_id": svc_row.id, "instance_id": svc_row.current_instance_id},
            )
            await session.commit()
        await drain(sm)
        again = (await client.get(f"/api/v1/services/{svc['slug']}", headers=headers)).json()
        assert again["status"] == "running"
        assert again["current_instance"]["uuid"] == final["current_instance"]["uuid"]


class TestGuards:
    async def test_subscription_and_unsettled_rejected(self, client, sm, fake):
        headers, svc, user_id = await _provision(client, sm, fake, "13900000403")
        slug = svc["slug"]
        sub = await client.post(
            f"/api/v1/services/{slug}/revisions",
            json=revision_body(svc, market="subscription", period="month"),
            headers=headers,
        )
        assert sub.status_code == 409
        assert sub.json()["message_key"] == "services.rolloutSubscriptionUnsupported"
        # 旧版本正在停止(未落定)→ 409 rolloutNeedsSettled
        await client.post(f"/api/v1/services/{slug}/stop", headers=headers)
        unsettled = await client.post(
            f"/api/v1/services/{slug}/revisions", json=revision_body(svc), headers=headers
        )
        assert unsettled.status_code == 409
        assert unsettled.json()["message_key"] == "services.rolloutNeedsSettled"
        # 沿用一个当前版本没有的密文键 → 400
        await drain(sm)
        fake.finish_delete(f"tenant-{user_id}", svc["current_instance"]["uuid"])
        await reconcile_once(sm)
        bad = await client.post(
            f"/api/v1/services/{slug}/revisions",
            json=revision_body(svc, env_secret_keep=["NOPE"]),
            headers=headers,
        )
        assert bad.status_code == 400 and bad.json()["message_key"] == "services.envKeepUnknown"
        # 停机的旧版本可以直接更新:不用先开机
        ok = await client.post(
            f"/api/v1/services/{slug}/revisions", json=revision_body(svc), headers=headers
        )
        assert ok.status_code == 202, ok.text
        old = await _instance(sm, svc["current_instance"]["uuid"])
        assert old.status == "stopped"  # 停机的旧版本不动,等新版本 running 后释放

    async def test_failed_rollout_keeps_old_and_allows_start(self, client, sm, fake):
        headers, svc, user_id = await _provision(client, sm, fake, "13900000404")
        slug = svc["slug"]
        old_uuid = svc["current_instance"]["uuid"]
        rollout = (
            await client.post(
                f"/api/v1/services/{slug}/revisions", json=revision_body(svc), headers=headers
            )
        ).json()
        new_uuid = rollout["rollout_instance"]["uuid"]
        await drain(sm)
        fake.finish_delete(f"tenant-{user_id}", old_uuid)
        settings = get_settings()
        saved = settings.creating_timeout_seconds
        settings.creating_timeout_seconds = 0
        try:
            await reconcile_once(sm)  # 新版本不就绪且已超时 → failed;旧 → stopped
        finally:
            settings.creating_timeout_seconds = saved
        assert (await _instance(sm, new_uuid)).status == "failed"
        after = (await client.get(f"/api/v1/services/{slug}", headers=headers)).json()
        assert after["status"] == "stopped" and after["rollout_instance"] is None
        assert after["current_instance"]["uuid"] == old_uuid
        assert after["desired_state"] == "running" and after["revision"] == 2
        async with sm() as session:
            note = (
                await session.execute(
                    select(Notification).where(
                        Notification.user_id == user_id, Notification.type == "service"
                    )
                )
            ).scalar_one()
        assert note.target_id == slug
        # 回滚 = 启动上一版本
        started = await client.post(f"/api/v1/services/{slug}/start", headers=headers)
        assert started.status_code == 200, started.text
        await drain(sm)
        fake.mark_ready(f"tenant-{user_id}", old_uuid)
        await reconcile_once(sm)
        back = (await client.get(f"/api/v1/services/{slug}", headers=headers)).json()
        assert back["status"] == "running" and back["current_instance"]["uuid"] == old_uuid

    async def test_quota_excludes_old_instance(self, client, sm, fake):
        settings = get_settings()
        saved = (settings.max_instances_per_user, settings.max_gpus_per_user)
        settings.max_instances_per_user = 1
        settings.max_gpus_per_user = 1
        try:
            headers, svc, _ = await _provision(client, sm, fake, "13900000405")
            resp = await client.post(
                f"/api/v1/services/{svc['slug']}/revisions",
                json=revision_body(svc),
                headers=headers,
            )
            assert resp.status_code == 202, resp.text
        finally:
            settings.max_instances_per_user, settings.max_gpus_per_user = saved

    async def test_idempotent_replay(self, client, sm, fake):
        headers, svc, _ = await _provision(client, sm, fake, "13900000406")
        slug = svc["slug"]
        h = {**headers, "Idempotency-Key": "rollout-1"}
        first = await client.post(
            f"/api/v1/services/{slug}/revisions", json=revision_body(svc), headers=h
        )
        assert first.status_code == 202, first.text
        again = await client.post(
            f"/api/v1/services/{slug}/revisions", json=revision_body(svc), headers=h
        )
        assert again.status_code == 200 and again.headers.get("x-idempotent-replay") == "true"
        assert again.json()["revision"] == 2
        assert again.json()["rollout_instance"]["uuid"] == first.json()["rollout_instance"]["uuid"]
        # 同键异参 → 409
        other = await client.post(
            f"/api/v1/services/{slug}/revisions",
            json=revision_body(svc, image_ref="registry.superdl.local/vllm:0.13.0"),
            headers=h,
        )
        assert other.status_code == 409
