"""编排协议 Fake/Real 契约一致性:同一组用例参数化跑两个后端。

Fake 恒跑;Real 由 SUPERDL_TEST_KUBECONFIG 门控(与 test_k8s_real_smoke.py 一致,
CI kind job 驱动;未设置时 real 参数跳过、fake 照常)。

kind 上 JuiceFS 作业永不完成(无 SC/PVC):Real 侧「完成返回」路径用 patch Job
status 注入成功态 —— conformance 关心协议状态机(进行中抛错/完成返回/幂等),
不依赖存储后端。Job 命名规则取自 real.py 实现(wipe-/quota-set-/quota-del-),
real.py 改名即红,属有意为之的契约钉死。
"""

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from kubernetes import client as k8s_client

from app.core.k8s.base import (
    POOL_NODE_LABEL,
    InstancePodSpec,
    K8sOrchestrator,
)
from app.core.k8s.fake import FakeOrchestrator


@dataclass
class Backend:
    impl: K8sOrchestrator
    kind: str  # "fake" / "real"
    namespace: str  # 租户 ns(Real 侧已建,fixture 收尾即删)
    fake: FakeOrchestrator | None = None
    real: Any = None  # RealOrchestrator(直访 batch/core 做 Job 状态注入)


@pytest.fixture(
    params=[
        pytest.param("fake", id="fake"),
        pytest.param("real", id="real", marks=pytest.mark.real_k8s),
    ]
)
async def backend(request: pytest.FixtureRequest) -> AsyncIterator[Backend]:
    if request.param == "fake":
        fake = FakeOrchestrator()
        ns = f"tenant-conf-{uuid.uuid4().hex[:8]}"
        await fake.ensure_namespace(ns)
        yield Backend(impl=fake, kind="fake", namespace=ns, fake=fake)
        return
    if not os.environ.get("SUPERDL_TEST_KUBECONFIG"):
        pytest.skip("SUPERDL_TEST_KUBECONFIG 未设置,跳过 Real 侧 conformance")
    os.environ["KUBECONFIG"] = os.environ["SUPERDL_TEST_KUBECONFIG"]
    from app.core.k8s.real import RealOrchestrator

    real = RealOrchestrator()
    ns = f"tenant-conf-{uuid.uuid4().hex[:8]}"
    await real.ensure_namespace(ns)
    # 平台 ns + metaurl secret:quota Job 的创建前提(kind 上作业不会成功,仅验协议状态机)
    platform_ns: str = real.settings.k8s_platform_namespace
    try:
        real.core.create_namespace(
            k8s_client.V1Namespace(metadata=k8s_client.V1ObjectMeta(name=platform_ns))
        )
    except k8s_client.ApiException as exc:
        if exc.status != 409:
            raise
    try:
        real.core.create_namespaced_secret(
            platform_ns,
            k8s_client.V1Secret(
                metadata=k8s_client.V1ObjectMeta(name="superdl-api-secrets"),
                string_data={"juicefs-metaurl": "postgres://conf:conf@localhost:5432/none"},
            ),
        )
    except k8s_client.ApiException as exc:
        if exc.status != 409:
            raise
    yield Backend(impl=real, kind="real", namespace=ns, real=real)
    try:
        real.core.delete_namespace(ns)
    except k8s_client.ApiException as exc:
        if exc.status != 404:
            raise


# ---------- Real 侧注入助手(Job 命名规则镜像 real.py,见文件头注释) ----------


def _wipe_job_name(subpath: str) -> str:
    return f"wipe-{subpath[-40:]}".lower()


def _quota_job_name(subpath: str, is_set: bool) -> str:
    return f"quota-{'set' if is_set else 'del'}-{subpath[-36:]}".lower()


def _mark_job_succeeded(real: Any, namespace: str, name: str) -> None:
    """kind 上存储类作业永不成功:patch status 注入完成态,走「完成返回」协议分支。"""
    real.batch.patch_namespaced_job_status(name, namespace, {"status": {"succeeded": 1}})


def _spec(namespace: str, pool: str, gpu_resources: dict[str, str]) -> InstancePodSpec:
    name = f"conf-{uuid.uuid4().hex[:12]}"
    return InstancePodSpec(
        namespace=namespace,
        name=name,
        image="registry.invalid/conf:0",
        gpu_resources=gpu_resources,
        runtime_class=None,
        host_users=True,
        vcpu=1,
        mem_gb=1,
        disk_gb=1,
        ssh_node_port=31998,
        jupyter_host=f"{name}.app.example.invalid",
        node_selector={POOL_NODE_LABEL: pool},
    )


# ---------- 契约用例(双后端同跑) ----------


class TestWipeDiskContract:
    """wipe_disk 异步语义:进行中抛错(交 outbox 退避);完成返回;注入失败可重试。"""

    async def test_in_progress_raises_then_completion_returns(self, backend: Backend) -> None:
        ns, sub = backend.namespace, f"confw-{uuid.uuid4().hex[:8]}"
        if backend.kind == "fake":
            assert backend.fake is not None
            backend.fake.auto_wipe = False
            with pytest.raises(RuntimeError, match="in progress"):
                await backend.impl.wipe_disk(ns, sub)  # 进行中
            with pytest.raises(RuntimeError, match="in progress"):
                await backend.impl.wipe_disk(ns, sub)  # 仍在进行(幂等,不产生第二份作业)
            backend.fake.finish_wipe(ns, sub)
            await backend.impl.wipe_disk(ns, sub)  # 完成:清理并返回
            assert backend.fake.wiped_disks == [(ns, sub)]  # 擦除只发生一次
        else:
            with pytest.raises(RuntimeError, match="awaiting completion"):
                await backend.impl.wipe_disk(ns, sub)  # 已创建,等完成
            with pytest.raises(RuntimeError):
                await backend.impl.wipe_disk(ns, sub)  # 进行中(kind 上 Job 不会成功)
            _mark_job_succeeded(backend.real, ns, _wipe_job_name(sub))
            await backend.impl.wipe_disk(ns, sub)  # 完成:清理 Job 并返回


class TestDiskQuotaContract:
    """set/delete_disk_quota:幂等(重放到同值不报错);进行中抛错;完成返回。"""

    async def test_idempotent_set_and_delete(self, backend: Backend) -> None:
        sub = f"confq-{uuid.uuid4().hex[:8]}"
        if backend.kind == "fake":
            assert backend.fake is not None
            await backend.impl.set_disk_quota(sub, 10)
            await backend.impl.set_disk_quota(sub, 10)  # 幂等重放
            assert backend.fake.disk_quotas[sub] == 10
            await backend.impl.delete_disk_quota(sub)
            await backend.impl.delete_disk_quota(sub)  # 无配额记录视为成功
            assert sub not in backend.fake.disk_quotas
        else:
            platform_ns: str = backend.real.settings.k8s_platform_namespace
            with pytest.raises(RuntimeError, match="awaiting completion"):
                await backend.impl.set_disk_quota(sub, 10)  # 已创建,等完成
            _mark_job_succeeded(backend.real, platform_ns, _quota_job_name(sub, True))
            await backend.impl.set_disk_quota(sub, 10)  # 完成返回
            with pytest.raises(RuntimeError, match="awaiting completion"):
                await backend.impl.delete_disk_quota(sub)
            _mark_job_succeeded(backend.real, platform_ns, _quota_job_name(sub, False))
            await backend.impl.delete_disk_quota(sub)  # 完成返回


class TestAvailableGpusContract:
    """available_gpus 归池口径:非负 int;未知池归 0;占用按池扣减、释放归还。"""

    async def test_pool_accounting(self, backend: Backend) -> None:
        kata = await backend.impl.available_gpus("kata")
        assert isinstance(kata, int) and kata >= 0
        assert await backend.impl.available_gpus("no-such-pool") == 0  # 未知池归 0
        if backend.kind == "fake":
            assert backend.fake is not None
            capacity = backend.fake.pool_capacity["kata"]
            assert kata == capacity
            # 占用归池:kata 池起 2 卡实例 → kata 可用 -2,hami 不受影响;删除后归还
            spec = _spec(backend.namespace, "kata", {"nvidia.com/gpu": "2"})
            await backend.impl.create_instance(spec)
            assert await backend.impl.available_gpus("kata") == capacity - 2
            assert await backend.impl.available_gpus("hami") == backend.fake.pool_capacity["hami"]
            await backend.impl.delete_instance(backend.namespace, spec.name, force=True)
            assert await backend.impl.available_gpus("kata") == capacity
        # Real:kind 无打池标签节点,各池可用恒 0(上面已断言非负与未知池口径)


class TestReadInstanceLogsContract:
    """read_instance_logs:成功路径的形状契约(str、行数受 tail_lines 约束、参数透传)。

    注:Fake 对不存在的 Pod 也合成日志(dev 联调需要,见 fake.py 注释),
    Real 直通 K8s 错误 —— 这是两侧有意的行为差,本套件分别钉死。
    """

    async def test_logs_shape(self, backend: Backend) -> None:
        if backend.kind == "fake":
            assert backend.fake is not None
            logs = await backend.impl.read_instance_logs(
                backend.namespace, "conf-pod", tail_lines=3
            )
            assert isinstance(logs, str)
            lines = logs.splitlines()
            assert 0 < len(lines) <= 3
            assert backend.fake.log_calls[-1] == (backend.namespace, "conf-pod", 3, None)
            logs_full = await backend.impl.read_instance_logs(
                backend.namespace, "conf-pod", tail_lines=100, since_seconds=60
            )
            assert backend.fake.log_calls[-1] == (backend.namespace, "conf-pod", 100, 60)
            assert "conf-pod" in logs_full
        else:
            # Pod 不存在:Real 直通 apiserver 404,不合成空串
            with pytest.raises(k8s_client.ApiException):
                await backend.impl.read_instance_logs(
                    backend.namespace, "no-such-pod", tail_lines=10
                )
