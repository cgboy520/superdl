"""管理端镜像 CRUD/预热契约:审计、覆盖率计算、公开 is_prewarmed 语义。"""

import pytest
from sqlalchemy import select

from app.core.audit import AuditLog
from app.core.k8s import set_orchestrator
from app.core.k8s.fake import FakeOrchestrator
from app.core.outbox import OutboxTask, drain
from app.modules.catalog.models import ImageNodeCache
from app.modules.catalog.prewarm import prewarm_patrol
from tests.test_catalog import admin_headers

pytestmark = pytest.mark.usefixtures("fake")

IMAGE_BODY = {
    "framework": "PyTorch",
    "framework_version": "2.9.0",
    "python_version": "3.12",
    "cuda_version": "12.8",
    "image_ref": "registry.superdl.local/pytorch:2.9.0-cu128",
}


@pytest.fixture
def fake():
    orch = FakeOrchestrator(auto_ready=False)
    set_orchestrator(orch)
    yield orch
    set_orchestrator(None)


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
        await prewarm_patrol(sm)  # 铺 3 行
        resp = await client.request(
            "DELETE",
            f"/api/admin/v1/images/{image_id}",
            json={"reason": "下线旧版本"},
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
            assert any((log.detail or {}).get("reason") == "下线旧版本" for log in logs)

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
        await prewarm_patrol(sm)  # 3 行 pending(已各带 1 个任务)
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
            assert len(tasks) == 6  # 巡检 3 + 手动 3(handler 幂等,重复无害)

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

        # 零 cache 行:回落 prewarm_enabled(dev/演示零回归)
        imgs = (await client.get("/api/v1/images")).json()
        assert imgs[0]["is_prewarmed"] is True

        # 全链路 cached → true
        await prewarm_patrol(sm)
        await drain(sm)
        await prewarm_patrol(sm)
        imgs = (await client.get("/api/v1/images")).json()
        assert imgs[0]["is_prewarmed"] is True
        admin_row = (await client.get("/api/admin/v1/images", headers=ah)).json()[0]
        assert admin_row["coverage"] == {"cached": 3, "total": 3, "pct": 100}

        # 一节点失效重拉失败 → 覆盖率 2/3 < 90% → false,failed_nodes=1
        fake.auto_prewarm = False
        fake.set_prewarm_state("fake-kata-node-1", IMAGE_BODY["image_ref"], "failed")
        async with sm() as session:
            row = (
                await session.execute(
                    select(ImageNodeCache).where(ImageNodeCache.node_name == "fake-kata-node-1")
                )
            ).scalar_one()
            row.status = "pulling"  # 模拟复检中失败
            await session.commit()
        await prewarm_patrol(sm)
        imgs = (await client.get("/api/v1/images")).json()
        assert imgs[0]["is_prewarmed"] is False
        admin_row = (await client.get("/api/admin/v1/images", headers=ah)).json()[0]
        assert admin_row["coverage"]["cached"] == 2
        assert admin_row["failed_nodes"] == 1

        # 关闭预热 → 公开标志立刻 false
        await client.patch(
            f"/api/admin/v1/images/{image_id}", json={"prewarm_enabled": False}, headers=ah
        )
        imgs = (await client.get("/api/v1/images")).json()
        assert imgs[0]["is_prewarmed"] is False
