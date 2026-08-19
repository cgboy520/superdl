from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.adminapi.service import create_admin
from app.modules.catalog import inventory
from app.modules.catalog.models import PlatformImage, Sku


def make_sku(**overrides) -> Sku:
    defaults = {
        "name": "RTX 4090 · 共享标准",
        "gpu_model": "RTX4090",
        "tier": "shared_std",
        "gpu_cores_pct": 50,
        "vram_gb": 8,
        "oversell_cores": Decimal("1.50"),
        "oversell_vram": Decimal("1.10"),
        "pool_label": "hami",
        "vcpu": 8,
        "mem_gb": 32,
        "disk_gb": 100,
        "price_hourly": Decimal("1.6800"),
        "max_gpus_per_instance": 1,
        "cuda_max": "12.8",
        "status": "on",
    }
    defaults.update(overrides)
    return Sku(**defaults)


async def seed_skus(sm: async_sessionmaker[AsyncSession]) -> None:
    async with sm() as session:
        session.add_all(
            [
                make_sku(),
                make_sku(
                    name="RTX 4090 · 独享",
                    tier="dedicated",
                    gpu_cores_pct=100,
                    vram_gb=24,
                    pool_label="kata",
                    price_hourly=Decimal("3.9900"),
                    max_gpus_per_instance=8,
                ),
                make_sku(
                    name="A100 · 独享(下架)",
                    gpu_model="A100",
                    tier="dedicated",
                    vram_gb=80,
                    pool_label="kata",
                    price_hourly=Decimal("9.9900"),
                    status="off",
                ),
            ]
        )
        session.add(
            PlatformImage(
                framework="PyTorch",
                framework_version="2.9.0",
                python_version="3.12",
                cuda_version="12.8",
                image_ref="registry.superdl.local/pytorch:2.9.0-cu128",
            )
        )
        await session.commit()
    inventory.clear_cache()


async def admin_headers(
    sm: async_sessionmaker[AsyncSession], client: AsyncClient, role: str = "admin"
) -> dict[str, str]:
    async with sm() as session:
        await create_admin(session, f"{role}-user", "pass1234", role)
    resp = await client.post(
        "/api/admin/v1/auth/login", json={"username": f"{role}-user", "password": "pass1234"}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


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

    async def test_price_serialized_as_string(self, client: AsyncClient, sm):
        await seed_skus(sm)
        resp = await client.get("/api/v1/skus")
        prices = [s["price_hourly"] for s in resp.json()]
        assert "1.6800" in prices  # 字符串且保留 4 位 scale

    async def test_inventory_cached_30s(self, client: AsyncClient, sm, monkeypatch):
        await seed_skus(sm)
        calls = {"n": 0}

        async def counting_provider(_sku):
            calls["n"] += 1
            return 5

        monkeypatch.setattr(inventory, "_provider", counting_provider)
        inventory.clear_cache()
        await client.get("/api/v1/skus")
        first = calls["n"]
        await client.get("/api/v1/skus")
        assert calls["n"] == first  # 30s 窗口内不重复计算
        data = (await client.get("/api/v1/skus")).json()
        assert all(s["available_count"] == 5 for s in data)

    async def test_images(self, client: AsyncClient, sm):
        await seed_skus(sm)
        resp = await client.get("/api/v1/images")
        assert resp.json()[0]["framework"] == "PyTorch"


class TestAdminSku:
    async def test_admin_crud(self, client: AsyncClient, sm):
        headers = await admin_headers(sm, client)
        body = {
            "name": "H100 · MIG",
            "gpu_model": "H100",
            "tier": "mig",
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
        assert resp.json()["status"] == "off"  # 默认不上架

        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}",
            json={"status": "on", "price_hourly": "2.8000"},
            headers=headers,
        )
        assert resp.json()["price_hourly"] == "2.8000"

        market = (await client.get("/api/v1/skus")).json()
        assert any(s["id"] == sku_id for s in market)

    async def test_readonly_cannot_write(self, client: AsyncClient, sm):
        headers = await admin_headers(sm, client, role="readonly")
        resp = await client.get("/api/admin/v1/skus", headers=headers)
        assert resp.status_code == 200
        resp = await client.post(
            "/api/admin/v1/skus",
            json={
                "name": "x",
                "gpu_model": "x",
                "tier": "mig",
                "vram_gb": 1,
                "pool_label": "mig",
                "vcpu": 1,
                "mem_gb": 1,
                "price_hourly": "1",
            },
            headers=headers,
        )
        assert resp.status_code == 403

    async def test_finance_cannot_write_sku(self, client: AsyncClient, sm):
        headers = await admin_headers(sm, client, role="finance")
        resp = await client.patch("/api/admin/v1/skus/1", json={"status": "on"}, headers=headers)
        assert resp.status_code == 403

    async def test_user_token_rejected(self, client: AsyncClient, sm):
        from tests.test_account_auth import register

        data = await register(client, "13800000088")
        resp = await client.get(
            "/api/admin/v1/skus", headers={"Authorization": f"Bearer {data['access_token']}"}
        )
        assert resp.status_code == 401

    async def test_admin_login_wrong_password(self, client: AsyncClient, sm):
        await admin_headers(sm, client)
        resp = await client.post(
            "/api/admin/v1/auth/login", json={"username": "admin-user", "password": "wrong"}
        )
        assert resp.json()["code"] == "LOGIN_FAILED"
