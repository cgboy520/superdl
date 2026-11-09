"""Fake/Real CephFS volume contract; Real cases are gated by SUPERDL_TEST_KUBECONFIG."""

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from kubernetes import client as k8s_client

from app.core.k8s.base import CephVolumeSpec, K8sOrchestrator, ceph_pv_name, ceph_secret_name
from app.core.k8s.fake import FakeOrchestrator
from tests.helpers import use_kubeconfig, wait_pvc_bound


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
        pytest.skip("SUPERDL_TEST_KUBECONFIG unset, skipping the Real conformance")
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


class TestCephVolumeContract:
    """CephFS volume objects (Secret + static PV + bound PVC): ensure is synchronous and
    idempotent, delete tolerates missing objects."""

    async def test_ensure_then_delete(self, backend: Backend) -> None:
        ns = backend.namespace
        sub = f"u1-disk-{uuid.uuid4().hex}"
        spec = CephVolumeSpec(
            namespace=ns,
            pvc_name=f"disk-{sub[-32:]}",
            pv_name=ceph_pv_name(sub),
            secret_name=ceph_secret_name(sub),
            cluster_id="00000000-0000-0000-0000-000000000000",
            fs_name="cephfs",
            root_path=f"/volumes/conf/{sub}/0",
            client_id=f"conf.{sub}",
            client_key="AQBconformanceKey==",
            size_gb=1,
        )
        await backend.impl.ensure_ceph_volume(spec)
        await backend.impl.ensure_ceph_volume(spec)
        if backend.kind == "fake":
            assert backend.fake is not None
            assert backend.fake.ceph_volumes[(ns, spec.pvc_name)] == spec
        else:
            pvc: Any = backend.real.core.read_namespaced_persistent_volume_claim(spec.pvc_name, ns)
            assert pvc.spec.access_modes == ["ReadWriteMany"]
            await wait_pvc_bound(backend.real.core, ns, spec.pvc_name)

        for _ in range(2):
            await backend.impl.delete_ceph_volume(
                ns, pvc_name=spec.pvc_name, pv_name=spec.pv_name, secret_name=spec.secret_name
            )
        if backend.kind == "fake":
            assert backend.fake is not None
            assert (ns, spec.pvc_name) not in backend.fake.ceph_volumes
