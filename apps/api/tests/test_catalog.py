from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.adminapi.service import create_admin
from app.modules.catalog.models import PlatformImage, Sku


def make_sku(**overrides) -> Sku:
    defaults = {
        "name": "RTX 4090 · 共享标准",
        "gpu_model": "RTX4090",
        "tier": "shared",
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
    sm: async_sessionmaker[AsyncSession],
    client: AsyncClient,
    role: str = "admin",
    *,
    username: str | None = None,
) -> dict[str, str]:
    """建管理员(默认用户名 {role}-user)并登录到正式 token:全角色强制 TOTP,
    登录只回绑定票,走完整绑定流。"""
    name = username or f"{role}-user"
    async with sm() as session:
        await create_admin(session, name, "pass1234", role)
    resp = await client.post(
        "/api/admin/v1/auth/login", json={"username": name, "password": "pass1234"}
    )
    assert resp.status_code == 200, resp.text
    token = await complete_mfa_setup(client, resp.json()["ticket"])
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


class TestSellablePerGpu:
    def test_decimal_floor_division(self):
        """每卡可售数必须走 Decimal 整除:float 会把 100×1.15 算成 114.999…→ 22,
        同一 SKU 市场库存与管理端容量预览各差一台(挂了 = 两处口径再次分叉)。"""
        from app.modules.catalog.service import sellable_per_gpu

        assert sellable_per_gpu("hami", 5, Decimal("1.15")) == 23
        assert sellable_per_gpu("hami", 50, Decimal("1.50")) == 3
        assert sellable_per_gpu("kata", 100, Decimal("1.50")) == 1  # 整卡池不折算
        assert sellable_per_gpu("mig", 100, Decimal("1.50")) == 1  # MIG 硬切分不超卖


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
        assert resp.json()["status"] == "off"  # 默认不上架

        # 台账无匹配节点:force 上架(硬校验 409 见 test_sku_capacity.TestSellableGate)
        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}?force=true",
            json={"status": "on", "price_hourly": "2.8000", "reason": "上架调价"},
            headers=headers,
        )
        assert resp.json()["price_hourly"] == "2.8000"

        market = (await client.get("/api/v1/skus")).json()
        assert any(s["id"] == sku_id for s in market)

    async def test_isolation_change_only_when_off_sale(self, client: AsyncClient, sm):
        """在售规格不许改池、也不许改 MIG 切片;下架后两者可一起改。

        挂了说明:市场页挂着的「共享·标准」能被静默改成「共享·经济」、或切片从
        1g.10gb 变成 2g.20gb ——展示名、规格列与性能承诺都变了,新下单的人拿到的
        不是他看到的那件商品。
        """
        from tests.helpers import seed_node_spec

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
        # 台账有 mig × H100 Ready:直接上架成功(无需 force)
        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}",
            json={"status": "on", "reason": "上架"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        # 在售改池 → 409,force 也不放行(这不是「容量不足」而是「换了商品」)
        for qs in ("", "?force=true"):
            resp = await client.patch(
                f"/api/admin/v1/skus/{sku_id}{qs}",
                json={"pool_label": "hami", "mig_profile": None, "reason": "迁池"},
                headers=headers,
            )
            assert resp.status_code == 409, resp.text
            assert resp.json()["message_key"] == "catalog.isolationChangeNeedsOffSale"
        # 在售改切片同样 409:切片名就是市场页规格列展示的内容
        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}",
            json={"mig_profile": "2g.20gb", "reason": "换切片"},
            headers=headers,
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["message_key"] == "catalog.isolationChangeNeedsOffSale"
        # 改其他字段(价格)不受影响
        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}",
            json={"price_hourly": "2.6000", "reason": "调价"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        # 下架后可改池,但必须连切片一起改:只改池会被「切片与池不符」拦下
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
        """改 SKU 撞到另一条的业务唯一键给 409,不是漏出 500。

        挂了说明:唯一键的七列里有五列(池 / 切片 / 算力份额 / vCPU / 内存)可改,
        运营把 A 改成与 B 同规格时会看到「服务器错误」而不是「该规格已存在」。
        """
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
        """档位与池必须配对。

        挂了说明:能建出 tier=dedicated 却挂 hami 池的 SKU —— 卖的是整卡直通,
        跑的是软切分超卖(隔离机制的派发键是池,见 core/gpu_adapter)。
        """
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
        # 共享档不能落 kata 池
        resp = await client.post(
            "/api/admin/v1/skus",
            json={**base, "name": "假共享", "tier": "shared", "pool_label": "kata"},
            headers=headers,
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["message_key"] == "catalog.tierPoolMismatch"
        # mig 池必须带切片;非 mig 池不许带切片
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

    async def test_readonly_cannot_write(self, client: AsyncClient, sm):
        headers = await admin_headers(sm, client, role="readonly")
        resp = await client.get("/api/admin/v1/skus", headers=headers)
        assert resp.status_code == 200
        resp = await client.post(
            "/api/admin/v1/skus",
            json={
                "name": "x",
                "gpu_model": "x",
                "tier": "shared",
                "vram_gb": 1,
                "pool_label": "mig",
                "vcpu": 1,
                "mem_gb": 1,
                "price_hourly": "1",
            },
            headers=headers,
        )
        assert resp.status_code == 403
