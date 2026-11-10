"""Admin image CRUD / prewarm contract: audit, coverage computation (overall and per pool),
public is_prewarmed and download size semantics."""

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditLog
from app.core.k8s.fake import FakeOrchestrator
from app.core.outbox import OutboxTask
from app.modules.catalog.models import ImageNodeCache, PlatformImage
from app.modules.catalog.prewarm import prewarm_patrol
from app.modules.nodes.patrol import node_spec_patrol
from tests.helpers import IMAGE_PYTORCH, admin_headers, create_test_sku, drain

pytestmark = pytest.mark.usefixtures("fake")

IMAGE_BODY = {
    "framework": "PyTorch",
    "framework_version": "2.9.0",
    "python_version": "3.12",
    "cuda_version": "12.8",
    "image_ref": IMAGE_PYTORCH,
}


async def create_image(client, headers) -> int:
    resp = await client.post("/api/admin/v1/images", json=IMAGE_BODY, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


class TestImageCrud:
    async def test_create_with_audit_and_conflict(self, client, sm) -> None:
        ah = await admin_headers(sm, client, role="ops")
        image_id = await create_image(client, ah)

        async with sm() as session:
            logs = (
                await session.execute(
                    select(AuditLog).where(AuditLog.target == f"image:{image_id}")
                )
            ).scalars()
            assert any(log.detail == {"image_ref": IMAGE_BODY["image_ref"]} for log in logs)

        resp = await client.post("/api/admin/v1/images", json=IMAGE_BODY, headers=ah)
        assert resp.status_code == 409
        assert resp.json()["code"] == "CONFLICT"

    async def test_delete_cascades_and_audits_reason(self, client, sm) -> None:
        ah = await admin_headers(sm, client, role="ops")
        image_id = await create_image(client, ah)
        await prewarm_patrol(sm)
        resp = await client.request(
            "DELETE",
            f"/api/admin/v1/images/{image_id}",
            json={"reason": "retiring the old version"},
            headers=ah,
        )
        assert resp.status_code == 204
        async with sm() as session:
            rows = list((await session.execute(select(ImageNodeCache))).scalars())
            assert rows == []
            logs = (
                await session.execute(
                    select(AuditLog).where(AuditLog.target == f"image:{image_id}")
                )
            ).scalars()
            assert any(
                (log.detail or {}).get("reason") == "retiring the old version" for log in logs
            )

    async def test_update_ref_change_clears_cache_rows(self, client, sm) -> None:
        ah = await admin_headers(sm, client, role="ops")
        image_id = await create_image(client, ah)
        await prewarm_patrol(sm)
        resp = await client.patch(
            f"/api/admin/v1/images/{image_id}",
            json={"image_ref": "registry.superdl.local/pytorch:2.9.1-cu128"},
            headers=ah,
        )
        assert resp.status_code == 200
        assert resp.json()["coverage"] == {"cached": 0, "total": 0, "pct": 0}


class TestPrewarmContract:
    async def test_manual_prewarm_enqueues_non_cached(self, client, sm) -> None:
        ah = await admin_headers(sm, client, role="ops")
        image_id = await create_image(client, ah)
        await prewarm_patrol(sm)
        resp = await client.post(f"/api/admin/v1/images/{image_id}/prewarm", headers=ah)
        assert resp.status_code == 200
        assert resp.json()["enqueued"] == 3
        async with sm() as session:
            tasks = list(
                (
                    await session.execute(
                        select(OutboxTask).where(
                            OutboxTask.type == "image.prewarm", OutboxTask.status == "pending"
                        )
                    )
                ).scalars()
            )
            assert len(tasks) == 6

    async def test_disabled_image_prewarm_rejected(self, client, sm) -> None:
        ah = await admin_headers(sm, client, role="ops")
        image_id = await create_image(client, ah)
        await client.patch(
            f"/api/admin/v1/images/{image_id}", json={"prewarm_enabled": False}, headers=ah
        )
        resp = await client.post(f"/api/admin/v1/images/{image_id}/prewarm", headers=ah)
        assert resp.status_code == 400
        assert resp.json()["code"] == "VALIDATION_ERROR"


class TestPublicIsPrewarmed:
    async def test_coverage_threshold_drives_public_flag(
        self, client, sm, fake: FakeOrchestrator
    ) -> None:
        ah = await admin_headers(sm, client, role="ops")
        image_id = await create_image(client, ah)

        # no target node has it yet: not prewarmed (the console must not promise a fast start)
        imgs = (await client.get("/api/v1/images")).json()
        assert imgs[0]["is_prewarmed"] is False

        await prewarm_patrol(sm)
        await drain(sm)
        await prewarm_patrol(sm)
        imgs = (await client.get("/api/v1/images")).json()
        assert imgs[0]["is_prewarmed"] is True
        admin_row = (await client.get("/api/admin/v1/images", headers=ah)).json()[0]
        assert admin_row["coverage"] == {"cached": 3, "total": 3, "pct": 100}

        fake.auto_prewarm = False
        fake.set_prewarm_state("fake-kata-node-1", IMAGE_BODY["image_ref"], "failed")
        async with sm() as session:
            row = (
                await session.execute(
                    select(ImageNodeCache).where(ImageNodeCache.node_name == "fake-kata-node-1")
                )
            ).scalar_one()
            row.status = "pulling"
            await session.commit()
        await prewarm_patrol(sm)
        imgs = (await client.get("/api/v1/images")).json()
        assert imgs[0]["is_prewarmed"] is False
        admin_row = (await client.get("/api/admin/v1/images", headers=ah)).json()[0]
        assert admin_row["coverage"]["cached"] == 2
        assert admin_row["failed_nodes"] == 1

        await client.patch(
            f"/api/admin/v1/images/{image_id}", json={"prewarm_enabled": False}, headers=ah
        )
        imgs = (await client.get("/api/v1/images")).json()
        assert imgs[0]["is_prewarmed"] is False


async def _cached_everywhere_but_kata(client, sm, ah) -> int:
    """One image cached on the hami and mig nodes, failed on the kata node; inventory synced."""
    image_id = await create_image(client, ah)
    await node_spec_patrol(sm)
    await prewarm_patrol(sm)
    await drain(sm)
    await prewarm_patrol(sm)
    async with sm() as session:
        row = (
            await session.execute(
                select(ImageNodeCache).where(ImageNodeCache.node_name == "fake-kata-node-1")
            )
        ).scalar_one()
        row.status = "failed"
        await session.commit()
    return image_id


async def _image_field(client: AsyncClient, image_id: int, field: str, sku_id: int | None = None):
    params = {"sku_id": sku_id} if sku_id is not None else {}
    resp = await client.get("/api/v1/images", params=params)
    assert resp.status_code == 200, resp.text
    return {i["id"]: i[field] for i in resp.json()}[image_id]


class TestPoolCoverageAndDownloadSize:
    async def test_flag_follows_the_sku_pool_and_never_a_cpu_pool(
        self, client, sm: async_sessionmaker[AsyncSession], fake: FakeOrchestrator
    ) -> None:
        ah = await admin_headers(sm, client, role="ops")
        image_id = await _cached_everywhere_but_kata(client, sm, ah)
        hami = await create_test_sku(sm)
        kata = await create_test_sku(
            sm, tier="dedicated", pool_label="kata", gpu_cores_pct=100, oversell_cores=Decimal("1")
        )
        cpu = await create_test_sku(
            sm, gpu_model="CPU", tier="cpu", pool_label="cpu", gpu_cores_pct=0, vram_gb=0
        )
        assert await _image_field(client, image_id, "is_prewarmed", hami) is True
        assert await _image_field(client, image_id, "is_prewarmed", kata) is False
        assert await _image_field(client, image_id, "is_prewarmed", cpu) is False
        # overall 2 of 3 target nodes is below the 90 % default
        assert await _image_field(client, image_id, "is_prewarmed") is False
        missing = await client.get("/api/v1/images", params={"sku_id": 999999})
        assert missing.status_code == 404

        row = (await client.get("/api/admin/v1/images", headers=ah)).json()[0]
        assert row["coverage"] == {"cached": 2, "total": 3, "pct": 66}
        assert row["coverage_by_pool"] == [
            {"pool_label": "hami", "cached": 1, "total": 1, "pct": 100},
            {"pool_label": "kata", "cached": 0, "total": 1, "pct": 0},
            {"pool_label": "mig", "cached": 1, "total": 1, "pct": 100},
        ]
        nodes = (await client.get(f"/api/admin/v1/images/{image_id}/nodes", headers=ah)).json()
        assert {n["node_name"]: n["pool_label"] for n in nodes} == {
            "fake-hami-node-1": "hami",
            "fake-kata-node-1": "kata",
            "fake-mig-node-1": "mig",
        }

    async def test_download_bytes_per_pool_architecture_and_stale_after_ref_change(
        self, client, sm: async_sessionmaker[AsyncSession], fake: FakeOrchestrator
    ) -> None:
        ah = await admin_headers(sm, client, role="ops")
        image_id = await create_image(client, ah)
        assert await _image_field(client, image_id, "download_bytes") is None
        await node_spec_patrol(sm)
        async with sm() as session:
            img = await session.get(PlatformImage, image_id)
            assert img is not None
            img.download_bytes = {"amd64": 9_000, "arm64": 11_000}
            img.download_ref = img.image_ref
            await session.commit()
        hami = await create_test_sku(sm)

        # no SKU: the largest; the hami pool's nodes are amd64
        assert await _image_field(client, image_id, "download_bytes") == 11_000
        assert await _image_field(client, image_id, "download_bytes", hami) == 9_000
        row = (await client.get("/api/admin/v1/images", headers=ah)).json()[0]
        assert row["download_bytes_by_arch"] == {"amd64": 9_000, "arm64": 11_000}

        new_ref = IMAGE_PYTORCH.replace("2.9.0", "2.9.1")
        resp = await client.patch(
            f"/api/admin/v1/images/{image_id}", json={"image_ref": new_ref}, headers=ah
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["download_bytes_by_arch"] == {}
        assert await _image_field(client, image_id, "download_bytes", hami) is None
