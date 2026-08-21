"""WP27 批1:probe_cluster(fake)、distro 派生、cluster_status 单行 upsert。"""

import pytest
from sqlalchemy import func, select

from app.core.k8s import set_orchestrator
from app.core.k8s.base import ClusterProbe, derive_distro
from app.core.k8s.fake import FakeOrchestrator
from app.modules.nodes import service
from app.modules.nodes.models import ClusterStatus


@pytest.fixture
def fake():
    orch = FakeOrchestrator()
    set_orchestrator(orch)
    yield orch
    set_orchestrator(None)


@pytest.mark.parametrize(
    ("git_version", "expected"),
    [
        ("v1.36.2+rke2r1", "rke2"),
        ("v1.33.4+k3s1", "k3s"),
        ("v1.36.2", None),
        ("", None),
        (None, None),
    ],
)
def test_derive_distro(git_version, expected):
    assert derive_distro(git_version) == expected


async def test_fake_probe_defaults(fake):
    probe = await fake.probe_cluster()
    assert probe.api_reachable and probe.distro == "rke2" and probe.hami_ready
    assert probe.kata_runtimeclass
    assert probe.pools == {"kata": 1, "hami": 1, "mig": 1}  # fake 三池各一节点


async def test_fake_probe_failure_modes(fake):
    fake.fail_probe = True
    down = await fake.probe_cluster()
    assert down.api_reachable is False and down.error
    fake.fail_probe = False
    fake.probe_override = ClusterProbe(api_reachable=True, k8s_version="v1.33.4+k3s1", distro="k3s")
    assert (await fake.probe_cluster()).distro == "k3s"


async def test_save_probe_upserts_single_row(sm, fake):
    probe = await fake.probe_cluster()
    async with sm() as session:
        await service.save_cluster_probe(session, probe)
        await session.commit()
    fake.probe_hami_ready = False
    probe2 = await fake.probe_cluster()
    async with sm() as session:
        await service.save_cluster_probe(session, probe2)
        await session.commit()
    async with sm() as session:
        count = (await session.execute(select(func.count()).select_from(ClusterStatus))).scalar()
        row = await service.get_cluster_status(session)
    assert count == 1
    assert row is not None and row.hami_ready is False and row.distro == "rke2"
    assert row.detail == {"runtime_classes": ["kata-qemu", "nvidia"]}
    assert row.probed_at is not None


async def test_get_status_empty(sm):
    async with sm() as session:
        assert await service.get_cluster_status(session) is None
