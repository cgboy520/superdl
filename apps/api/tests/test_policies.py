"""策略常量:公开出参口径 + 在线调整(env 默认 / DB 覆盖 / 越界拒绝 / 盘价快照跟随)。"""

from httpx import AsyncClient

from tests.helpers import admin_headers


class TestPolicyOverrides:
    async def test_default_then_override_flows_to_public_endpoint(self, client: AsyncClient, sm):
        base = (await client.get("/api/v1/policies")).json()
        assert base["disk_price_gb_month"] == "0.0350"  # env 默认

        ah = await admin_headers(sm, client, role="ops")
        resp = await client.put(
            "/api/admin/v1/policies",
            json={"updates": {"disk_price_gb_month": "0.0500"}, "reason": "季度调价"},
            headers=ah,
        )
        assert resp.status_code == 200, resp.text

        updated = (await client.get("/api/v1/policies")).json()
        assert updated["disk_price_gb_month"] == "0.0500"

        admin_view = (await client.get("/api/admin/v1/policies", headers=ah)).json()
        assert admin_view["overrides"]["disk_price_gb_month"] == "0.0500"
        assert admin_view["effective"]["disk_price_gb_month"] == "0.0500"
        assert "specs" in admin_view

    async def test_new_disk_snapshots_overridden_price(self, client: AsyncClient, sm):
        """铁律:盘价是建盘时快照 —— 覆盖后新盘用新价。"""
        from tests.test_payment import create_order, pay_mock, user_headers

        ah = await admin_headers(sm, client, role="ops")
        await client.put(
            "/api/admin/v1/policies",
            json={"updates": {"disk_price_gb_month": "0.0700"}, "reason": "测试调价"},
            headers=ah,
        )
        headers = await user_headers(client, "13700000031")
        order = await create_order(client, headers, "100.00")
        await pay_mock(client, order["order_no"], "100.00")
        resp = await client.post(
            "/api/v1/disks", json={"name": "d1", "size_gb": 50}, headers=headers
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["price_gb_month"] == "0.0700"

    async def test_invalid_updates_rejected(self, client: AsyncClient, sm):
        ah = await admin_headers(sm, client, role="ops")
        # 越界
        resp = await client.put(
            "/api/admin/v1/policies",
            json={"updates": {"disk_price_gb_month": "9.99"}, "reason": "手滑"},
            headers=ah,
        )
        assert resp.status_code == 400
        # 未知键
        resp = await client.put(
            "/api/admin/v1/policies",
            json={"updates": {"jwt_secret": "hack"}, "reason": "越权"},
            headers=ah,
        )
        assert resp.status_code == 400
        # 非 ops 角色拒绝
        fh = await admin_headers(sm, client, role="finance")
        resp = await client.put(
            "/api/admin/v1/policies",
            json={"updates": {"disk_grace_days": "10"}, "reason": "无权"},
            headers=fh,
        )
        assert resp.status_code == 403


class TestPublicPolicies:
    async def test_fields_and_decimal_fidelity(self, client: AsyncClient):
        """公开端点免鉴权;盘价以 Decimal 字符串出参,保 scale 不失真(禁 float)。"""
        body = (await client.get("/api/v1/policies")).json()
        assert isinstance(body["disk_price_gb_month"], str)
