"""Online service revision update (recreate): flip and release, slug/URL/keys unchanged, secrets
carried over, zero double charging,
in flight / unsettled / subscription 409, failure rollback, quotas, idempotency."""

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
    """Drive one recreate: old revision stops → new revision ready → flip → release the old one.
    Returns the final service view."""
    ns = f"tenant-{svc['_user_id']}"
    old_uuid = svc["current_instance"]["uuid"]
    new_uuid = rollout["rollout_instance"]["uuid"]
    await drain(sm)
    fake.finish_delete(ns, old_uuid)
    fake.mark_ready(ns, new_uuid)
    await reconcile_once(sm)
    await drain(sm)
    await reconcile_once(sm)
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
        pod_spec = fake.pods[(f"tenant-{user_id}", new_uuid)].spec
        assert pod_spec.secret_env["HF_TOKEN"] == "hf_secret_v1"
        assert pod_spec.env["MAX_MODEL_LEN"] == "8192"
        assert final["container"]["env_secret_keys"] == ["HF_TOKEN"]
        assert final["container"]["image_ref"] == V2_IMAGE
        ok = await client.post(AUTH_URL, headers=bearer)
        assert ok.status_code == 200 and ok.headers["x-superdl-endpoint"] == slug
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
            old_bills = await session.scalar(
                select(func.count()).select_from(BillHourly).where(BillHourly.instance_id == old.id)
            )
            svc_row = (
                await session.execute(select(Service).where(Service.public_slug == slug))
            ).scalar_one()
        assert old_reasons[-2:] == ["rollout_retire", "released"]
        assert (old_bills or 0) <= 1
        assert svc_row.current_instance_id != old.id and svc_row.released_at is None
        revisions = (await client.get(f"/api/v1/services/{slug}/revisions", headers=headers)).json()
        assert [r["uuid"] for r in revisions["items"]] == [new_uuid, old_uuid]
        events = (await client.get(f"/api/v1/services/{slug}/events", headers=headers)).json()
        assert {e["revision"] for e in events["items"]} == {1, 2}
        assert (await client.get("/api/v1/instances", headers=headers)).json()["items"] == []

    async def test_retire_replay_is_noop(self, client, sm, fake):
        """service.retire replay: another task after the old revision was released is a no-op."""
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
        await client.post(f"/api/v1/services/{slug}/stop", headers=headers)
        unsettled = await client.post(
            f"/api/v1/services/{slug}/revisions", json=revision_body(svc), headers=headers
        )
        assert unsettled.status_code == 409
        assert unsettled.json()["message_key"] == "services.rolloutNeedsSettled"
        await drain(sm)
        fake.finish_delete(f"tenant-{user_id}", svc["current_instance"]["uuid"])
        await reconcile_once(sm)
        bad = await client.post(
            f"/api/v1/services/{slug}/revisions",
            json=revision_body(svc, env_secret_keep=["NOPE"]),
            headers=headers,
        )
        assert bad.status_code == 400 and bad.json()["message_key"] == "services.envKeepUnknown"
        ok = await client.post(
            f"/api/v1/services/{slug}/revisions", json=revision_body(svc), headers=headers
        )
        assert ok.status_code == 202, ok.text
        old = await _instance(sm, svc["current_instance"]["uuid"])
        assert old.status == "stopped"

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
            await reconcile_once(sm)
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
        other = await client.post(
            f"/api/v1/services/{slug}/revisions",
            json=revision_body(svc, image_ref="registry.superdl.local/vllm:0.13.0"),
            headers=h,
        )
        assert other.status_code == 409
