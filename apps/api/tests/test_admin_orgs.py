"""Admin organization routes: listing and search, masked detail, dissolution, member removal,
ownership transfer, and the organization reference on tenant rows (role gates live in
test_admin_route_role_matrix)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.helpers import admin_headers
from tests.test_orgs import create_org, join_org, org_email, org_user

pytestmark = pytest.mark.usefixtures("fake")

REASON = {"reason": "customer support request"}


class TestAdminOrgs:
    async def test_search_and_masked_detail(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """Fails when the admin views leak member handles or the search misses an organization."""
        owner, owner_uid = await org_user(client, 1)
        created = await create_org(client, owner, "Acme Lab")
        _, member_uid, _ = await join_org(client, owner, 2)
        uuid = created["org"]["uuid"]
        admin = await admin_headers(sm, client, "readonly")
        for q in ("Acme", uuid, str(member_uid)):
            page = (await client.get("/api/admin/v1/orgs", params={"q": q}, headers=admin)).json()
            assert [o["uuid"] for o in page["items"]] == [uuid], q
        detail = (await client.get(f"/api/admin/v1/orgs/{uuid}", headers=admin)).json()
        assert detail["owner_user_id"] == owner_uid
        assert detail["members"] == 2
        assert all("***@" in m["email_masked"] for m in detail["member_list"])
        assert org_email(1) not in str(detail)

    async def test_detail_shows_code_created_accounts_and_mounts_without_the_key(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """Fails when admins cannot tell created accounts or the tenant's storage apart, or when
        the cephx key leaks into the admin view."""
        owner, _ = await org_user(client, 1)
        uuid = (await create_org(client, owner, "Acme Lab", code="acme"))["org"]["uuid"]
        created = await client.post("/api/v1/org/users", json={"names": ["alice"]}, headers=owner)
        assert created.status_code == 201, created.text
        key = "AQDorgSecretKeyForTestsOnly0123456789=="
        mount = await client.post(
            "/api/v1/org/mounts",
            json={
                "name": "datasets",
                "ceph_path": "/volumes/team/datasets",
                "ceph_user": "client.team-a",
                "ceph_key": key,
            },
            headers=owner,
        )
        assert mount.status_code == 201, mount.text
        admin = await admin_headers(sm, client, "readonly")
        page = (await client.get("/api/admin/v1/orgs", params={"q": "acme"}, headers=admin)).json()
        assert [(o["uuid"], o["code"]) for o in page["items"]] == [(uuid, "acme")]
        detail = (await client.get(f"/api/admin/v1/orgs/{uuid}", headers=admin)).json()
        managed = [m for m in detail["member_list"] if m["managed"]]
        assert [m["username"] for m in managed] == ["acme.alice"]
        assert [(m["mount_path"], m["ceph_user"]) for m in detail["mounts"]] == [
            ("/mnt/org/datasets", "team-a")
        ]
        assert key not in str(detail)

    async def test_dissolve_and_remove(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        owner, owner_uid = await org_user(client, 1)
        uuid = (await create_org(client, owner))["org"]["uuid"]
        member, member_uid, _ = await join_org(client, owner, 2)
        await join_org(client, owner, 3)
        ops = await admin_headers(sm, client, "ops")
        resp = await client.post(
            f"/api/admin/v1/orgs/{uuid}/members/remove",
            json={**REASON, "user_id": owner_uid},
            headers=ops,
        )
        assert resp.status_code == 409
        resp = await client.post(
            f"/api/admin/v1/orgs/{uuid}/members/remove",
            json={**REASON, "user_id": member_uid},
            headers=ops,
        )
        assert resp.status_code == 200
        assert resp.json()["members"] == 2
        assert (await client.get("/api/v1/org", headers=member)).json()["org"] is None
        resp = await client.post(f"/api/admin/v1/orgs/{uuid}/dissolve", json=REASON, headers=ops)
        assert resp.json()["status"] == "dissolved"
        assert resp.json()["member_list"] == []
        again = await client.post(f"/api/admin/v1/orgs/{uuid}/dissolve", json=REASON, headers=ops)
        assert again.status_code == 409

    async def test_transfer_swaps_owner_and_admin(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        owner, owner_uid = await org_user(client, 1)
        uuid = (await create_org(client, owner))["org"]["uuid"]
        member, member_uid, _ = await join_org(client, owner, 2)
        admin = await admin_headers(sm, client)
        resp = await client.post(
            f"/api/admin/v1/orgs/{uuid}/transfer",
            json={**REASON, "user_id": member_uid},
            headers=admin,
        )
        assert resp.status_code == 200, resp.text
        roles = {m["user_id"]: m["role"] for m in resp.json()["member_list"]}
        assert roles == {owner_uid: "admin", member_uid: "owner"}
        assert (await client.get("/api/v1/org", headers=member)).json()["role"] == "owner"

    async def test_tenant_rows_name_the_organization(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        owner, owner_uid = await org_user(client, 1)
        await create_org(client, owner, "Acme Lab")
        _, loner_uid = await org_user(client, 2)
        admin = await admin_headers(sm, client, "readonly")
        items = (await client.get("/api/admin/v1/tenants", headers=admin)).json()["items"]
        by_id = {t["id"]: t["org"] for t in items}
        assert by_id[owner_uid] is not None
        assert by_id[owner_uid]["name"] == "Acme Lab"
        assert by_id[owner_uid]["role"] == "owner"
        assert by_id[loner_uid] is None
