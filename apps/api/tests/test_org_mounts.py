"""Organization storage mounts: owner / admin management with the organization's own key, the
user-space check gating instances, mounting into members' Pods at /mnt/org/<name>, revisions and
the sweeps when the mount changes, a member leaves or the organization dissolves."""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.k8s.base import ORG_MOUNT_LABEL, ORG_MOUNT_REV_LABEL
from app.modules.orgs.models import OrgMount
from tests.helpers import create_test_sku, drain, fund_wallet, gen_ed25519_key
from tests.test_orgs import create_org, join_org, org_notices, org_user

pytestmark = pytest.mark.usefixtures("fake")

MOUNTS = "/api/v1/org/mounts"
KEY = "AQDorgSecretKeyForTestsOnly0123456789=="


def _body(**over: Any) -> dict[str, Any]:
    return {
        "name": "datasets",
        "ceph_path": "/volumes/team/datasets",
        "ceph_user": "client.team-a",
        "ceph_key": KEY,
        **over,
    }


async def _mount(client: AsyncClient, headers: dict[str, str], **over: Any) -> dict[str, Any]:
    resp = await client.post(MOUNTS, json=_body(**over), headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _member_instance(
    client: AsyncClient, sm: async_sessionmaker[AsyncSession], headers: dict[str, str], uid: int
) -> str:
    await fund_wallet(sm, uid)
    key = await client.post(
        "/api/v1/ssh-keys", json={"name": "k", "public_key": gen_ed25519_key()}, headers=headers
    )
    assert key.status_code == 201, key.text
    sku_id = await create_test_sku(sm)
    resp = await client.post(
        "/api/v1/instances",
        json={"sku_id": sku_id, "image_ref": "img", "ssh_key_ids": [key.json()["id"]]},
        headers=headers,
    )
    assert resp.status_code == 202, resp.text
    await drain(sm)
    return resp.json()["uuid"]


def _org_mounts(fake, namespace: str) -> list[tuple[str, str, bool]]:
    return sorted(
        (spec.labels[ORG_MOUNT_LABEL], spec.labels[ORG_MOUNT_REV_LABEL], spec.read_only)
        for (ns, _pvc), spec in fake.ceph_volumes.items()
        if ns == namespace and ORG_MOUNT_LABEL in spec.labels
    )


class TestManage:
    async def test_admin_creates_member_sees_without_secrets(self, client, sm):
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        member, member_uid, _ = await join_org(client, owner, 2)
        created = await _mount(client, owner)
        assert created["status"] == "checking"
        assert created["mount_path"] == "/mnt/org/datasets"
        assert created["ceph_user"] == "team-a"
        await drain(sm)
        listed = (await client.get(MOUNTS, headers=owner)).json()
        assert listed[0]["status"] == "ready" and listed[0]["ceph_path"] == "/volumes/team/datasets"
        seen = (await client.get(MOUNTS, headers=member)).json()[0]
        assert seen["ceph_path"] is None and seen["ceph_user"] is None
        assert "ceph_key" not in seen and "key_encrypted" not in seen
        assert any("Shared storage" in t for t in await org_notices(sm, member_uid))
        async with sm() as session:
            row = await session.scalar(select(OrgMount))
            assert row is not None and KEY not in row.key_encrypted

    async def test_members_cannot_manage(self, client, sm):
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        member, _uid, _ = await join_org(client, owner, 2)
        resp = await client.post(MOUNTS, json=_body(), headers=member)
        assert resp.status_code == 403
        assert resp.json()["message_key"] == "orgs.roleNotAllowed"

    async def test_platform_issued_keys_are_refused(self, client, sm):
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        resp = await client.post(
            MOUNTS, json=_body(ceph_user="superdl.u7-disk-" + "a" * 32), headers=owner
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "orgs.mountUserReserved"

    async def test_failed_check_is_reported_and_never_mounted(self, client, sm, fake, ceph):
        owner, owner_uid = await org_user(client, 1)
        await create_org(client, owner)
        ceph.unreadable_paths.add("/volumes/team/secret")
        await _mount(client, owner, ceph_path="/volumes/team/secret")
        await drain(sm)
        view = (await client.get(MOUNTS, headers=owner)).json()[0]
        assert view["status"] == "failed" and "permission denied" in view["last_error"]
        await _member_instance(client, sm, owner, owner_uid)
        assert _org_mounts(fake, f"tenant-{owner_uid}") == []


class TestPods:
    async def test_member_pod_mounts_ready_directory(self, client, sm, fake, ceph):
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        member, member_uid, _ = await join_org(client, owner, 2)
        mount = await _mount(client, owner)
        await drain(sm)
        uuid = await _member_instance(client, sm, member, member_uid)
        pod = fake.pods[(f"tenant-{member_uid}", uuid)]
        assert [(m.mount_path, m.read_only) for m in pod.spec.extra_mounts] == [
            ("/mnt/org/datasets", True)
        ]
        spec = fake.ceph_volumes[(f"tenant-{member_uid}", pod.spec.extra_mounts[0].pvc_name)]
        assert (spec.client_id, spec.client_key, spec.root_path) == (
            "team-a",
            KEY,
            "/volumes/team/datasets",
        )
        assert ceph.checked_paths == [("team-a", "/volumes/team/datasets")]
        assert _org_mounts(fake, f"tenant-{member_uid}") == [(mount["uuid"], "1", True)]

    async def test_change_bumps_revision_and_sweeps_the_previous_one(self, client, sm, fake):
        owner, owner_uid = await org_user(client, 1)
        await create_org(client, owner)
        mount = await _mount(client, owner)
        await drain(sm)
        await _member_instance(client, sm, owner, owner_uid)
        ns = f"tenant-{owner_uid}"
        assert _org_mounts(fake, ns) == [(mount["uuid"], "1", True)]
        resp = await client.patch(
            f"{MOUNTS}/{mount['uuid']}", json={"read_only": False}, headers=owner
        )
        assert resp.json()["status"] == "checking"
        await drain(sm)
        uuid = await _member_instance(client, sm, owner, owner_uid)
        assert fake.pods[(ns, uuid)].spec.extra_mounts[0].read_only is False
        assert (mount["uuid"], "2", False) in _org_mounts(fake, ns)
        assert (mount["uuid"], "1", True) not in _org_mounts(fake, ns)

    async def test_removed_member_and_deleted_mount_are_swept(self, client, sm, fake):
        owner, owner_uid = await org_user(client, 1)
        await create_org(client, owner)
        member, member_uid, member_id = await join_org(client, owner, 2)
        mount = await _mount(client, owner)
        await drain(sm)
        await _member_instance(client, sm, member, member_uid)
        await _member_instance(client, sm, owner, owner_uid)
        resp = await client.delete(f"/api/v1/org/members/{member_id}", headers=owner)
        assert resp.status_code == 204
        await drain(sm)
        assert _org_mounts(fake, f"tenant-{member_uid}") == []
        assert _org_mounts(fake, f"tenant-{owner_uid}") == [(mount["uuid"], "1", True)]
        resp = await client.delete(f"{MOUNTS}/{mount['uuid']}", headers=owner)
        assert resp.status_code == 204
        await drain(sm)
        assert _org_mounts(fake, f"tenant-{owner_uid}") == []

    async def test_disabled_mount_is_left_out(self, client, sm, fake):
        owner, owner_uid = await org_user(client, 1)
        await create_org(client, owner)
        await _mount(client, owner, enabled=False)
        await drain(sm)
        uuid = await _member_instance(client, sm, owner, owner_uid)
        assert fake.pods[(f"tenant-{owner_uid}", uuid)].spec.extra_mounts == ()
