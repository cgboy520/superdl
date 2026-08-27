"""上架硬校验(SKU_NOT_SELLABLE/force)、容量预览、SKU 列表容量组装列。"""

from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.timeutil import now_utc
from app.modules.nodes.models import NodeSpec
from tests.test_catalog import admin_headers, make_sku


async def seed_spec(sm: async_sessionmaker[AsyncSession], **overrides) -> None:
    defaults = {
        "node_name": "gpu-node-1",
        "pool_label": "hami",
        "unlabeled": False,
        "gpu_model_raw": "NVIDIA GeForce RTX 4090",
        "gpu_model": "RTX4090",
        "label_synced": True,
        "gpu_count": 4,
        "gpu_used": 0,
        "vram_gb": 24,
        "vcpu": 64,
        "mem_gb": 256,
        "disk_gb": 2000,
        "status": "Ready",
        "last_seen": now_utc(),
    }
    defaults.update(overrides)
    async with sm() as session:
        session.add(NodeSpec(**defaults))
        await session.commit()


async def seed_one_sku(sm, **overrides) -> int:
    async with sm() as session:
        sku = make_sku(**overrides)
        session.add(sku)
        await session.commit()
        return sku.id


class TestSellableGate:
    async def test_turn_on_without_matching_node_409(self, client: AsyncClient, sm):
        sku_id = await seed_one_sku(sm, status="off", gpu_model="H100", pool_label="hami")
        headers = await admin_headers(sm, client)
        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}", json={"status": "on", "reason": "用例"}, headers=headers
        )
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["code"] == "SKU_NOT_SELLABLE"
        assert body["message_key"] == "catalog.skuNotSellable"
        assert body["params"] == {"model": "H100", "pool": "hami"}

    async def test_matching_ready_node_passes(self, client: AsyncClient, sm):
        await seed_spec(sm)
        sku_id = await seed_one_sku(sm, status="off")  # RTX4090 × hami
        headers = await admin_headers(sm, client)
        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}", json={"status": "on", "reason": "用例"}, headers=headers
        )
        assert resp.status_code == 200

    async def test_not_ready_node_rejected(self, client: AsyncClient, sm):
        await seed_spec(sm, status="NotReady")
        sku_id = await seed_one_sku(sm, status="off")
        headers = await admin_headers(sm, client)
        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}", json={"status": "on", "reason": "用例"}, headers=headers
        )
        assert resp.status_code == 409

    async def test_off_and_edit_skip_gate(self, client: AsyncClient, sm):
        """下架与已上架编辑不触发校验(仅 off→on 的边)。"""
        sku_id = await seed_one_sku(sm, status="on", gpu_model="H100")
        headers = await admin_headers(sm, client)
        r1 = await client.patch(
            f"/api/admin/v1/skus/{sku_id}", json={"vcpu": 16, "reason": "用例"}, headers=headers
        )
        r2 = await client.patch(
            f"/api/admin/v1/skus/{sku_id}",
            json={"status": "off", "reason": "用例"},
            headers=headers,
        )
        assert r1.status_code == 200 and r2.status_code == 200


class TestCapacityPreview:
    async def test_numbers_and_est(self, client: AsyncClient, sm):
        await seed_spec(sm)  # 4 卡 Ready
        await seed_spec(sm, node_name="gpu-node-2", gpu_count=2, status="NotReady")
        headers = await admin_headers(sm, client, role="readonly")
        resp = await client.get(
            "/api/admin/v1/skus/capacity-preview",
            params={
                "gpu_model": "RTX4090",
                "pool_label": "hami",
                "gpu_cores_pct": 50,
                "oversell_cores": "1.50",
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["matching_nodes"] == 2
        assert body["ready_gpus"] == 4 and body["total_gpus"] == 6
        assert body["est_instances"] == 4 * 3  # ⌊100×1.5/50⌋=3
        assert body["warnings"] == []

    async def test_vram_warning_and_no_node(self, client: AsyncClient, sm):
        await seed_spec(sm)  # vram 24
        headers = await admin_headers(sm, client)
        resp = await client.get(
            "/api/admin/v1/skus/capacity-preview",
            params={
                "gpu_model": "RTX4090",
                "pool_label": "hami",
                "vram_gb": 48,
            },
            headers=headers,
        )
        codes = [w["code"] for w in resp.json()["warnings"]]
        assert codes == ["vram_exceeds_node"]
        resp2 = await client.get(
            "/api/admin/v1/skus/capacity-preview",
            params={"gpu_model": "H20", "pool_label": "kata"},
            headers=headers,
        )
        body2 = resp2.json()
        assert body2["ready_gpus"] == 0
        assert [w["code"] for w in body2["warnings"]] == ["no_ready_node"]

    async def test_unrecognized_model_warning(self, client: AsyncClient, sm):
        headers = await admin_headers(sm, client)
        resp = await client.get(
            "/api/admin/v1/skus/capacity-preview",
            params={"gpu_model": "Banana 9000", "pool_label": "hami"},
            headers=headers,
        )
        codes = [w["code"] for w in resp.json()["warnings"]]
        assert "unrecognized_model" in codes and "no_ready_node" in codes


class TestSkuListAssembly:
    async def test_capacity_and_sold_columns(self, client: AsyncClient, sm):
        await seed_spec(sm)  # RTX4090×hami 4 卡 Ready
        sku_id = await seed_one_sku(sm)  # 共享 50%, oversell 1.50
        async with sm() as session:
            from app.modules.orchestrator.models import Instance

            session.add(
                Instance(
                    user_id=1,
                    sku_id=sku_id,
                    k8s_namespace="tenant-1",
                    uuid="i-cap-test",
                    name="cap-test",
                    status="running",
                    gpu_count=2,
                    image_ref="img:latest",
                    spec={},
                    jupyter_token="t",
                    price_hourly=Decimal("1.6800"),
                )
            )
            await session.commit()
        headers = await admin_headers(sm, client)
        resp = await client.get("/api/admin/v1/skus", headers=headers)
        row = next(r for r in resp.json() if r["id"] == sku_id)
        assert row["capacity_gpus"] == 4
        # 名义算力 2×0.5=1.0;物理 4 卡 → actual 0.25;可售 4×1.5=6 → share≈0.17
        assert row["actual_oversell"] == "0.25"
        assert Decimal(row["sold_share"]) == Decimal("0.17")

    async def test_empty_ledger_defaults(self, client: AsyncClient, sm):
        sku_id = await seed_one_sku(sm)
        headers = await admin_headers(sm, client)
        resp = await client.get("/api/admin/v1/skus", headers=headers)
        row = next(r for r in resp.json() if r["id"] == sku_id)
        assert row["capacity_gpus"] == 0
        assert row["sold_share"] is None and row["actual_oversell"] is None


class TestGpuModelAggregates:
    async def test_per_gpu_ratio_min_across_nodes(self, client: AsyncClient, sm):
        await seed_spec(sm)  # 4 卡 64c/256G → 16c/64G 每卡
        await seed_spec(sm, node_name="gpu-node-2", gpu_count=8, vcpu=64, mem_gb=256)  # 8c/32G
        headers = await admin_headers(sm, client, role="readonly")
        resp = await client.get("/api/admin/v1/cluster/gpu-models", headers=headers)
        row = next(r for r in resp.json() if r["gpu_model"] == "RTX4090")
        assert row["vcpu_per_gpu"] == 8 and row["mem_gb_per_gpu"] == 32
        assert row["ready_gpu_total"] == 12
