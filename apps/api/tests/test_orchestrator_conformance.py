"""Fake/Real 数据盘契约;Real 用例由 SUPERDL_TEST_KUBECONFIG 门控。"""

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
    kind: str
    namespace: str
    fake: FakeOrchestrator | None = None
    real: Any = None


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
    platform_ns: str = real.settings.k8s_platform_namespace
    try:
        real.core.create_namespace(
            k8s_client.V1Namespace(metadata=k8s_client.V1ObjectMeta(name=platform_ns))
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


class TestDataDiskContract:
    """数据盘 PVC 创建、扩容与删除为同步操作,幂等且只扩不缩。"""

    async def test_create_expand_delete(self, backend: Backend) -> None:
        ns = backend.namespace
        name = f"disk-{uuid.uuid4().hex}"
        await backend.impl.ensure_data_disk(ns, name, 1)
        await backend.impl.ensure_data_disk(ns, name, 1)
        if backend.kind == "fake":
            assert backend.fake is not None
            assert backend.fake.data_disks[(ns, name)] == 1
        else:
            pvc: Any = backend.real.core.read_namespaced_persistent_volume_claim(name, ns)
            assert pvc.spec.resources.requests["storage"] == "1Gi"
            assert pvc.spec.access_modes == ["ReadWriteMany"]

        await backend.impl.ensure_data_disk(ns, name, 2)
        if backend.kind == "fake":
            assert backend.fake is not None
            assert backend.fake.data_disks[(ns, name)] == 2
        else:
            grown: Any = backend.real.core.read_namespaced_persistent_volume_claim(name, ns)
            assert grown.spec.resources.requests["storage"] == "2Gi"

        await backend.impl.ensure_data_disk(ns, name, 1)
        if backend.kind == "fake":
            assert backend.fake is not None
            assert backend.fake.data_disks[(ns, name)] == 2
        else:
            same: Any = backend.real.core.read_namespaced_persistent_volume_claim(name, ns)
            assert same.spec.resources.requests["storage"] == "2Gi"

        await backend.impl.delete_data_disk(ns, name)
        await backend.impl.delete_data_disk(ns, name)
        if backend.kind == "fake":
            assert backend.fake is not None
            assert (ns, name) not in backend.fake.data_disks
