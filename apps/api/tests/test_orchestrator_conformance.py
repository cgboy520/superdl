"""编排协议 Fake/Real 契约一致性:同一组用例参数化跑两个后端;Real 由 SUPERDL_TEST_KUBECONFIG 门控。
Real 侧「完成返回」用 patch Job status 注入;Job 命名规则(wipe-/quota-set-/quota-del-)取自 real.py。
"""

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from kubernetes import client as k8s_client

from app.core.k8s.base import K8sOrchestrator
from app.core.k8s.fake import FakeOrchestrator
from tests.helpers import use_kubeconfig


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
    use_kubeconfig(os.environ["SUPERDL_TEST_KUBECONFIG"])
    from app.core.k8s.real import RealOrchestrator

    real = RealOrchestrator()
    ns = f"tenant-conf-{uuid.uuid4().hex[:8]}"
    await real.ensure_namespace(ns)
    # 平台 ns + metaurl secret:quota Job 的创建前提
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
                metadata=k8s_client.V1ObjectMeta(name="superdl-db"),
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


# ---------- Real 侧注入助手 ----------


def _wipe_job_name(subpath: str) -> str:
    return f"wipe-{subpath[-40:]}".lower()


def _quota_job_name(subpath: str, is_set: bool) -> str:
    return f"quota-{'set' if is_set else 'del'}-{subpath[-36:]}".lower()


def _mark_job_succeeded(real: Any, namespace: str, name: str) -> None:
    """patch Job status 注入完成态。"""
    real.batch.patch_namespaced_job_status(name, namespace, {"status": {"succeeded": 1}})


def _bind_juicefs_pvc(real: Any, tenant_ns: str) -> None:
    """手工建 CSI PV 并绑上 PVC(real._juicefs_fs_base_sync 读 PV volumeAttributes.subPath)。"""
    from app.core.k8s.base import JUICEFS_PVC_NAME

    pv_name = f"pvc-conf-{uuid.uuid4().hex[:8]}"
    real.core.create_persistent_volume(
        k8s_client.V1PersistentVolume(
            metadata=k8s_client.V1ObjectMeta(name=pv_name),
            spec=k8s_client.V1PersistentVolumeSpec(
                capacity={"storage": "1Gi"},
                access_modes=["ReadWriteMany"],
                csi=k8s_client.V1CSIPersistentVolumeSource(
                    driver="csi.juicefs.com",
                    volume_handle=pv_name,
                    volume_attributes={"subPath": pv_name},
                ),
            ),
        )
    )
    real.core.patch_namespaced_persistent_volume_claim(
        JUICEFS_PVC_NAME, tenant_ns, {"spec": {"volumeName": pv_name}}
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
        ns, sub = backend.namespace, f"confq-{uuid.uuid4().hex[:8]}"
        if backend.kind == "fake":
            assert backend.fake is not None
            await backend.impl.set_disk_quota(ns, sub, 10)
            await backend.impl.set_disk_quota(ns, sub, 10)  # 幂等重放
            assert backend.fake.disk_quotas[(ns, sub)] == 10
            await backend.impl.delete_disk_quota(ns, sub)
            await backend.impl.delete_disk_quota(ns, sub)  # 无配额记录视为成功
            assert (ns, sub) not in backend.fake.disk_quotas
        else:
            _bind_juicefs_pvc(backend.real, ns)
            platform_ns: str = backend.real.settings.k8s_platform_namespace
            with pytest.raises(RuntimeError, match="awaiting completion"):
                await backend.impl.set_disk_quota(ns, sub, 10)  # 已创建,等完成
            _mark_job_succeeded(backend.real, platform_ns, _quota_job_name(sub, True))
            await backend.impl.set_disk_quota(ns, sub, 10)  # 完成返回
            with pytest.raises(RuntimeError, match="awaiting completion"):
                await backend.impl.delete_disk_quota(ns, sub)
            _mark_job_succeeded(backend.real, platform_ns, _quota_job_name(sub, False))
            await backend.impl.delete_disk_quota(ns, sub)  # 完成返回
