# pyright: reportPrivateUsage=false
from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.audit import AuditLog
from app.core.errors import AppError
from app.core.timeutil import now_utc
from app.modules.notify.models import Notification
from tests.helpers import admin_headers, seed_node_spec, seed_skus

_SKU_BODY = {
    "name": "A100 · 告警用例",
    "gpu_model": "A100",
    "tier": "dedicated",
    "vram_gb": 80,
    "pool_label": "kata",
    "vcpu": 8,
    "mem_gb": 32,
    "price_hourly": "10.0000",
}


async def _admin_alerts(sm) -> list[Notification]:
    """只取改价告警(管理员绑定 MFA 等其它 admin_alert 不算)。"""
    async with sm() as session:
        return list(
            (
                await session.execute(
                    select(Notification)
                    .where(
                        Notification.type == "admin_alert",
                        Notification.title.like("SKU 单价%"),
                    )
                    .order_by(Notification.id)
                )
            ).scalars()
        )


async def _set_price(client, headers, sku_id: int, price: str):
    return await client.patch(
        f"/api/admin/v1/skus/{sku_id}",
        json={"price_hourly": price, "reason": "告警用例"},
        headers=headers,
    )


class TestPriceChangeAlerts:
    async def test_cumulative_24h_change_is_critical(self, client: AsyncClient, sm):
        """两步各不到 50%,但相对 24 小时前基准累计 ≥50%:critical 告警;
        单步不足 50% 且无累计:不告警;审计行超过 24 小时后不再计入基准。"""
        headers = await admin_headers(sm, client)
        resp = await client.post("/api/admin/v1/skus", json=_SKU_BODY, headers=headers)
        assert resp.status_code == 201, resp.text
        sku_id = resp.json()["id"]

        assert (await _set_price(client, headers, sku_id, "13.0000")).status_code == 200
        assert await _admin_alerts(sm) == []

        assert (await _set_price(client, headers, sku_id, "16.0000")).status_code == 200
        alerts = await _admin_alerts(sm)
        assert [a.severity for a in alerts] == ["critical"]
        assert alerts[0].title.startswith("SKU 单价 24 小时累计大幅调整")
        assert "10.0000 → 现 16.0000" in alerts[0].content and "60%" in alerts[0].content

        async with sm() as session:
            await session.execute(
                update(AuditLog).values(created_at=now_utc() - timedelta(hours=25))
            )
            await session.commit()
        assert (await _set_price(client, headers, sku_id, "17.0000")).status_code == 200
        assert len(await _admin_alerts(sm)) == 1

    async def test_single_step_change_is_warning(self, client: AsyncClient, sm):
        """单步 ≥50% 且相对 24 小时基准也 ≥50%:只落一条 critical;
        单步 ≥50% 但回到基准附近(累计 <50%):落 warning。"""
        headers = await admin_headers(sm, client)
        resp = await client.post("/api/admin/v1/skus", json=_SKU_BODY, headers=headers)
        sku_id = resp.json()["id"]
        assert (await _set_price(client, headers, sku_id, "5.0000")).status_code == 200
        alerts = await _admin_alerts(sm)
        assert [a.severity for a in alerts] == ["critical"]

        assert (await _set_price(client, headers, sku_id, "10.0000")).status_code == 200
        alerts = await _admin_alerts(sm)
        assert [a.severity for a in alerts] == ["critical", "warning"]
        assert alerts[1].title.startswith("SKU 单价大幅调整")
        assert "100%" in alerts[1].content

    async def test_pricing_writes_rate_limited_per_admin(self, client: AsyncClient, sm):
        """SKU 建/改共用每管理员 20 次/时:第 21 次 429。"""
        headers = await admin_headers(sm, client)
        resp = await client.post("/api/admin/v1/skus", json=_SKU_BODY, headers=headers)
        sku_id = resp.json()["id"]
        for _ in range(19):
            resp = await client.patch(
                f"/api/admin/v1/skus/{sku_id}",
                json={"vcpu": 8, "reason": "限流用例"},
                headers=headers,
            )
            assert resp.status_code == 200, resp.text
        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}", json={"vcpu": 8, "reason": "限流用例"}, headers=headers
        )
        assert resp.status_code == 429
        assert resp.json()["code"] == "RATE_LIMITED"
        other = await admin_headers(sm, client, username="admin-two")
        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}", json={"vcpu": 8, "reason": "另一人"}, headers=other
        )
        assert resp.status_code == 200, resp.text


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
