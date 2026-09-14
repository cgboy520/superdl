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
    """已关机实例也拦住切池。挂了说明:实例盘是节点本地 LV、开机 pin 回原节点,
    换池后 nodeSelector 再也匹配不上,用户的停机实例永远开不了机。"""
    await _cluster_configured(sm)
    await _probe(sm)
    await seed_node_spec(sm, node_name="sw-1", pool_label="hami", gpu_count=8)
    await seed_instance(sm, status="stopped", node_name="sw-1")

    with pytest.raises(AppError) as e:
        await _switch(sm, "sw-1", "kata")
    assert e.value.message_key == "nodes.nodeHasInstances"
    assert e.value.params == {"count": 1}
    # 前置不过就什么都没写:期望态、登记、outbox 全干净
    assert (await _spec(sm, "sw-1")).desired_pool is None
    assert await _switch_tasks(sm) == []
    async with sm() as session:
        assert (await session.execute(select(NodeEnrollment))).scalars().all() == []


async def test_released_instance_does_not_block(sm):
    """已释放实例不算占用(实例盘已销毁)。挂了说明切池会被历史实例永久挡住。"""
    await _cluster_configured(sm)
    await _probe(sm)
    await seed_node_spec(sm, node_name="sw-2", pool_label="hami", gpu_count=8)
    await seed_instance(sm, status="released", node_name="sw-2")

    await _switch(sm, "sw-2", "kata")
    assert (await _spec(sm, "sw-2")).desired_pool == "kata"


async def test_pool_target_gates(sm):
    """目标池的三道取值闸:非可切池 / 同池 / 无卡机。挂了说明会把有卡机切进 cpu 池
    (库存口径错乱)或把无卡机切进 GPU 池(建出永远调度不上的容量)。"""
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
    """切 mig 池要机型支持。挂了说明会给 GB10 这类不支持 MIG 的机型建出
    nvidia.com/mig-* 永远注册不出来的库存。"""
    await _cluster_configured(sm)
    await _probe(sm)
    await seed_node_spec(sm, node_name="sw-gb10", pool_label="hami", gpu_model="GB10", gpu_count=1)
    await seed_node_spec(sm, node_name="sw-h100", pool_label="hami", gpu_model="H100-80G")

    with pytest.raises(AppError) as e:
        await _switch(sm, "sw-gb10", "mig")
    assert e.value.message_key == "nodes.poolMigUnsupported"

    await _switch(sm, "sw-h100", "mig")
    assert (await _spec(sm, "sw-h100")).desired_pool == "mig"


async def test_target_runtime_must_be_ready(sm, fake_auto_ready):
    """目标池运行时未就绪即拒。挂了说明会切到一个开不了机的池,节点白白离线。"""
    await _cluster_configured(sm)
    fake_auto_ready.probe_hami_ready = False
    await _probe(sm)
    await seed_node_spec(sm, node_name="sw-4", pool_label="kata", gpu_count=8)

    with pytest.raises(AppError) as e:
        await _switch(sm, "sw-4", "hami")
    assert e.value.code == "CLUSTER_NOT_READY"
    assert (await _spec(sm, "sw-4")).desired_pool is None


async def test_switch_writes_desired_state_only(sm):
    """成功路径:停调度 + 期望池 + outbox 同一事务落地,且**不签发任何注册令牌**。
    挂了说明切池又回到「要运维上节点重跑」的老路——池间差异全由 DaemonSet 按标签投送,
    节点侧没有任何需要同步的状态。"""
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
    """handler 整套下发:新池标签 + 新池 operand 标签,且删掉旧池残留。
    挂了说明 hami 的 deploy.device-plugin=false 会留在 kata 节点上,官方 device-plugin 起不来。"""
    await _cluster_configured(sm)
    await _probe(sm)
    await seed_node_spec(sm, node_name="sw-6", pool_label="hami", gpu_count=8)
    # 旧池残留:hami 装机时打的 operand 标签
    await fake_auto_ready.set_node_labels("sw-6", {GPU_DEPLOY_DEVICE_PLUGIN_LABEL: "false"})

    await _switch(sm, "sw-6", "kata")
    await drain(sm)

    labels = fake_auto_ready.node_labels["sw-6"]
    assert labels[POOL_NODE_LABEL] == "kata"
    assert labels[GPU_WORKLOAD_CONFIG_LABEL] == "vm-passthrough"
    assert GPU_DEPLOY_DEVICE_PLUGIN_LABEL not in labels
    assert "sw-6" in fake_auto_ready.cordoned_nodes

    # 幂等:重放同一任务不改变结果
    async with sm() as session:
        task = (
            await session.execute(select(OutboxTask).where(OutboxTask.type == "node.switch_pool"))
        ).scalar_one()
        await _node_handlers.handle_node_switch_pool(session, task)
    assert fake_auto_ready.node_labels["sw-6"] == labels


async def test_switch_back_restores_hami_operand_label(sm, fake_auto_ready):
    """切回 hami 要补回 deploy.device-plugin=false 并撤掉 vm-passthrough。
    挂了说明官方 device-plugin 与 HAMi 会在同一节点上抢卡。"""
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


async def test_reconciler_labels_unlabeled_node_then_joins(sm, fake_auto_ready):
    """入网时池标签由**平台**打:节点自己不声明,对账器看到 Ready 就打整套标签再判 joined。
    挂了说明池标签又有了第二个写入方(节点的 config.yaml),切池后它永远过时,
    Node 对象一重建就把旧池带回来。"""
    from app.core.k8s.base import NodeInfo
    from app.modules.nodes.reconciler import reconcile_enrollments_once

    await _cluster_configured(sm)
    # 节点注册时不带池标签(node-join 不再写 node-label)
    fake_auto_ready.inject_node(
        NodeInfo(
            name="join-1",
            pool_label="",
            gpu_model_label="RTX4090",
            gpu_total=8,
            gpu_used=0,
            status="Ready",
        )
    )
    enrollment_id = await _enroll(sm, "join-1", "hami")

    counts = await reconcile_enrollments_once(sm)

    assert counts["labeled"] == 1 and counts["joined"] == 1
    labels = fake_auto_ready.node_labels["join-1"]
    assert labels[POOL_NODE_LABEL] == "hami"
    assert labels[GPU_DEPLOY_DEVICE_PLUGIN_LABEL] == "false"
    async with sm() as session:
        row = await session.get(NodeEnrollment, enrollment_id)
        assert row is not None and row.status == "joined"


async def test_reconciler_label_failure_keeps_installing(sm, fake_auto_ready):
    """打标签失败就不推进状态。挂了说明会出现「登记已 joined 但节点没有池标签」的空档,
    节点看着入网了却永远接不到实例。"""
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
    """装机途中被切池:对账器按期望池打标签,不按登记池。
    挂了说明对账器与巡检 C2 会对着同一个节点来回改标签。"""
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
    # 巡检建台账行(池 hami),再在装机途中切到 kata
    await _probe(sm)
    await _switch(sm, "join-3", "kata")
    await _enroll(sm, "join-3", "hami")

    await reconcile_enrollments_once(sm)

    assert fake_auto_ready.node_labels["join-3"][POOL_NODE_LABEL] == "kata"


async def test_decommission_instance_gate_and_force(sm):
    """退役同样卡未释放实例,force 才放行。挂了说明会在用户实例还在的节点上直接删 Node 对象。"""
    await seed_node_spec(sm, node_name="dec-1", pool_label="hami", gpu_count=8)
    await seed_instance(sm, status="stopped", node_name="dec-1")

    async with sm() as session:
        with pytest.raises(AppError) as e:
            await service.decommission_node(session, "dec-1", reason="下架")
    assert e.value.message_key == "nodes.nodeHasInstances"

    async with sm() as session:
        await service.decommission_node(session, "dec-1", reason="主板损坏", force=True)
    assert (await _spec(sm, "dec-1")).desired_unschedulable is True
