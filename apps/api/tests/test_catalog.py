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


async def complete_mfa_setup(client: AsyncClient, ticket: str) -> str:
    """mfa_setup 票 → begin → confirm(当前 TOTP)→ access token。供管理端测试复用。"""
    import pyotp

    begin = await client.post("/api/admin/v1/auth/mfa/setup/begin", json={"ticket": ticket})
    assert begin.status_code == 200, begin.text
    secret = begin.json()["secret"]
    confirm = await client.post(
        "/api/admin/v1/auth/mfa/setup/confirm",
        json={"ticket": ticket, "code": pyotp.TOTP(secret).now()},
    )
    assert confirm.status_code == 200, confirm.text
    return confirm.json()["access_token"]


async def admin_headers(
    sm: async_sessionmaker[AsyncSession], client: AsyncClient, role: str = "admin"
) -> dict[str, str]:
    async with sm() as session:
        await create_admin(session, f"{role}-user", "pass1234", role)
    resp = await client.post(
        "/api/admin/v1/auth/login", json={"username": f"{role}-user", "password": "pass1234"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    if body["status"] == "ok":
        token = body["access_token"]
    else:
        # admin/finance 强制 TOTP:走完整绑定流拿 token
        token = await complete_mfa_setup(client, body["ticket"])
    return {"Authorization": f"Bearer {token}"}


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

    async def test_inventory_cached_while_signature_unchanged(
        self, client: AsyncClient, sm, monkeypatch
    ):
        """签名(台账行数/max(updated_at))未变时不重复计算;签名变化立即触发重算。"""
        await seed_skus(sm)
        calls = {"n": 0}

        async def counting_provider(_session, skus):
            calls["n"] += 1
            return {s.id: 5 for s in skus}

        monkeypatch.setattr(inventory, "_provider", counting_provider)
        inventory.clear_cache()
        await client.get("/api/v1/skus")
        first = calls["n"]
        assert first == 1  # 批量接口:一次调用算完全部 SKU,不是每 SKU 一次
        await client.get("/api/v1/skus")
        assert calls["n"] == first  # 签名未变不重复计算
        data = (await client.get("/api/v1/skus")).json()
        assert all(s["available_count"] == 5 for s in data)
        # 台账写入(签名变化)→ 下一次查询立即重算,不等任何 TTL
        from tests.helpers import seed_node_spec

        await seed_node_spec(sm, node_name="node-sig-1")
        await client.get("/api/v1/skus")
        assert calls["n"] == first + 1

    async def test_inventory_stale_on_provider_error(self, client: AsyncClient, sm, monkeypatch):
        """台账查询故障且有旧快照:市场页展示陈旧库存而不是 500(/skus 免登录无限流)。"""
        await seed_skus(sm)
        calls = {"n": 0}

        async def flaky_provider(_session, skus):
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("seeded ledger failure")
            return {s.id: 7 for s in skus}

        monkeypatch.setattr(inventory, "_provider", flaky_provider)
        inventory.clear_cache()
        assert (await client.get("/api/v1/skus")).json()[0]["available_count"] == 7
        assert inventory._cache is not None
        # 拨动签名触发重算(等价台账巡检写入),provider 故障 → 陈旧值兜底
        from tests.helpers import seed_node_spec

        await seed_node_spec(sm, node_name="node-stale-1")
        resp = await client.get("/api/v1/skus")
        assert resp.status_code == 200
        assert resp.json()[0]["available_count"] == 7  # 陈旧值兜底


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

        # 台账无匹配节点:上架被硬校验拦下,force 放行
        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}",
            json={"status": "on", "price_hourly": "2.8000", "reason": "上架调价"},
            headers=headers,
        )
        assert resp.status_code == 409 and resp.json()["code"] == "SKU_NOT_SELLABLE"
        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}?force=true",
            json={"status": "on", "price_hourly": "2.8000", "reason": "上架调价"},
            headers=headers,
        )
        assert resp.json()["price_hourly"] == "2.8000"

        market = (await client.get("/api/v1/skus")).json()
        assert any(s["id"] == sku_id for s in market)

    async def test_on_sale_sku_pool_change_also_validated(self, client: AsyncClient, sm):
        """在售 SKU 改 型号/池 同样过 sellable 硬校验(P2):无台账匹配的池方向 409,
        force 放行(挂了 = 在售 SKU 可被改成指向无 Ready 节点的池,用户创建才失败)。"""
        from tests.helpers import seed_node_spec

        await seed_node_spec(sm, pool_label="mig", gpu_model="H100")
        headers = await admin_headers(sm, client)
        body = {
            "name": "H100 · MIG 在售",
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
        sku_id = resp.json()["id"]
        # 台账有 mig × H100 Ready:直接上架成功(无需 force)
        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}",
            json={"status": "on", "reason": "上架"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        # 在售改 pool_label=kata(台账无 kata Ready)→ 409
        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}",
            json={"pool_label": "kata", "reason": "迁池"},
            headers=headers,
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "SKU_NOT_SELLABLE"
        # force 放行;改其他字段(价格)不受校验影响
        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}?force=true",
            json={"pool_label": "kata", "reason": "迁池"},
            headers=headers,
        )
        assert resp.status_code == 200
        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}",
            json={"price_hourly": "2.6000", "reason": "调价"},
            headers=headers,
        )
        assert resp.status_code == 200

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

    async def test_user_token_rejected(self, client: AsyncClient, sm):
        from tests.test_account_auth import register

        data = await register(client, "13800000088")
        resp = await client.get(
            "/api/admin/v1/skus", headers={"Authorization": f"Bearer {data['access_token']}"}
        )
        assert resp.status_code == 401
