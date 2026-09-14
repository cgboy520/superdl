"""节点台账巡检:铺行收敛/未打标可见/装机登记兜底/Missing 保留删行/label 收敛与失败自愈。"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.k8s.base import NodeInfo
from app.core.outbox import OutboxTask
from app.modules.nodes.models import NodeSpec
from app.modules.nodes.patrol import node_spec_patrol
from tests.helpers import set_platform_setting

pytestmark = pytest.mark.usefixtures("fake_auto_ready")


async def test_patrol_converges_and_labels(sm, fake_auto_ready):
    counts = await node_spec_patrol(sm)
    assert counts["upserted"] == 4  # fake 四池各一节点(kata / hami / mig / cpu)
    async with sm() as session:
        rows = {r.node_name: r for r in (await session.execute(select(NodeSpec))).scalars()}
    assert rows["fake-hami-node-1"].gpu_model == "RTX4090"
    assert rows["fake-hami-node-1"].vram_gb == 24  # DEFAULT_VRAM_GB 兜底
    assert rows["fake-mig-node-1"].gpu_model == "H100"
    # cpu 池无卡机:0 卡、无型号,台账照样收敛
    assert rows["fake-cpu-node-1"].gpu_count == 0
    assert not rows["fake-cpu-node-1"].gpu_model
    # label 收敛已写入 fake
    assert fake_auto_ready.node_labels["fake-hami-node-1"]["superdl.io/gpu-model"] == "RTX4090"
    async with sm() as session:
        row = (
            await session.execute(select(NodeSpec).where(NodeSpec.node_name == "fake-hami-node-1"))
        ).scalar_one()
        assert row.label_synced is True
    # 二轮幂等
    counts2 = await node_spec_patrol(sm)
    assert counts2["upserted"] == 4 and counts2["removed"] == 0


async def test_unlabeled_node_visible(sm, fake_auto_ready):
    fake_auto_ready.unlabeled_nodes.append(
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


async def test_missing_then_removed(sm, fake_auto_ready):
    fake_auto_ready.unlabeled_nodes.append(
        NodeInfo(
            name="gone-node",
            pool_label="hami",
            gpu_total=1,
            gpu_used=0,
            status="Ready",
        )
    )
    await node_spec_patrol(sm)
    fake_auto_ready.unlabeled_nodes.clear()
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


async def test_gfd_version_labels_fill_and_follow_upgrade(sm, fake_auto_ready):
    """驱动/CUDA 两列以 GFD 标签为准,驱动升级后台账跟随。"""
    fake_auto_ready.unlabeled_nodes.append(
        NodeInfo(
            name="gfd-node",
            pool_label="hami",
            gpu_total=8,
            gpu_used=0,
            status="Ready",
            gpu_model_label="NVIDIA-Graphics-Device",
            driver_version_label="580.65",
            cuda_version_label="12.8",
        )
    )
    await node_spec_patrol(sm)
    async with sm() as session:
        row = (
            await session.execute(select(NodeSpec).where(NodeSpec.node_name == "gfd-node"))
        ).scalar_one()
    assert row.driver_version == "580.65" and row.cuda_version == "12.8"
    # 节点升级驱动 → GFD 标签变 → 下一轮巡检跟随
    fake_auto_ready.unlabeled_nodes[-1] = replace(
        fake_auto_ready.unlabeled_nodes[-1],
        driver_version_label="610.57.04",
        cuda_version_label="13.3",
    )
    await node_spec_patrol(sm)
    async with sm() as session:
        row = (
            await session.execute(select(NodeSpec).where(NodeSpec.node_name == "gfd-node"))
        ).scalar_one()
    assert row.driver_version == "610.57.04" and row.cuda_version == "13.3"


async def test_enrollment_report_wins_over_gfd(sm, fake_auto_ready):
    """装机登记(nvidia-smi 卡清单 + 上报的驱动/CUDA 版本)优先于 GFD 型号标签;
    显存取卡清单最大值;无 GFD 版本标签时回落到登记快照。"""
    from app.modules.nodes import service
    from app.modules.nodes.reconciler import reconcile_enrollments_once
    from app.modules.nodes.schemas import EnrollmentCreate

    await set_platform_setting(sm, "cluster_server_url", "https://10.0.0.10:9345")
    await set_platform_setting(sm, "cluster_join_token", "K10abcdef0123456789::server:secrettoken")
    async with sm() as session:
        _e, token = await service.create_enrollment(
            session,
            EnrollmentCreate(pool="hami", hostname="fake-hami-node-1"),
            created_by=1,
            idempotency_key=None,
        )
    async with sm() as session:
        _row, _cfg, progress = await service.bootstrap(
            session,
            token,
            hostname="fake-hami-node-1",
            os_info={"os_release": "Ubuntu 24.04"},
            gpu_details=[{"name": "NVIDIA A100-SXM4-80GB", "memory_mib": 81920}],
            client_ip=None,
        )
        assert progress is not None
    async with sm() as session:
        await service.report_progress(
            session,
            progress,
            phase="waiting_node",
            state="ok",
            message=None,
            driver_version="580.65",
            cuda_version="12.8",
        )
    await reconcile_enrollments_once(sm)  # fake 集群里该节点 Ready 且池匹配 → joined
    await node_spec_patrol(sm)
    async with sm() as session:
        row = (
            await session.execute(select(NodeSpec).where(NodeSpec.node_name == "fake-hami-node-1"))
        ).scalar_one()
    assert row.gpu_model == "A100-80G"
    assert row.vram_gb == 80
    assert row.driver_version == "580.65" and row.cuda_version == "12.8"


async def _enroll_and_join_attempt(sm, *, hostname: str, pool: str) -> None:
    """走完「签发令牌 → bootstrap → 对账」登记链。"""
    from app.modules.nodes import service
    from app.modules.nodes.reconciler import reconcile_enrollments_once
    from app.modules.nodes.schemas import EnrollmentCreate

    await set_platform_setting(sm, "cluster_server_url", "https://10.0.0.10:9345")
    await set_platform_setting(sm, "cluster_join_token", "K10abcdef0123456789::server:secrettoken")
    async with sm() as session:
        _e, token = await service.create_enrollment(
            session,
            EnrollmentCreate(pool=pool, hostname=hostname),  # type: ignore[arg-type]
            created_by=1,
            idempotency_key=None,
        )
    async with sm() as session:
        await service.bootstrap(
            session,
            token,
            hostname=hostname,
            os_info={"os_release": "Ubuntu 24.04"},
            gpu_details=[],
            client_ip=None,
        )
    await reconcile_enrollments_once(sm)


async def test_pool_label_spoof_is_corrected_and_cordoned(sm, fake_auto_ready):
    """冒名节点(登记 cpu 池、自声明 kata 池,登记行落 failed):池标签按登记纠回并 cordon。"""
    from app.modules.nodes.models import NodeEnrollment

    fake_auto_ready.inject_node(
        NodeInfo(
            name="spoofer-1",
            pool_label="kata",  # 节点自声明:kubelet --node-labels,不可信
            gpu_model_label="RTX4090",
            gpu_total=8,
            gpu_used=0,
            status="Ready",
        )
    )
    await _enroll_and_join_attempt(sm, hostname="spoofer-1", pool="cpu")
    async with sm() as session:
        enrollment = (
            await session.execute(
                select(NodeEnrollment).where(NodeEnrollment.node_name == "spoofer-1")
            )
        ).scalar_one()
        # 登记行落在 failed
        assert enrollment.status == "failed" and enrollment.pool == "cpu"

    counts = await node_spec_patrol(sm)

    # (a) 标签按登记纠回 cpu
    assert counts["pool_label_corrected"] == 1
    assert fake_auto_ready.node_labels["spoofer-1"]["superdl.io/pool"] == "cpu"
    # (b) 同时停调度
    assert counts["pool_mismatch_cordoned"] == 1
    async with sm() as session:
        row = (
            await session.execute(select(NodeSpec).where(NodeSpec.node_name == "spoofer-1"))
        ).scalar_one()
        assert row.desired_unschedulable is True
        # 与管理端手工 cordon 同一条路径:期望态落台账 + outbox 入队
        tasks = (
            await session.execute(select(OutboxTask).where(OutboxTask.type == "node.cordon"))
        ).scalars()
        assert [t.payload["node_name"] for t in tasks] == ["spoofer-1"]
    # 本轮巡检阶段 D 已按期望态收敛
    assert "spoofer-1" in fake_auto_ready.cordoned_nodes

    # 二轮不重复入队 cordon,自声明标签没变就继续纠偏
    counts2 = await node_spec_patrol(sm)
    assert counts2["pool_mismatch_cordoned"] == 0


async def test_switch_pool_converges_without_spoof_alarm(sm, fake_auto_ready):
    """管理端切池:巡检按期望池整套下发标签,但**不**计冒名指标、不重复 cordon。
    挂了说明运维每切一次池就触发一条 critical NodePoolLabelMismatch,告警失去意义。"""
    from app.core.k8s.base import GPU_WORKLOAD_CONFIG_LABEL
    from app.core.metrics import NODE_POOL_LABEL_MISMATCH_TOTAL
    from app.modules.nodes import service

    fake_auto_ready.inject_node(
        NodeInfo(
            name="switcher-1",
            pool_label="hami",  # 标签还没收敛
            gpu_model_label="RTX4090",
            gpu_total=8,
            gpu_used=0,
            status="Ready",
        )
    )
    await set_platform_setting(sm, "cluster_server_url", "https://10.0.0.10:9345")
    await set_platform_setting(sm, "cluster_join_token", "K10abcdef0123456789::server:secrettoken")
    await node_spec_patrol(sm)
    async with sm() as session:
        await service.switch_node_pool(
            session,
            "switcher-1",
            pool="kata",
            reason="实机验证",
            created_by=1,
            idempotency_key=None,
        )

    before = NODE_POOL_LABEL_MISMATCH_TOTAL._value.get()
    counts = await node_spec_patrol(sm)

    assert counts["pool_label_corrected"] == 1
    assert counts["pool_mismatch_cordoned"] == 0  # 切池时已 cordon,不重复入队
    assert NODE_POOL_LABEL_MISMATCH_TOTAL._value.get() == before  # 切池不是冒名
    labels = fake_auto_ready.node_labels["switcher-1"]
    assert labels["superdl.io/pool"] == "kata"
    assert labels[GPU_WORKLOAD_CONFIG_LABEL] == "vm-passthrough"


async def test_matching_pool_label_is_left_alone(sm, fake_auto_ready):
    """登记与自声明一致的正常节点不被 cordon。"""
    fake_auto_ready.inject_node(
        NodeInfo(
            name="honest-1",
            pool_label="hami",
            gpu_model_label="RTX4090",
            gpu_total=8,
            gpu_used=0,
            status="Ready",
        )
    )
    await _enroll_and_join_attempt(sm, hostname="honest-1", pool="hami")
    counts = await node_spec_patrol(sm)
    assert counts["pool_label_corrected"] == 0 and counts["pool_mismatch_cordoned"] == 0
    async with sm() as session:
        row = (
            await session.execute(select(NodeSpec).where(NodeSpec.node_name == "honest-1"))
        ).scalar_one()
        assert row.desired_unschedulable is None
