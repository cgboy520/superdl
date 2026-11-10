"""Uploaded images end to end: chunked upload with resend and resume state, completion and the
import on image-ops, the instance-creation check (own or tenant-shared, architecture), deletion
while in use, failures, quotas and the chunk body cap."""

import hashlib
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.modules.customimages import handlers, staging
from tests.fake_registry import FakeRegistry
from tests.helpers import (
    admin_headers,
    create_test_sku,
    drain,
    fund_wallet,
    gen_ed25519_key,
    seed_node_spec,
    set_platform_setting,
    user_headers_with_id,
)
from tests.test_custom_image_archive import classic, layer_tar, tar_bytes
from tests.test_orgs import create_org, join_org, org_user

pytestmark = pytest.mark.usefixtures("fake")

BASE = "/api/v1/custom-images"


@pytest.fixture
async def registry(
    sm: async_sessionmaker[AsyncSession], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> FakeRegistry:
    fake_registry = FakeRegistry()
    monkeypatch.setattr(get_settings(), "image_upload_dir", str(tmp_path))
    monkeypatch.setattr(staging, "CHUNK_BYTES", 2048)
    monkeypatch.setattr(handlers, "registry_transport", fake_registry.transport())
    await set_platform_setting(sm, "registry_host", "reg.test")
    await set_platform_setting(sm, "registry_push_robot_name", "robot$push")
    await set_platform_setting(sm, "registry_push_robot_secret", "push-secret")
    return fake_registry


def _chunks(data: bytes, size: int = 2048) -> list[bytes]:
    return [data[i : i + size] for i in range(0, len(data), size)]


async def _start(
    client: AsyncClient, headers: dict[str, str], data: bytes, **over: Any
) -> dict[str, Any]:
    body = {
        "name": "myenv",
        "tag": "v1",
        "filename": "myenv.tar",
        "size_bytes": len(data),
        "first_chunk_sha256": hashlib.sha256(_chunks(data)[0]).hexdigest(),
        **over,
    }
    resp = await client.post(BASE, json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _put(
    client: AsyncClient, headers: dict[str, str], uuid: str, index: int, chunk: bytes
) -> Any:
    return await client.put(
        f"{BASE}/{uuid}/chunks/{index}",
        content=chunk,
        headers={
            **headers,
            "Content-Type": "application/octet-stream",
            "X-Chunk-Sha256": hashlib.sha256(chunk).hexdigest(),
        },
    )


async def _upload(
    client: AsyncClient, sm, headers: dict[str, str], data: bytes, **over: Any
) -> dict[str, Any]:
    image = await _start(client, headers, data, **over)
    for i, chunk in enumerate(_chunks(data)):
        assert (await _put(client, headers, image["uuid"], i, chunk)).status_code == 200
    done = await client.post(f"{BASE}/{image['uuid']}/complete", headers=headers)
    assert done.json()["status"] == "importing", done.text
    await drain(sm)
    return (await client.get(f"{BASE}/{image['uuid']}", headers=headers)).json()


async def _instance(client: AsyncClient, sm, headers: dict[str, str], uid: int, ref: str) -> Any:
    await fund_wallet(sm, uid)
    key = await client.post(
        "/api/v1/ssh-keys", json={"name": "k", "public_key": gen_ed25519_key()}, headers=headers
    )
    sku_id = await create_test_sku(sm)
    return await client.post(
        "/api/v1/instances",
        json={"sku_id": sku_id, "image_ref": ref, "ssh_key_ids": [key.json()["id"]]},
        headers=headers,
    )


class TestUpload:
    async def test_resumable_upload_imports_and_feeds_an_instance(
        self, client, sm, fake, registry, tmp_path: Path
    ):
        headers, uid = await user_headers_with_id(client, "img-1@test.local")
        data = classic([layer_tar(1), layer_tar(2)])
        image = await _start(client, headers, data)
        chunks = _chunks(data)
        assert image["chunks_total"] == len(chunks) > 2
        for i in reversed(range(len(chunks))):
            assert (await _put(client, headers, image["uuid"], i, chunks[i])).status_code == 200
            if i == len(chunks) - 1:
                early = await client.post(f"{BASE}/{image['uuid']}/complete", headers=headers)
                assert early.json()["message_key"] == "customImages.chunksMissing"
        resent = await _put(client, headers, image["uuid"], 1, chunks[1])
        assert resent.json()["chunks_received"] == len(chunks)
        await client.post(f"{BASE}/{image['uuid']}/complete", headers=headers)
        await drain(sm)
        ready = (await client.get(f"{BASE}/{image['uuid']}", headers=headers)).json()
        assert ready["status"] == "ready", ready
        assert ready["image_ref"].startswith(f"reg.test/superdl-users/u{uid}/myenv:v1@sha256:")
        assert ready["dev_ready"] is True and ready["architecture"] == "amd64"
        assert not (tmp_path / str(uid) / image["uuid"]).exists()
        resp = await _instance(client, sm, headers, uid, ready["image_ref"])
        assert resp.status_code == 202, resp.text
        assert resp.json()["spec"]["image_arch"] == "amd64"
        await drain(sm)
        pod = fake.pods[(f"tenant-{uid}", resp.json()["uuid"])]
        assert pod.spec.node_selector["kubernetes.io/arch"] == "amd64"

    async def test_chunk_and_session_checks(self, client, sm, registry):
        headers, _uid = await user_headers_with_id(client, "img-2@test.local")
        data = classic([layer_tar(3)])
        image = await _start(client, headers, data)
        bad = await client.put(
            f"{BASE}/{image['uuid']}/chunks/0",
            content=_chunks(data)[0],
            headers={**headers, "X-Chunk-Sha256": "0" * 64},
        )
        assert bad.json()["message_key"] == "customImages.chunkMismatch"
        other = classic([layer_tar(4)])
        swapped = await _put(client, headers, image["uuid"], 0, _chunks(other)[0])
        assert swapped.json()["message_key"] == "customImages.differentFile"
        dup = await client.post(
            BASE,
            json={
                "name": "myenv",
                "tag": "v1",
                "filename": "x.tar",
                "size_bytes": 10,
                "first_chunk_sha256": "a" * 64,
            },
            headers=headers,
        )
        assert dup.json()["message_key"] == "customImages.nameTagTaken"
        fmt = await client.post(
            BASE,
            json={
                "name": "other",
                "filename": "x.rar",
                "size_bytes": 10,
                "first_chunk_sha256": "a" * 64,
            },
            headers=headers,
        )
        assert fmt.json()["message_key"] == "customImages.formatUnsupported"

    async def test_quotas(self, client, sm, registry):
        headers, _uid = await user_headers_with_id(client, "img-3@test.local")
        await set_platform_setting(sm, "custom_image_max_upload_gb", "1")
        big = await client.post(
            BASE,
            json={
                "name": "big",
                "filename": "big.tar",
                "size_bytes": 2 * 1024**3,
                "first_chunk_sha256": "a" * 64,
            },
            headers=headers,
        )
        assert big.json()["message_key"] == "customImages.uploadTooLarge"
        await set_platform_setting(sm, "max_custom_images_per_user", "1")
        await _start(client, headers, classic([layer_tar(5)]))
        second = await client.post(
            BASE,
            json={
                "name": "two",
                "filename": "two.tar",
                "size_bytes": 10,
                "first_chunk_sha256": "a" * 64,
            },
            headers=headers,
        )
        assert second.json()["message_key"] == "customImages.countQuota"

    async def test_rejected_archive_fails_with_a_reason(self, client, sm, registry):
        headers, _uid = await user_headers_with_id(client, "img-4@test.local")
        out = await _upload(client, sm, headers, tar_bytes({"etc/hosts": b"127.0.0.1" * 400}))
        assert out["status"] == "failed"
        assert out["error_key"] == "customImages.import.notDockerArchive"

    async def test_chunk_body_is_capped(self, client, sm, registry):
        headers, _uid = await user_headers_with_id(client, "img-5@test.local")
        image = await _start(client, headers, classic([layer_tar(6)]))
        resp = await client.put(
            f"{BASE}/{image['uuid']}/chunks/0",
            content=b"x" * (8 * 1024 * 1024 + 1),
            headers={**headers, "X-Chunk-Sha256": "0" * 64},
        )
        assert resp.status_code == 413


class TestUseAndDelete:
    async def test_others_need_a_tenant_share(self, client, sm, registry):
        owner, owner_uid = await org_user(client, 1)
        await create_org(client, owner)
        member, member_uid, _ = await join_org(client, owner, 2)
        stranger, stranger_uid = await user_headers_with_id(client, "img-6@test.local")
        ready = await _upload(client, sm, owner, classic([layer_tar(7)]))
        ref = ready["image_ref"]
        refused = await _instance(client, sm, member, member_uid, ref)
        assert refused.json()["message_key"] == "customImages.notUsable"
        shared = await client.patch(
            f"{BASE}/{ready['uuid']}", json={"shared_with_org": True}, headers=owner
        )
        assert shared.json()["shared_with_org"] is True
        listed = (await client.get(BASE, headers=member)).json()
        assert [(i["owned"], i["image_ref"]) for i in listed] == [(False, ref)]
        assert (await _instance(client, sm, member, member_uid, ref)).status_code == 202
        outsider = await _instance(client, sm, stranger, stranger_uid, ref)
        assert outsider.json()["message_key"] == "customImages.notUsable"
        del owner_uid

    async def test_delete_waits_for_release_then_removes_the_artifact(
        self, client, sm, fake, registry
    ):
        headers, uid = await user_headers_with_id(client, "img-7@test.local")
        ready = await _upload(client, sm, headers, classic([layer_tar(8)]))
        inst = await _instance(client, sm, headers, uid, ready["image_ref"])
        await drain(sm)
        busy = await client.delete(f"{BASE}/{ready['uuid']}", headers=headers)
        assert busy.json()["message_key"] == "customImages.inUse"
        await client.delete(f"/api/v1/instances/{inst.json()['uuid']}", headers=headers)
        from app.modules.orchestrator.reconciler import reconcile_once

        await drain(sm)
        await reconcile_once(sm)
        gone = await client.delete(f"{BASE}/{ready['uuid']}", headers=headers)
        assert gone.status_code == 204, gone.text
        await drain(sm)
        assert registry.deleted and registry.deleted[0][1] == ready["image_ref"].split("@")[1]
        assert (await client.get(BASE, headers=headers)).json() == []

    async def test_architecture_must_exist_in_the_pool(self, client, sm, registry):
        headers, uid = await user_headers_with_id(client, "img-8@test.local")
        ready = await _upload(client, sm, headers, classic([layer_tar(9)]))
        await seed_node_spec(sm, node_name="arm-1", pool_label="hami", gpu_count=8)
        from sqlalchemy import update

        from app.modules.nodes.models import NodeSpec

        async with sm() as session:
            await session.execute(update(NodeSpec).values(arch="arm64"))
            await session.commit()
        resp = await _instance(client, sm, headers, uid, ready["image_ref"])
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "customImages.archUnavailable"
        skus = (await client.get("/api/v1/skus")).json()
        assert {tuple(s["archs"]) for s in skus if s["pool_label"] == "hami"} == {("arm64",)}


class TestAdminUsage:
    async def test_usage_counts_like_the_upload_quota(self, client, sm, registry):
        h1, uid1 = await user_headers_with_id(client, "img-usage-1@test.local")
        h2, uid2 = await user_headers_with_id(client, "img-usage-2@test.local")
        ready = await _upload(client, sm, h1, classic([layer_tar(11)]))
        assert ready["status"] == "ready", ready
        pending = classic([layer_tar(12)])
        await _start(client, h1, pending, name="second")
        other = classic([layer_tar(13), layer_tar(14)])
        await _start(client, h2, other, name="other")
        gone = await _start(client, h2, classic([layer_tar(15)]), name="gone")
        assert (await client.delete(f"{BASE}/{gone['uuid']}", headers=h2)).status_code == 204

        ah = await admin_headers(sm, client, role="readonly")
        resp = await client.get("/api/admin/v1/custom-images/usage", headers=ah)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert (body["max_images"], body["max_bytes"]) == (20, 200 * 1024**3)
        # ready counts its registry size, an upload its archive size, a deleted one nothing
        by_user = {u["user_id"]: (u["images"], u["bytes"]) for u in body["users"]}
        assert by_user[uid1] == (2, ready["size_bytes"] + len(pending))
        assert by_user[uid2] == (1, len(other))
        sizes = [u["bytes"] for u in body["users"]]
        assert sizes == sorted(sizes, reverse=True)
