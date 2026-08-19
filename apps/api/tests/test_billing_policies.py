"""GET /api/v1/policies:公开策略常量(前端展示口径唯一来源)。"""

from httpx import AsyncClient


class TestPolicies:
    async def test_public_no_auth(self, client: AsyncClient):
        resp = await client.get("/api/v1/policies")
        assert resp.status_code == 200, resp.text

    async def test_fields_and_decimal_fidelity(self, client: AsyncClient):
        body = (await client.get("/api/v1/policies")).json()
        # 盘价以 Decimal 字符串出参,保 scale 不失真(禁 float)
        assert body["disk_price_gb_month"] == "0.0350"
        assert isinstance(body["disk_price_gb_month"], str)
        assert body["disk_min_gb"] == 10
        assert body["disk_max_gb"] == 4096
        assert body["disk_grace_days"] == 7
        assert body["disk_frozen_days"] == 30
        assert body["freeze_grace_hours"] == 72
        assert body["low_balance_warn_hours_default"] == 24
