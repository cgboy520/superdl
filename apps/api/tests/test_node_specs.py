"""节点台账巡检:铺行收敛/未打标可见/装机登记兜底/Missing 保留删行/label 收敛与失败自愈。"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.k8s import set_orchestrator
from app.core.k8s.base import NodeInfo
from app.core.k8s.fake import FakeOrchestrator
from app.modules.nodes.models import NodeSpec
from app.modules.nodes.patrol import node_spec_patrol


@pytest.fixture
def fake():
    orch = FakeOrchestrator()
    set_orchestrator(orch)
    yield orch
    set_orchestrator(None)


async def test_patrol_converges_and_labels(sm, fake):
    counts = await node_spec_patrol(sm)
    assert counts["upserted"] == 3  # fake 三池各一节点
    async with sm() as session:
        rows = {r.node_name: r for r in (await session.execute(select(NodeSpec))).scalars()}
    assert rows["fake-hami-node-1"].gpu_model == "RTX4090"
    assert rows["fake-hami-node-1"].vram_gb == 24  # DEFAULT_VRAM_GB 兜底
    assert rows["fake-mig-node-1"].gpu_model == "H100"
    # label 收敛已写入 fake
    assert fake.node_labels["fake-hami-node-1"]["superdl.io/gpu-model"] == "RTX4090"
    async with sm() as session:
        row = (
            await session.execute(select(NodeSpec).where(NodeSpec.node_name == "fake-hami-node-1"))
        ).scalar_one()
        assert row.label_synced is True
    # 二轮幂等
    counts2 = await node_spec_patrol(sm)
    assert counts2["upserted"] == 3 and counts2["removed"] == 0


async def test_unlabeled_node_visible(sm, fake):
    fake.unlabeled_nodes.append(
        NodeInfo(
            name="rogue-node",
            pool_label="unknown",
            gpu_total=4,
            gpu_used=0,
            status="Ready",
            gpu_model_label="NVIDIA-GeForce-RTX-4090",
        )
    )
    await node_spec_patrol(sm)
    async with sm() as session:
        row = (
            await session.execute(select(NodeSpec).where(NodeSpec.node_name == "rogue-node"))
        ).scalar_one()
    assert row.unlabeled is True and row.pool_label is None
    assert row.gpu_model == "RTX4090"  # GFD 标签兜底归一化


async def test_missing_then_removed(sm, fake):
    fake.unlabeled_nodes.append(
        NodeInfo(
            name="gone-node",
            pool_label="hami",
            gpu_total=1,
            gpu_used=0,
            status="Ready",
        )
    )
    await node_spec_patrol(sm)
    fake.unlabeled_nodes.clear()
    counts = await node_spec_patrol(sm)
    assert counts["missing"] == 1
    async with sm() as session:
        row = (
            await session.execute(select(NodeSpec).where(NodeSpec.node_name == "gone-node"))
        ).scalar_one()
        assert row.status == "Missing"
        # 篡改 last_seen 到 8 天前 → 下轮删行
        row.last_seen = datetime.now(UTC) - timedelta(days=8)
        await session.commit()
    counts = await node_spec_patrol(sm)
    assert counts["removed"] == 1
    async with sm() as session:
        assert (
            await session.execute(select(NodeSpec).where(NodeSpec.node_name == "gone-node"))
        ).scalar_one_or_none() is None


async def test_enrollment_fallback_wins_over_gfd(sm, fake):
    """装机登记(nvidia-smi 全卡清单)优先于 GFD 标签;显存取卡清单最大值。"""
    # 直接落一行 joined 登记(绕过完整注册流程)
    from app.modules.nodes.models import NodeEnrollment

    async with sm() as session:
        session.add(
            NodeEnrollment(
                token_hash="x" * 64,
                pool="hami",
                status="joined",
                node_name="fake-hami-node-1",
                gpu_info=[{"name": "NVIDIA A100-SXM4-80GB", "memory_mib": 81920}],
                os_info={"driver_version": "580.65", "cuda_version": "12.8"},
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                created_by=1,
            )
        )
        await session.commit()
    await node_spec_patrol(sm)
    async with sm() as session:
        row = (
            await session.execute(select(NodeSpec).where(NodeSpec.node_name == "fake-hami-node-1"))
        ).scalar_one()
    assert row.gpu_model == "A100-80G"
    assert row.vram_gb == 80
    assert row.driver_version == "580.65"
