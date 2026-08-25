"""probe_cluster(fake)、distro 派生、cluster_status 单行 upsert。"""

import pytest
from sqlalchemy import func, select

from app.core.k8s import set_orchestrator
from app.core.k8s.base import derive_distro
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


async def test_patrol_unreachable_saves_error_skips_nodes(sm, fake):
    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from app.modules.nodes.models import NodeSpec
    from app.modules.nodes.patrol import node_spec_patrol

    fake.fail_probe = True
    counts = await node_spec_patrol(sm)
    assert counts["probe_ok"] == 0 and counts["upserted"] == 0
    async with sm() as session:
        row = await service.get_cluster_status(session)
        specs = (await session.execute(sa_select(func.count()).select_from(NodeSpec))).scalar()
    assert row is not None and row.api_reachable is False and row.error
    assert specs == 0


async def test_require_hami_ready_gate(sm, fake):
    from datetime import timedelta

    from app.core.errors import AppError, ErrorCode

    # 无缓存 → 拒(先清掉 conftest 预置行)
    async with sm() as session:
        row = await service.get_cluster_status(session)
        if row is not None:
            await session.delete(row)
            await session.commit()
    async with sm() as session:
        with pytest.raises(AppError) as exc:
            await service.require_hami_ready(session)
        assert exc.value.code == ErrorCode.CLUSTER_NOT_READY
    # 新鲜且就绪 → 放行
    async with sm() as session:
        await service.save_cluster_probe(session, await fake.probe_cluster())
        await session.commit()
    async with sm() as session:
        await service.require_hami_ready(session)
    # HAMi 未就绪 → 拒
    fake.probe_hami_ready = False
    async with sm() as session:
        await service.save_cluster_probe(session, await fake.probe_cluster())
        await session.commit()
    async with sm() as session:
        with pytest.raises(AppError):
            await service.require_hami_ready(session)
    # 陈旧 → 拒(即使 hami_ready=True)
    fake.probe_hami_ready = True
    async with sm() as session:
        row = await service.save_cluster_probe(session, await fake.probe_cluster())
        await session.commit()
    async with sm() as session:
        row = await service.get_cluster_status(session)
        assert row is not None
        row.probed_at = row.probed_at - timedelta(minutes=11)
        await session.commit()
    async with sm() as session:
        with pytest.raises(AppError) as exc:
            await service.require_hami_ready(session)
        assert exc.value.detail == {"reason": "probe_stale"}


class TestGateWiring:
    """三入口门禁与 k3s runtimeClassName 下发。"""

    async def test_shared_create_blocked_when_hami_down(self, sm, fake, client):
        from app.modules.nodes.models import ClusterStatus
        from tests.helpers import create_user_with_key, fund_wallet
        from tests.test_catalog import seed_skus

        await seed_skus(sm)
        headers, user_id, key_id = await create_user_with_key(client)
        await fund_wallet(sm, user_id)
        async with sm() as session:
            row = await session.get(ClusterStatus, 1)
            assert row is not None
            row.hami_ready = False
            await session.commit()
        skus = (await client.get("/api/v1/skus")).json()
        shared = next(s for s in skus if s["tier"] == "shared_std")
        dedicated = next(s for s in skus if s["tier"] == "dedicated")
        images = (await client.get("/api/v1/images")).json()
        body = {
            "sku_id": shared["id"],
            "gpu_count": 1,
            "image_ref": images[0]["image_ref"],
            "ssh_key_ids": [key_id],
        }
        resp = await client.post("/api/v1/instances", json=body, headers=headers)
        assert resp.status_code == 409, resp.text
        assert resp.json()["code"] == "CLUSTER_NOT_READY"
        # dedicated 不受门禁影响:直接创建成功
        resp2 = await client.post(
            "/api/v1/instances", json={**body, "sku_id": dedicated["id"]}, headers=headers
        )
        assert resp2.status_code == 202, resp2.text

    async def test_create_blocked_when_storage_class_missing(self, sm, fake, client):
        """SC 名对不上/档位没装 → 即时 409,而不是让用户等 300 秒 Pending 超时判 failed。

        门禁必须按名核对,不能只判「集群里有任意一个 SC」。
        """
        from app.modules.nodes.models import ClusterStatus
        from tests.helpers import create_user_with_key, fund_wallet
        from tests.test_catalog import seed_skus

        await seed_skus(sm)
        headers, user_id, key_id = await create_user_with_key(client, "13900000077")
        await fund_wallet(sm, user_id)
        async with sm() as session:
            row = await session.get(ClusterStatus, 1)
            assert row is not None
            row.storage_classes = ["local-path"]  # 有 SC,但不是实例盘要的那只
            await session.commit()
        skus = (await client.get("/api/v1/skus")).json()
        images = (await client.get("/api/v1/images")).json()
        resp = await client.post(
            "/api/v1/instances",
            json={
                "sku_id": next(s for s in skus if s["tier"] == "dedicated")["id"],
                "gpu_count": 1,
                "image_ref": images[0]["image_ref"],
                "ssh_key_ids": [key_id],
            },
            headers=headers,
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["missing"] == ["topolvm-provisioner"]
        # 数据盘同理:不能卖一块永远挂不上、却按日计费的盘
        resp = await client.post(
            "/api/v1/disks", json={"name": "d", "size_gb": 100}, headers=headers
        )
        assert resp.status_code == 409, resp.text

    async def test_k3s_shared_pod_gets_nvidia_runtime(self, sm, fake):
        from app.core.gpu_adapter import build_gpu_request

        k3s = build_gpu_request(
            tier="shared_std",
            gpu_count=1,
            gpu_cores_pct=50,
            vram_gb=8,
            mig_profile=None,
            pool_label="hami",
            distro="k3s",
        )
        assert k3s.runtime_class == "nvidia"
        rke2 = build_gpu_request(
            tier="shared_std",
            gpu_count=1,
            gpu_cores_pct=50,
            vram_gb=8,
            mig_profile=None,
            pool_label="hami",
            distro="rke2",
        )
        assert rke2.runtime_class is None
        kata = build_gpu_request(
            tier="dedicated",
            gpu_count=1,
            gpu_cores_pct=100,
            vram_gb=24,
            mig_profile=None,
            pool_label="kata",
            distro="k3s",
        )
        assert kata.runtime_class == "kata-qemu"

    async def test_build_pod_spec_with_cluster_reads_distro(self, sm, fake):
        from app.core.k8s.base import ClusterProbe
        from app.modules.orchestrator.models import Instance
        from app.modules.orchestrator.service import _encode_token, build_pod_spec_with_cluster

        async with sm() as session:
            await service.save_cluster_probe(
                session, ClusterProbe(api_reachable=True, k8s_version="v1.33.4+k3s1", distro="k3s")
            )
            await session.commit()
        instance = Instance(
            user_id=1,
            uuid="i-rt",
            name="rt",
            status="creating",
            sku_id=1,
            gpu_count=1,
            image_ref="img:x",
            jupyter_token=_encode_token("t", instance_uuid="i-rt"),
            ssh_port=30022,
            authorized_keys=[],
            k8s_namespace="tenant-1",
            spec={
                "tier": "shared_std",
                "pool_label": "hami",
                "vram_gb": 8,
                "gpu_cores_pct": 50,
                "vcpu": 8,
                "mem_gb": 32,
                "disk_gb": 100,
                "mig_profile": None,
            },
        )
        async with sm() as session:
            pod = await build_pod_spec_with_cluster(session, instance)
        assert pod.runtime_class == "nvidia"


async def test_derive_node_distro_chain(sm, fake):
    """派生链:探测缓存 > agent 版本后缀 > rke2 兜底。"""
    from app.core.k8s.base import ClusterProbe

    # conftest 预置 rke2 探测
    async with sm() as session:
        assert await service.derive_node_distro(session, {}) == "rke2"
    async with sm() as session:
        await service.save_cluster_probe(
            session, ClusterProbe(api_reachable=True, k8s_version="v1.33.4+k3s1", distro="k3s")
        )
        await session.commit()
    async with sm() as session:
        assert await service.derive_node_distro(session, {}) == "k3s"
        # 缓存无 distro(未知发行版)→ 回落 agent 版本后缀
        row = await service.get_cluster_status(session)
        assert row is not None
        row.distro = None
        await session.commit()
    async with sm() as session:
        cfg = {"cluster_agent_version": "v1.36.3+k3s1"}
        assert await service.derive_node_distro(session, cfg) == "k3s"
        assert (
            await service.derive_node_distro(session, {"cluster_agent_version": "v1.36.2"})
            == "rke2"
        )


def test_render_registries_yaml():
    """验收:server_url → 含 NodePort endpoint;覆盖值优先;未配置返回空。"""
    from app.modules.nodes.service import render_registries_yaml

    out = render_registries_yaml({"cluster_server_url": "https://10.0.0.1:9345"})
    assert "http://10.0.0.1:30500" in out and '"*": {}' in out
    assert "registry.superdl.local" in out
    override = render_registries_yaml(
        {"cluster_server_url": "https://10.0.0.1:9345", "node_registries_yaml": "mirrors: {}"}
    )
    assert override == "mirrors: {}"
    assert render_registries_yaml({}) == ""
    v6 = render_registries_yaml({"cluster_server_url": "https://[fd00::1]:9345"})
    assert "http://[fd00::1]:30500" in v6


class TestClusterEndpoints:
    async def test_status_endpoint_reads_cache(self, sm, fake, client):
        from app.modules.nodes.patrol import node_spec_patrol
        from tests.test_catalog import admin_headers

        await node_spec_patrol(sm)
        headers = await admin_headers(sm, client, role="readonly")
        resp = await client.get("/api/admin/v1/cluster/status", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["api_reachable"] is True and body["distro"] == "rke2"
        assert body["pools"] == {"kata": 1, "hami": 1, "mig": 1}
        comp = {c["key"]: c for c in body["components"]}
        assert comp["hami"]["ok"] and comp["monitoring"]["ok"] and comp["storage"]["ok"]
        assert comp["hami"]["fix_hint"] is None
        assert body["config"]["server_url_set"] is False  # 测试未配置 cluster 键
        assert body["config"]["prometheus_url_set"] is False  # 默认 localhost

    async def test_status_empty_cache_shows_checklist(self, sm, client):
        from app.modules.nodes.models import ClusterStatus
        from tests.test_catalog import admin_headers

        async with sm() as session:
            row = await session.get(ClusterStatus, 1)
            if row:
                await session.delete(row)
                await session.commit()
        headers = await admin_headers(sm, client)
        body = (await client.get("/api/admin/v1/cluster/status", headers=headers)).json()
        assert body["api_reachable"] is False and body["probed_at"] is None
        comp = {c["key"]: c for c in body["components"]}
        assert not comp["hami"]["ok"] and comp["hami"]["fix_hint"]
        assert "helmfile" in comp["monitoring"]["fix_hint"]

    async def test_test_connection_upserts_and_returns(self, sm, fake, client):
        from tests.test_catalog import admin_headers

        headers = await admin_headers(sm, client)
        resp = await client.post("/api/admin/v1/cluster/test-connection", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["api_reachable"] is True
        async with sm() as session:
            row = await service.get_cluster_status(session)
        assert row is not None and row.hami_ready

    async def test_test_connection_unreachable_502(self, sm, fake, client):
        from tests.test_catalog import admin_headers

        fake.fail_probe = True
        headers = await admin_headers(sm, client)
        resp = await client.post("/api/admin/v1/cluster/test-connection", headers=headers)
        assert resp.status_code == 502, resp.text
        body = resp.json()
        assert body["code"] == "CLUSTER_NOT_READY"
        assert "fake: connection refused" in body["message"]
        async with sm() as session:
            row = await service.get_cluster_status(session)
        assert row is not None and row.api_reachable is False and row.error

    async def test_readonly_cannot_test_connection(self, sm, fake, client):
        from tests.test_catalog import admin_headers

        headers = await admin_headers(sm, client, role="readonly")
        resp = await client.post("/api/admin/v1/cluster/test-connection", headers=headers)
        assert resp.status_code == 403
