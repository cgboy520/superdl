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
    # cpu 池是无卡机:0 卡、无型号,台账照样收敛(纯 CPU 规格的库存口径只看 vCPU/内存)
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
    """驱动/CUDA 两列以 GFD 标签为准:没有这条路径,收尾上报晚于对账器判 joined(终态,上报 404)
    的节点两列恒空,且驱动升级后台账不跟随。"""
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
    # 节点升级驱动 → GFD 标签变 → 下一轮巡检跟随(装机快照永远停在首装那次)
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
    """装机登记(bootstrap 的 nvidia-smi 全卡清单 + 收尾上报的驱动/CUDA 版本)优先于 GFD 型号标签;
    显存取卡清单最大值。版本走 report_progress → 登记快照 → 巡检落台账整条链
    (无 GFD 版本标签时的回落):
    挂了 = 管理端节点页驱动/CUDA 两列恒空。"""
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
    """走完「签发令牌 → bootstrap → 对账」整条登记链,让登记行落到对账器判定的终态。"""
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
    """冒名节点:登记为 cpu 池的机器把 config.yaml 改成 superdl.io/pool=kata,想吸走别人的
    VM 隔离负载(落到攻击者宿主上,宿主 root 可读)。

    关键在于此时登记行是 **failed** —— nodes/reconciler 正是把「标签与登记不符」判成
    failed。旧实现的池事实源只查 status='joined',对这一行恰好查不到登记池,纠偏静默跳过、
    伪标签长期有效。挂了 = 池标签纠偏对它唯一要防的攻击完全失效。
    """
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
        # 前提:这正是旧实现漏掉的那一档
        assert enrollment.status == "failed" and enrollment.pool == "cpu"

    counts = await node_spec_patrol(sm)

    # (a) 标签按登记纠回 cpu
    assert counts["pool_label_corrected"] == 1
    assert fake_auto_ready.node_labels["spoofer-1"]["superdl.io/pool"] == "cpu"
    # (b) 同时停调度:纠标签有窗口(kubelet 重启会再次自声明),而调度器只看标签
    assert counts["pool_mismatch_cordoned"] == 1
    async with sm() as session:
        row = (
            await session.execute(select(NodeSpec).where(NodeSpec.node_name == "spoofer-1"))
        ).scalar_one()
        assert row.desired_unschedulable is True
        # 走与管理端手工 cordon 同一条路径:期望态落台账 + outbox 入队(请求路径不碰 K8s)
        tasks = (
            await session.execute(select(OutboxTask).where(OutboxTask.type == "node.cordon"))
        ).scalars()
        assert [t.payload["node_name"] for t in tasks] == ["spoofer-1"]
    # 本轮巡检的阶段 D 已按期望态收敛,节点当轮就真的不可调度(不等 outbox)
    assert "spoofer-1" in fake_auto_ready.cordoned_nodes

    # 二轮不重复入队 cordon(期望态已是 True),但只要 fake 的自声明标签没变就继续纠偏
    counts2 = await node_spec_patrol(sm)
    assert counts2["pool_mismatch_cordoned"] == 0


async def test_matching_pool_label_is_left_alone(sm, fake_auto_ready):
    """登记与自声明一致的正常节点不能被误判:挂了 = 每轮巡检把好节点全 cordon 掉。"""
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
