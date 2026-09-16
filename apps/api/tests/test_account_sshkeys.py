from httpx import AsyncClient

from tests.helpers import register, seed_instance

ED25519_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOblF2Q+8knaANZllifZUQ6+S0sWDtiFm9UJtgAJtx5R dev@test"
)


async def auth_client(client: AsyncClient) -> dict[str, str]:
    data = await register(client, "u13800000009@test.local")
    return {"Authorization": f"Bearer {data['access_token']}"}


class TestSshKeys:
    async def test_add_list_delete(self, client: AsyncClient):
        headers = await auth_client(client)
        resp = await client.post(
            "/api/v1/ssh-keys",
            json={"name": "laptop", "public_key": ED25519_KEY},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        key = resp.json()
        assert key["fingerprint"].startswith("SHA256:")

        resp = await client.get("/api/v1/ssh-keys", headers=headers)
        assert len(resp.json()) == 1

        resp = await client.delete(f"/api/v1/ssh-keys/{key['id']}", headers=headers)
        assert resp.status_code == 204
        resp = await client.get("/api/v1/ssh-keys", headers=headers)
        assert resp.json() == []
        resp = await client.post(
            "/api/v1/ssh-keys",
            json={"name": "laptop-2", "public_key": ED25519_KEY},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text

    async def test_duplicate_fingerprint(self, client: AsyncClient):
        headers = await auth_client(client)
        await client.post(
            "/api/v1/ssh-keys", json={"name": "a", "public_key": ED25519_KEY}, headers=headers
        )
        resp = await client.post(
            "/api/v1/ssh-keys", json={"name": "b", "public_key": ED25519_KEY}, headers=headers
        )
        assert resp.json()["code"] == "SSH_KEY_DUPLICATE"

    async def test_invalid_key(self, client: AsyncClient):
        headers = await auth_client(client)
        for bad in ["not a key", "ssh-ed25519 %%%invalid%%%", "ssh-dss AAAA abc"]:
            resp = await client.post(
                "/api/v1/ssh-keys", json={"name": "x", "public_key": bad}, headers=headers
            )
            assert resp.json()["code"] == "SSH_KEY_INVALID", bad

    async def test_type_mismatch_rejected(self, client: AsyncClient):
        headers = await auth_client(client)
        blob_part = ED25519_KEY.split()[1]
        resp = await client.post(
            "/api/v1/ssh-keys",
            json={"name": "x", "public_key": f"ssh-rsa {blob_part}"},
            headers=headers,
        )
        assert resp.json()["code"] == "SSH_KEY_INVALID"

    async def test_same_key_allowed_across_users(self, client: AsyncClient):
        """Fingerprint uniqueness is (user_id, fingerprint), not global."""
        h1 = await auth_client(client)
        data = await register(client, "u13800000010@test.local")
        h2 = {"Authorization": f"Bearer {data['access_token']}"}
        for h in (h1, h2):
            resp = await client.post(
                "/api/v1/ssh-keys", json={"name": "k", "public_key": ED25519_KEY}, headers=h
            )
            assert resp.status_code == 201, resp.text

    async def test_delete_strips_key_from_live_instances(self, client: AsyncClient, sm):
        """Deleting a key removes it from the authorized_keys snapshot of unreleased instances;
        released instances are untouched."""
        from app.modules.orchestrator.models import Instance

        data = await register(client, "u13800000011@test.local")
        headers = {"Authorization": f"Bearer {data['access_token']}"}
        resp = await client.post(
            "/api/v1/ssh-keys", json={"name": "laptop", "public_key": ED25519_KEY}, headers=headers
        )
        assert resp.status_code == 201, resp.text
        key_id = resp.json()["id"]
        stored_key = resp.json()["public_key"]
        live_id, _ = await seed_instance(sm, user_id=data["user"]["id"], status="running")
        released_id, _ = await seed_instance(sm, user_id=data["user"]["id"], status="released")
        async with sm() as session:
            for iid in (live_id, released_id):
                inst = await session.get(Instance, iid)
                assert inst is not None
                inst.authorized_keys = [stored_key]
            await session.commit()

        resp = await client.delete(f"/api/v1/ssh-keys/{key_id}", headers=headers)
        assert resp.status_code == 204, resp.text
        async with sm() as session:
            live = await session.get(Instance, live_id)
            released = await session.get(Instance, released_id)
        assert live is not None and live.authorized_keys == []
        assert released is not None and released.authorized_keys == [stored_key]


class TestSshKeyCap:
    async def test_limit_per_user(self, client: AsyncClient, monkeypatch):
        from app.modules.account import sshkeys as account_sshkeys

        monkeypatch.setattr(account_sshkeys, "MAX_SSH_KEYS_PER_USER", 1)
        data = await register(client, "u13800000019@test.local")
        headers = {"Authorization": f"Bearer {data['access_token']}"}
        assert (
            await client.post(
                "/api/v1/ssh-keys", json={"name": "a", "public_key": ED25519_KEY}, headers=headers
            )
        ).status_code == 201
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519

        second = (
            ed25519.Ed25519PrivateKey.generate()
            .public_key()
            .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
            .decode()
        )
        resp = await client.post(
            "/api/v1/ssh-keys", json={"name": "b", "public_key": second}, headers=headers
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["message_key"] == "account.sshKeyLimitReached"
