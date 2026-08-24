from httpx import AsyncClient

from tests.test_account_auth import register

# 合法的 ed25519 测试公钥(ssh-keygen 真实生成)
ED25519_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOblF2Q+8knaANZllifZUQ6+S0sWDtiFm9UJtgAJtx5R dev@test"
)


async def auth_client(client: AsyncClient) -> dict[str, str]:
    data = await register(client, "13800000009")
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
        # 声明 ssh-rsa 但 blob 是 ed25519
        blob_part = ED25519_KEY.split()[1]
        resp = await client.post(
            "/api/v1/ssh-keys",
            json={"name": "x", "public_key": f"ssh-rsa {blob_part}"},
            headers=headers,
        )
        assert resp.json()["code"] == "SSH_KEY_INVALID"

    async def test_same_key_allowed_across_users(self, client: AsyncClient):
        """指纹唯一性收窄为 (user_id, fingerprint):全局唯一是跨租户枚举面
        (可探测/占位阻断他租户添加自己的钥匙)。挂了 = 枚举面回潮。"""
        from tests.test_account_auth import register

        h1 = await auth_client(client)
        data = await register(client, "13800000010")
        h2 = {"Authorization": f"Bearer {data['access_token']}"}
        for h in (h1, h2):
            resp = await client.post(
                "/api/v1/ssh-keys", json={"name": "k", "public_key": ED25519_KEY}, headers=h
            )
            assert resp.status_code == 201, resp.text

    async def test_delete_then_readd_same_key(self, client: AsyncClient):
        """删除是硬删除:删过的指纹可直接重新添加(无恢复语义)。"""
        headers = await auth_client(client)
        resp = await client.post(
            "/api/v1/ssh-keys", json={"name": "k", "public_key": ED25519_KEY}, headers=headers
        )
        key_id = resp.json()["id"]
        resp = await client.delete(f"/api/v1/ssh-keys/{key_id}", headers=headers)
        assert resp.status_code == 204
        resp = await client.post(
            "/api/v1/ssh-keys", json={"name": "k2", "public_key": ED25519_KEY}, headers=headers
        )
        assert resp.status_code == 201, resp.text
