"""节点池在线切换:前置闸(实例 / 机型 / 目标池 / 运行时)、期望态落库、标签整套收敛,
以及入网时池标签由平台(而非节点)写入。退役的实例闸与 force 旁路同在此。"""

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.core.k8s.base import (
    GPU_DEPLOY_DEVICE_PLUGIN_LABEL,
    GPU_WORKLOAD_CONFIG_LABEL,
    POOL_NODE_LABEL,
)
from app.core.outbox import OutboxTask
from app.modules.nodes import (
    handlers as _node_handlers,
    service,
)
from app.modules.nodes.models import NodeEnrollment, NodeSpec
from tests.helpers import drain, seed_instance, seed_node_spec, set_platform_setting

pytestmark = pytest.mark.usefixtures("fake_auto_ready")


async def _cluster_configured(sm) -> None:
    await set_platform_setting(sm, "cluster_server_url", "https://10.0.0.10:9345")
    await set_platform_setting(sm, "cluster_join_token", "K10abcdef0123456789::server:secrettoken")


async def _probe(sm) -> None:
    """落一份新鲜的集群能力快照(require_pool_runtime 读它)。"""
    from app.modules.nodes.patrol import node_spec_patrol

    await node_spec_patrol(sm)


async def _switch(sm, node_name: str, pool: str, *, reason: str = "实机验证"):
    async with sm() as session:
        return await service.switch_node_pool(
            session,
            node_name,
            pool=pool,
            reason=reason,
        )


async def _spec(sm, node_name: str) -> NodeSpec:
    async with sm() as session:
        return (
            await session.execute(select(NodeSpec).where(NodeSpec.node_name == node_name))
        ).scalar_one()


async def _switch_tasks(sm) -> list[OutboxTask]:
    async with sm() as session:
        return list(
            (
                await session.execute(
                    select(OutboxTask).where(OutboxTask.type == "node.switch_pool")
                )
            ).scalars()
        )


async def test_stopped_instance_blocks_switch(sm):
    """已关机实例阻止切池。"""
    await _cluster_configured(sm)
    await _probe(sm)
    await seed_node_spec(sm, node_name="sw-1", pool_label="hami", gpu_count=8)
    await seed_instance(sm, status="stopped", node_name="sw-1")

    with pytest.raises(AppError) as e:
        await _switch(sm, "sw-1", "kata")
    assert e.value.message_key == "nodes.nodeHasInstances"
    assert e.value.params == {"count": 1}
    assert (await _spec(sm, "sw-1")).desired_pool is None
    assert await _switch_tasks(sm) == []
    async with sm() as session:
        assert (await session.execute(select(NodeEnrollment))).scalars().all() == []


async def test_released_instance_does_not_block(sm):
    """已释放实例不阻止切池。"""
    await _cluster_configured(sm)
    await _probe(sm)
    await seed_node_spec(sm, node_name="sw-2", pool_label="hami", gpu_count=8)
    await seed_instance(sm, status="released", node_name="sw-2")

    await _switch(sm, "sw-2", "kata")
    assert (await _spec(sm, "sw-2")).desired_pool == "kata"


async def test_pool_target_gates(sm):
    """拒绝非法目标池、当前池及无卡节点切池。"""
    await _cluster_configured(sm)
    await _probe(sm)
    await seed_node_spec(sm, node_name="sw-3", pool_label="hami", gpu_count=8)
    await seed_node_spec(sm, node_name="sw-cpu", pool_label="cpu", gpu_count=0, gpu_model=None)

    with pytest.raises(AppError) as e:
        await _switch(sm, "sw-3", "cpu")
    assert e.value.message_key == "nodes.poolNotSwitchable"

    with pytest.raises(AppError) as e:
        await _switch(sm, "sw-3", "hami")
    assert e.value.message_key == "nodes.poolUnchanged"

    with pytest.raises(AppError) as e:
        await _switch(sm, "sw-cpu", "hami")
    assert e.value.message_key == "nodes.poolIncompatible"


async def test_mig_needs_capable_model(sm):
    """切入 mig 池要求机型支持 MIG。"""
    await _cluster_configured(sm)
    await _probe(sm)
    await seed_node_spec(sm, node_name="sw-gb10", pool_label="hami", gpu_model="GB10", gpu_count=1)
    await seed_node_spec(sm, node_name="sw-h100", pool_label="hami", gpu_model="H100-80G")

    with pytest.raises(AppError) as e:
        await _switch(sm, "sw-gb10", "mig")
    assert e.value.message_key == "nodes.poolMigUnsupported"

    await _switch(sm, "sw-h100", "mig")
    assert (await _spec(sm, "sw-h100")).desired_pool == "mig"


async def test_kata_needs_passthrough_capable_model(sm):
    """切入 kata 池要求机型能整卡直通:集成 GPU 绑不了 vfio-pci,进池即 0 卡可分配。"""
    await _cluster_configured(sm)
    await _probe(sm)
    await seed_node_spec(sm, node_name="sw-gb10k", pool_label="hami", gpu_model="GB10", gpu_count=1)
    await seed_node_spec(sm, node_name="sw-a100", pool_label="hami", gpu_model="A100-80G")

    with pytest.raises(AppError) as e:
        await _switch(sm, "sw-gb10k", "kata")
    assert e.value.message_key == "nodes.poolPassthroughUnsupported"
    assert (await _spec(sm, "sw-gb10k")).desired_pool is None
    assert await _switch_tasks(sm) == []

    await _switch(sm, "sw-a100", "kata")
    assert (await _spec(sm, "sw-a100")).desired_pool == "kata"


async def test_zero_gpu_in_gpu_pool_can_switch_back(sm):
    """目标池组件没起来导致观测卡数归 0 时仍能切回:否则节点被锁死在坏池里。"""
    await _cluster_configured(sm)
    await _probe(sm)
    await seed_node_spec(sm, node_name="sw-stuck", pool_label="kata", gpu_model="GB10", gpu_count=0)

    await _switch(sm, "sw-stuck", "hami")
    assert (await _spec(sm, "sw-stuck")).desired_pool == "hami"


async def test_target_runtime_must_be_ready(sm, fake_auto_ready):
    """目标池运行时未就绪时拒绝切池。"""
    await _cluster_configured(sm)
    fake_auto_ready.probe_hami_ready = False
    await _probe(sm)
    await seed_node_spec(sm, node_name="sw-4", pool_label="kata", gpu_count=8)

    with pytest.raises(AppError) as e:
        await _switch(sm, "sw-4", "hami")
    assert e.value.code == "CLUSTER_NOT_READY"
    assert (await _spec(sm, "sw-4")).desired_pool is None


async def test_switch_writes_desired_state_only(sm):
    """切池同事务写入停调度、期望池和 outbox,不签发注册令牌。"""
    await _cluster_configured(sm)
    await _probe(sm)
    await seed_node_spec(sm, node_name="sw-5", pool_label="hami", gpu_count=8)

    row, from_pool = await _switch(sm, "sw-5", "kata")
    assert row.desired_pool == "kata" and row.desired_unschedulable is True
    assert from_pool == "hami"
    tasks = await _switch_tasks(sm)
    assert [t.payload["to_pool"] for t in tasks] == ["kata"]
    assert tasks[0].payload["from_pool"] == "hami"
    async with sm() as session:
        assert (await session.execute(select(NodeEnrollment))).scalars().all() == []


async def test_handler_converges_full_label_set(sm, fake_auto_ready):
    """handler 下发目标池及 operand 标签,删除旧池标签。"""
    await _cluster_configured(sm)
    await _probe(sm)
    await seed_node_spec(sm, node_name="sw-6", pool_label="hami", gpu_count=8)
    await fake_auto_ready.set_node_labels("sw-6", {GPU_DEPLOY_DEVICE_PLUGIN_LABEL: "false"})

    await _switch(sm, "sw-6", "kata")
    await drain(sm)

    labels = fake_auto_ready.node_labels["sw-6"]
    assert labels[POOL_NODE_LABEL] == "kata"
    assert labels[GPU_WORKLOAD_CONFIG_LABEL] == "vm-passthrough"
    assert GPU_DEPLOY_DEVICE_PLUGIN_LABEL not in labels
    assert "sw-6" in fake_auto_ready.cordoned_nodes

    async with sm() as session:
        task = (
            await session.execute(select(OutboxTask).where(OutboxTask.type == "node.switch_pool"))
        ).scalar_one()
        await _node_handlers.handle_node_switch_pool(session, task)
    assert fake_auto_ready.node_labels["sw-6"] == labels


async def test_switch_back_restores_hami_operand_label(sm, fake_auto_ready):
    """切回 hami 恢复 deploy.device-plugin=false,移除 vm-passthrough。"""
    await _cluster_configured(sm)
    await _probe(sm)
    await seed_node_spec(sm, node_name="sw-7", pool_label="kata", gpu_count=8)
    await fake_auto_ready.set_node_labels("sw-7", {GPU_WORKLOAD_CONFIG_LABEL: "vm-passthrough"})

    await _switch(sm, "sw-7", "hami")
    await drain(sm)

    labels = fake_auto_ready.node_labels["sw-7"]
    assert labels[POOL_NODE_LABEL] == "hami"
    assert labels[GPU_DEPLOY_DEVICE_PLUGIN_LABEL] == "false"
    assert GPU_WORKLOAD_CONFIG_LABEL not in labels


async def _enroll(sm, hostname: str, pool: str) -> int:
    """签发令牌并 bootstrap 到 installing,返回登记 id(节点侧不打任何池标签)。"""
    from app.modules.nodes.schemas import EnrollmentCreate

    async with sm() as session:
        enrollment, token = await service.create_enrollment(
            session,
            EnrollmentCreate(pool=pool, hostname=hostname),  # type: ignore[arg-type]
            created_by=1,
            idempotency_key=None,
        )
        enrollment_id = enrollment.id
    async with sm() as session:
        await service.bootstrap(
            session,
            token,
            hostname=hostname,
            os_info={"os_release": "Ubuntu 24.04"},
            gpu_details=[],
            client_ip=None,
        )
    return enrollment_id


async def test_reconciler_label_failure_keeps_installing(sm, fake_auto_ready):
    """写标签失败时登记保持 installing。"""
    from app.core.k8s.base import NodeInfo
    from app.modules.nodes.reconciler import reconcile_enrollments_once

    await _cluster_configured(sm)
    fake_auto_ready.inject_node(
        NodeInfo(name="join-2", pool_label="", gpu_total=8, gpu_used=0, status="Ready")
    )
    enrollment_id = await _enroll(sm, "join-2", "hami")

    async def boom(*_a, **_kw):
        raise RuntimeError("apiserver 抖动")

    orig = fake_auto_ready.set_node_labels
    fake_auto_ready.set_node_labels = boom
    try:
        counts = await reconcile_enrollments_once(sm)
    finally:
        fake_auto_ready.set_node_labels = orig

    assert counts["joined"] == 0
    async with sm() as session:
        row = await session.get(NodeEnrollment, enrollment_id)
        assert row is not None and row.status == "installing"


async def test_reconciler_honours_desired_pool_over_enrollment(sm, fake_auto_ready):
    """装机途中切池后,对账器按期望池而非登记池写标签。"""
    from app.core.k8s.base import NodeInfo
    from app.modules.nodes.reconciler import reconcile_enrollments_once

    await _cluster_configured(sm)
    fake_auto_ready.inject_node(
        NodeInfo(
            name="join-3",
            pool_label="hami",
            gpu_model_label="RTX4090",
            gpu_total=8,
            gpu_used=0,
            status="Ready",
        )
    )
    await _probe(sm)
    await _switch(sm, "join-3", "kata")
    await _enroll(sm, "join-3", "hami")

    await reconcile_enrollments_once(sm)

    assert fake_auto_ready.node_labels["join-3"][POOL_NODE_LABEL] == "kata"


async def test_decommission_instance_gate_and_force(sm):
    """存在未释放实例时退役须指定 force。"""
    await seed_node_spec(sm, node_name="dec-1", pool_label="hami", gpu_count=8)
    await seed_instance(sm, status="stopped", node_name="dec-1")

    async with sm() as session:
        with pytest.raises(AppError) as e:
            await service.decommission_node(session, "dec-1", reason="下架")
    assert e.value.message_key == "nodes.nodeHasInstances"

    async with sm() as session:
        await service.decommission_node(session, "dec-1", reason="主板损坏", force=True)
    assert (await _spec(sm, "dec-1")).desired_unschedulable is True
