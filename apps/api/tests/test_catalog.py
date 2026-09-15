# pyright: reportPrivateUsage=false
from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.core.errors import AppError
from tests.helpers import admin_headers, seed_node_spec, seed_skus


class TestMarket:
    async def test_only_on_sale_visible(self, client: AsyncClient, sm):
        await seed_skus(sm)
        resp = await client.get("/api/v1/skus")
        assert resp.status_code == 200
        names = [s["name"] for s in resp.json()]
        assert "A100 · 独享(下架)" not in names
        assert len(names) == 2

    async def test_filters(self, client: AsyncClient, sm):
        await seed_skus(sm)
        resp = await client.get("/api/v1/skus", params={"tier": "dedicated"})
        assert [s["tier"] for s in resp.json()] == ["dedicated"]
        resp = await client.get("/api/v1/skus", params={"gpu_model": "RTX4090"})
        assert len(resp.json()) == 2


class TestSellablePerGpu:
    def test_decimal_floor_division(self):
        """每卡可售数走 Decimal 整除,市场库存与管理端容量预览同口径。"""
        from app.modules.catalog.service import sellable_per_gpu

        assert sellable_per_gpu("hami", 5, Decimal("1.15")) == 23
        assert sellable_per_gpu("hami", 50, Decimal("1.50")) == 3
        assert sellable_per_gpu("kata", 100, Decimal("1.50")) == 1
        assert sellable_per_gpu("mig", 100, Decimal("1.50")) == 1


class TestAdminSku:
    async def test_admin_crud(self, client: AsyncClient, sm):
        headers = await admin_headers(sm, client)
        body = {
            "name": "H100 · MIG",
            "gpu_model": "H100",
            "tier": "shared",
            "mig_profile": "1g.10gb",
            "vram_gb": 10,
            "pool_label": "mig",
            "vcpu": 8,
            "mem_gb": 32,
            "price_hourly": "2.5000",
        }
        resp = await client.post("/api/admin/v1/skus", json=body, headers=headers)
        assert resp.status_code == 201, resp.text
        sku_id = resp.json()["id"]
        assert resp.json()["status"] == "off"

        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}?force=true",
            json={"status": "on", "price_hourly": "2.8000", "reason": "上架调价"},
            headers=headers,
        )
        assert resp.json()["price_hourly"] == "2.8000"

        market = (await client.get("/api/v1/skus")).json()
        assert any(s["id"] == sku_id for s in market)

    async def test_isolation_change_only_when_off_sale(self, client: AsyncClient, sm):
        """在售规格不许改池与 MIG 切片;下架后两者可一起改。"""
        await seed_node_spec(sm, pool_label="mig", gpu_model="H100")
        headers = await admin_headers(sm, client)
        body = {
            "name": "H100 · MIG 在售",
            "gpu_model": "H100",
            "tier": "shared",
            "mig_profile": "1g.10gb",
            "vram_gb": 10,
            "pool_label": "mig",
            "vcpu": 8,
            "mem_gb": 32,
            "price_hourly": "2.5000",
        }
        resp = await client.post("/api/admin/v1/skus", json=body, headers=headers)
        sku_id = resp.json()["id"]
        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}",
            json={"status": "on", "reason": "上架"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        for qs in ("", "?force=true"):
            resp = await client.patch(
                f"/api/admin/v1/skus/{sku_id}{qs}",
                json={"pool_label": "hami", "mig_profile": None, "reason": "迁池"},
                headers=headers,
            )
            assert resp.status_code == 409, resp.text
            assert resp.json()["message_key"] == "catalog.isolationChangeNeedsOffSale"
        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}",
            json={"mig_profile": "2g.20gb", "reason": "换切片"},
            headers=headers,
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["message_key"] == "catalog.isolationChangeNeedsOffSale"
        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}",
            json={"price_hourly": "2.6000", "reason": "调价"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}",
            json={"status": "off", "reason": "下架"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}",
            json={"pool_label": "hami", "reason": "迁池"},
            headers=headers,
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["message_key"] == "catalog.migProfileMismatch"
        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}",
            json={"pool_label": "hami", "mig_profile": None, "gpu_cores_pct": 50, "reason": "迁池"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["pool_label"] == "hami" and resp.json()["mig_profile"] is None

    async def test_update_colliding_business_key_is_409(self, client: AsyncClient, sm):
        """改 SKU 撞到另一条的业务唯一键 → 409。"""
        headers = await admin_headers(sm, client)
        base = {
            "gpu_model": "L40S",
            "tier": "shared",
            "pool_label": "hami",
            "vram_gb": 24,
            "vcpu": 8,
            "mem_gb": 32,
            "price_hourly": "1.0000",
        }
        a = await client.post(
            "/api/admin/v1/skus",
            json={**base, "name": "L40S-30", "gpu_cores_pct": 30},
            headers=headers,
        )
        b = await client.post(
            "/api/admin/v1/skus",
            json={**base, "name": "L40S-50", "gpu_cores_pct": 50},
            headers=headers,
        )
        assert a.status_code == 201 and b.status_code == 201, (a.text, b.text)
        resp = await client.patch(
            f"/api/admin/v1/skus/{b.json()['id']}",
            json={"gpu_cores_pct": 30, "reason": "撞键"},
            headers=headers,
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["message_key"] == "catalog.skuBusinessKeyExists"

    async def test_tier_pool_must_pair(self, client: AsyncClient, sm):
        """档位与池必须配对。"""
        headers = await admin_headers(sm, client)
        base = {
            "gpu_model": "RTX4090",
            "vram_gb": 24,
            "vcpu": 8,
            "mem_gb": 32,
            "price_hourly": "1.0000",
        }
        resp = await client.post(
            "/api/admin/v1/skus",
            json={**base, "name": "假整卡", "tier": "dedicated", "pool_label": "hami"},
            headers=headers,
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["message_key"] == "catalog.tierPoolMismatch"
        resp = await client.post(
            "/api/admin/v1/skus",
            json={**base, "name": "假共享", "tier": "shared", "pool_label": "kata"},
            headers=headers,
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["message_key"] == "catalog.tierPoolMismatch"
        resp = await client.post(
            "/api/admin/v1/skus",
            json={**base, "name": "无切片 MIG", "tier": "shared", "pool_label": "mig"},
            headers=headers,
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["message_key"] == "catalog.migProfileMismatch"
        resp = await client.post(
            "/api/admin/v1/skus",
            json={
                **base,
                "name": "HAMi 带切片",
                "tier": "shared",
                "pool_label": "hami",
                "mig_profile": "1g.10gb",
            },
            headers=headers,
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["message_key"] == "catalog.migProfileMismatch"

    async def test_shared_tier_allowed_pools_switch(self, client: AsyncClient, sm, monkeypatch):
        """shared_tier_allowed_pools 摘掉 hami 后共享档只能建 MIG 池 SKU;置空则整体停售。"""
        from app.core.config import get_settings

        monkeypatch.setattr(get_settings(), "shared_tier_allowed_pools", "mig")
        headers = await admin_headers(sm, client)
        base = {
            "gpu_model": "RTX4090",
            "vram_gb": 24,
            "vcpu": 8,
            "mem_gb": 32,
            "price_hourly": "1.0000",
        }
        resp = await client.post(
            "/api/admin/v1/skus",
            json={**base, "name": "HAMi 禁售", "tier": "shared", "pool_label": "hami"},
            headers=headers,
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["message_key"] == "catalog.tierPoolMismatch"
        resp = await client.post(
            "/api/admin/v1/skus",
            json={
                **base,
                "name": "MIG 在售",
                "tier": "shared",
                "pool_label": "mig",
                "mig_profile": "1g.10gb",
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text

        monkeypatch.setattr(get_settings(), "shared_tier_allowed_pools", "")
        resp = await client.post(
            "/api/admin/v1/skus",
            json={
                **base,
                "name": "共享停售",
                "tier": "shared",
                "pool_label": "mig",
                "mig_profile": "1g.10gb",
            },
            headers=headers,
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["message_key"] == "catalog.tierPoolMismatch"


class TestPriceFloor:
    """时价上架/改价拦免费价与超 2 位小数价。"""

    def test_min_billable_price_accepted(self):
        from app.modules.catalog.service import _checked_price

        assert _checked_price(Decimal("0.01")) == Decimal("0.01")
        assert _checked_price(Decimal("1.6800")) == Decimal("1.6800")

    def test_sub_cent_precision_rejected(self):
        """按小时计费的 SKU 超过 2 位小数即拒。"""
        from app.modules.catalog.service import _checked_price

        with pytest.raises(AppError) as exc:
            _checked_price(Decimal("0.0051"))
        assert exc.value.message_key == "catalog.priceHourlyTwoDecimals"
        with pytest.raises(AppError) as exc:
            _checked_price(Decimal("1.2345"))
        assert exc.value.message_key == "catalog.priceHourlyTwoDecimals"

    def test_zero_price_still_rejected_with_original_key(self):
        from app.modules.catalog.service import _checked_price

        with pytest.raises(AppError) as exc:
            _checked_price(Decimal("0.0000"))
        assert exc.value.message_key == "catalog.priceTooSmall"
