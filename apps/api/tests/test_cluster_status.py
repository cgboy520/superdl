"""probe_cluster(fake)、distro 派生、cluster_status 单行 upsert。"""

# 白盒用例:直探模块内部
# pyright: reportPrivateUsage=false

import pytest
from sqlalchemy import func, select

from app.core.k8s.base import derive_distro
from app.modules.nodes import service
from app.modules.nodes.models import ClusterStatus
from tests.helpers import admin_headers, create_user_with_key, fund_wallet, funded_user, seed_skus

pytestmark = pytest.mark.usefixtures("fake_auto_ready")


def _fact(component: dict, key: str) -> tuple[str, str]:
    """体检项里取一条事实的 (value, tone)。"""
    f = next(x for x in component["facts"] if x["key"] == key)
    return f["value"], f["tone"]


@pytest.mark.parametrize(
    ("git_version", "expected"),
    [
        ("v1.36.2+rke2r1", "rke2"),
        ("v1.33.4+k3s1", "k3s"),
        ("v1.36.2", None),
        (None, None),
    ],
)
def test_derive_distro(git_version, expected):
    assert derive_distro(git_version) == expected


async def test_save_probe_upserts_single_row(sm, fake_auto_ready):
    probe = await fake_auto_ready.probe_cluster()
    async with sm() as session:
        await service.save_cluster_probe(session, probe)
        await session.commit()
    fake_auto_ready.probe_hami_ready = False
    probe2 = await fake_auto_ready.probe_cluster()
    async with sm() as session:
        await service.save_cluster_probe(session, probe2)
        await session.commit()
    async with sm() as session:
        count = (await session.execute(select(func.count()).select_from(ClusterStatus))).scalar()
        row = await service.get_cluster_status(session)
    assert count == 1
    assert row is not None and row.hami_ready is False and row.distro == "rke2"


async def test_patrol_unreachable_saves_error_skips_nodes(sm, fake_auto_ready):
    from app.modules.nodes.models import NodeSpec
    from app.modules.nodes.patrol import node_spec_patrol

    fake_auto_ready.fail_probe = True
    counts = await node_spec_patrol(sm)
    assert counts["probe_ok"] == 0 and counts["upserted"] == 0
    async with sm() as session:
        row = await service.get_cluster_status(session)
        specs = (await session.execute(select(func.count()).select_from(NodeSpec))).scalar()
    assert row is not None and row.api_reachable is False and row.error
    assert specs == 0


async def test_require_hami_ready_gate(sm, fake_auto_ready):
    from datetime import timedelta

    from app.core.errors import AppError, ErrorCode

    # 无缓存 → 拒
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
        await service.save_cluster_probe(session, await fake_auto_ready.probe_cluster())
        await session.commit()
    async with sm() as session:
        await service.require_hami_ready(session)
    # HAMi 未就绪 → 拒
    fake_auto_ready.probe_hami_ready = False
    async with sm() as session:
        await service.save_cluster_probe(session, await fake_auto_ready.probe_cluster())
        await session.commit()
    async with sm() as session:
        with pytest.raises(AppError):
            await service.require_hami_ready(session)
    # 陈旧 → 拒(即使 hami_ready=True)
    fake_auto_ready.probe_hami_ready = True
    async with sm() as session:
        row = await service.save_cluster_probe(session, await fake_auto_ready.probe_cluster())
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

    async def test_shared_create_blocked_when_hami_down(self, sm, fake_auto_ready, client):
        from app.modules.nodes.models import ClusterStatus

        await seed_skus(sm)
        headers, user_id, key_id = await create_user_with_key(client)
        await fund_wallet(sm, user_id)
        async with sm() as session:
            row = await session.get(ClusterStatus, 1)
            assert row is not None
            row.hami_ready = False
            await session.commit()
        skus = (await client.get("/api/v1/skus")).json()
        shared = next(s for s in skus if s["pool_label"] == "hami")
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
        # dedicated 不受门禁影响
        resp2 = await client.post(
            "/api/v1/instances", json={**body, "sku_id": dedicated["id"]}, headers=headers
        )
        assert resp2.status_code == 202, resp2.text

    async def test_dedicated_create_blocked_when_kata_runtimeclass_missing(
        self, sm, fake_auto_ready, client
    ):
        """dedicated 档缺 RuntimeClass kata-qemu → 即时 409;shared 档不受影响。"""
        from app.modules.nodes.models import ClusterStatus

        await seed_skus(sm)
        headers, user_id, key_id = await create_user_with_key(client)
        await fund_wallet(sm, user_id)
        async with sm() as session:
            row = await session.get(ClusterStatus, 1)
            assert row is not None
            row.kata_runtimeclass = False
            await session.commit()
        skus = (await client.get("/api/v1/skus")).json()
        dedicated = next(s for s in skus if s["tier"] == "dedicated")
        shared = next(s for s in skus if s["pool_label"] == "hami")
        images = (await client.get("/api/v1/images")).json()
        body = {
            "sku_id": dedicated["id"],
            "gpu_count": 1,
            "image_ref": images[0]["image_ref"],
            "ssh_key_ids": [key_id],
        }
        resp = await client.post("/api/v1/instances", json=body, headers=headers)
        assert resp.status_code == 409, resp.text
        assert resp.json()["code"] == "CLUSTER_NOT_READY"
        resp2 = await client.post(
            "/api/v1/instances", json={**body, "sku_id": shared["id"]}, headers=headers
        )
        assert resp2.status_code == 202, resp2.text

    async def test_create_blocked_when_storage_class_missing(self, sm, fake_auto_ready, client):
        """SC 按名核对,对不上 → 即时 409。"""
        from app.modules.nodes.models import ClusterStatus

        await seed_skus(sm)
        headers, _user_id, key_id = await funded_user(client, sm, "13900000077")
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
        # 数据盘同理
        resp = await client.post(
            "/api/v1/disks", json={"name": "d", "size_gb": 100}, headers=headers
        )
        assert resp.status_code == 409, resp.text

    async def test_k3s_hami_pod_gets_nvidia_runtime(self, sm, fake_auto_ready):
        from app.core.gpu_adapter import build_gpu_request

        k3s = build_gpu_request(
            gpu_count=1,
            gpu_cores_pct=50,
            vram_gb=8,
            mig_profile=None,
            pool_label="hami",
            distro="k3s",
        )
        assert k3s.runtime_class == "nvidia"
        rke2 = build_gpu_request(
            gpu_count=1,
            gpu_cores_pct=50,
            vram_gb=8,
            mig_profile=None,
            pool_label="hami",
            distro="rke2",
        )
        assert rke2.runtime_class is None
        kata = build_gpu_request(
            gpu_count=1,
            gpu_cores_pct=100,
            vram_gb=24,
            mig_profile=None,
            pool_label="kata",
            distro="k3s",
        )
        assert kata.runtime_class == "kata-qemu"

    async def test_build_pod_spec_with_cluster_reads_distro(self, sm, fake_auto_ready):
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
                "tier": "shared",
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


async def test_derive_node_distro_chain(sm, fake_auto_ready):
    """派生链:探测缓存 > agent 版本后缀 > rke2 兜底。"""
    from app.core.k8s.base import ClusterProbe
    from app.core.platform_config import runtime_config_from_strings as rc

    # conftest 预置 rke2 探测
    async with sm() as session:
        assert await service.derive_node_distro(session, rc({})) == "rke2"
    async with sm() as session:
        await service.save_cluster_probe(
            session, ClusterProbe(api_reachable=True, k8s_version="v1.33.4+k3s1", distro="k3s")
        )
        await session.commit()
    async with sm() as session:
        assert await service.derive_node_distro(session, rc({})) == "k3s"
        # 缓存无 distro → 回落 agent 版本后缀
        row = await service.get_cluster_status(session)
        assert row is not None
        row.distro = None
        await session.commit()
    async with sm() as session:
        cfg = rc({"cluster_agent_version": "v1.36.3+k3s1"})
        assert await service.derive_node_distro(session, cfg) == "k3s"
        assert (
            await service.derive_node_distro(session, rc({"cluster_agent_version": "v1.36.2"}))
            == "rke2"
        )


class TestClusterEndpoints:
    async def test_status_endpoint_reads_cache(self, sm, fake_auto_ready, client):
        from app.modules.nodes.patrol import node_spec_patrol

        await node_spec_patrol(sm)
        headers = await admin_headers(sm, client, role="readonly")
        resp = await client.get("/api/admin/v1/cluster/status", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["api_reachable"] is True and body["distro"] == "rke2"
        assert body["pools"] == {"kata": 1, "hami": 1, "mig": 1, "cpu": 1}
        assert body["pools_ready"] == {"kata": 1, "hami": 1, "mig": 1, "cpu": 1}
        comp = {c["key"]: c for c in body["components"]}
        assert comp["hami"]["state"] == "ok"
        assert comp["monitoring"]["state"] == "ok" and comp["storage"]["state"] == "ok"
        assert comp["hami"]["fix_hint"] is None
        # 排障命令一直给(与状态无关),修复命令只在故障时给
        assert comp["gateway"]["diag_hint"] and "kubectl" in comp["gateway"]["diag_hint"]
        assert body["config"]["server_url_set"] is False  # 测试未配置 cluster 键
        assert body["config"]["prometheus_url_set"] is False  # 默认 localhost

    async def test_panel_carries_checkable_numbers_not_prose(self, sm, fake_auto_ready, client):
        """面板正面是可核对的数字与标识符。

        挂了说明后端又在拼中文散文:detail 字符串绕过两端 locales,en-US 下管理员看到中文,
        且 x/y 这类事实被压进句子里没法着色、没法排序。
        """
        from app.modules.nodes.patrol import node_spec_patrol

        await node_spec_patrol(sm)
        headers = await admin_headers(sm, client, role="readonly")
        body = (await client.get("/api/admin/v1/cluster/status", headers=headers)).json()
        comp = {c["key"]: c for c in body["components"]}
        assert "detail" not in comp["nodes"]  # 中文散文字段已删
        assert comp["nodes"]["headline"] == {"key": "ready", "value": "4/4", "tone": "normal"}
        gateway = comp["gateway"]
        assert gateway["headline"]["value"] == "6/6"
        # 抽屉对象表:每个 listener 的端口、挂载路由数、条件都在
        names = {o["name"] for o in gateway["objects"]}
        assert {"api-https", "app-https", "svc-https"} <= names
        listener = next(o for o in gateway["objects"] if o["name"] == "svc-https")
        assert listener["fields"]["port"] == "443"
        assert listener["fields"]["programmed"] == "true"
        # 事实里不该出现中文:value 一律是纯数据
        for c in body["components"]:
            for f in [*c["facts"], c["headline"] or {"value": ""}]:
                assert not any("\u4e00" <= ch <= "\u9fff" for ch in f["value"]), f

    async def test_status_empty_cache_shows_checklist(self, sm, client):
        """从未探测:十项判 unknown,但仍给安装命令 —— 体检卡在装机阶段兼作清单。"""
        from app.modules.nodes.models import ClusterStatus

        async with sm() as session:
            row = await session.get(ClusterStatus, 1)
            if row:
                await session.delete(row)
                await session.commit()
        headers = await admin_headers(sm, client)
        body = (await client.get("/api/admin/v1/cluster/status", headers=headers)).json()
        assert body["api_reachable"] is False and body["probed_at"] is None
        comp = {c["key"]: c for c in body["components"]}
        assert comp["hami"]["state"] == "unknown" and comp["hami"]["fix_hint"]
        assert "apply.sh" in comp["monitoring"]["fix_hint"]
        # 无探测缓存:档位留占位
        assert "apply.sh <full|light>" in comp["monitoring"]["fix_hint"]

    async def test_stale_probe_is_unknown_not_green(self, sm, fake_auto_ready, client):
        """快照超保鲜窗 → 全部 unknown,且不谎报修复命令。

        挂了说明陈旧假绿回归了:worker 停掉三小时,体检卡照样十项全绿,
        管理员据此判断集群健康。下发门禁一直有这个保鲜判定,体检卡此前没有。
        """
        from datetime import timedelta

        from app.modules.nodes.models import ClusterStatus
        from app.modules.nodes.patrol import node_spec_patrol

        await node_spec_patrol(sm)
        headers = await admin_headers(sm, client, role="readonly")
        body = (await client.get("/api/admin/v1/cluster/status", headers=headers)).json()
        assert all(c["state"] == "ok" for c in body["components"])
        async with sm() as session:
            row = await session.get(ClusterStatus, 1)
            assert row is not None
            row.probed_at = row.probed_at - timedelta(minutes=11)
            await session.commit()
        body = (await client.get("/api/admin/v1/cluster/status", headers=headers)).json()
        assert all(c["state"] == "unknown" for c in body["components"])
        assert all(c["fix_hint"] is None for c in body["components"])
        # 上次事实仍回:知道「上次是 4/4」比什么都不显示有用
        comp = {c["key"]: c for c in body["components"]}
        assert comp["nodes"]["headline"]["value"] == "4/4"

    async def test_api_unreachable_is_unknown(self, sm, fake_auto_ready, client):
        """探测到 API 不可达:事实全部不可信,不能拿上一轮的绿勾顶着。"""
        from app.modules.nodes.patrol import node_spec_patrol

        await node_spec_patrol(sm)
        fake_auto_ready.fail_probe = True
        await node_spec_patrol(sm)
        headers = await admin_headers(sm, client, role="readonly")
        body = (await client.get("/api/admin/v1/cluster/status", headers=headers)).json()
        assert all(c["state"] == "unknown" for c in body["components"])

    async def test_fix_hint_env_follows_probed_distro(self, sm, fake_auto_ready, client):
        """修复命令的档位跟实测发行版走:k3s → -e light。"""
        from app.modules.nodes.patrol import node_spec_patrol

        fake_auto_ready.probe_hami_ready = False
        fake_auto_ready.probe_k8s_version = "v1.36.3+k3s1"
        await node_spec_patrol(sm)
        headers = await admin_headers(sm, client, role="readonly")
        body = (await client.get("/api/admin/v1/cluster/status", headers=headers)).json()
        assert body["distro"] == "k3s"
        comp = {c["key"]: c for c in body["components"]}
        assert comp["hami"]["fix_hint"] == "deploy/cluster/apply.sh light -l name=hami"

    async def test_storage_component_uses_the_same_names_as_the_gate(
        self, sm, fake_auto_ready, client
    ):
        """体检页 storage 与 require_storage_classes 同一口径(按名核对)。

        缺数据盘 SC 不判红:数据盘不可售不影响开机。
        """
        from app.modules.nodes.patrol import node_spec_patrol

        fake_auto_ready.probe_storage_classes = ("local-path",)  # 不是实例盘要的那只
        await node_spec_patrol(sm)
        headers = await admin_headers(sm, client, role="readonly")
        body = (await client.get("/api/admin/v1/cluster/status", headers=headers)).json()
        storage = {c["key"]: c for c in body["components"]}["storage"]
        assert storage["state"] == "down"
        assert _fact(storage, "instanceDisk") == ("-", "bad")
        # 数据盘 SC 可选:缺它 tone 转 warn,状态仍 ok
        fake_auto_ready.probe_storage_classes = ("local-path", "topolvm-provisioner")
        await node_spec_patrol(sm)
        body = (await client.get("/api/admin/v1/cluster/status", headers=headers)).json()
        storage = {c["key"]: c for c in body["components"]}["storage"]
        assert storage["state"] == "ok"
        assert _fact(storage, "dataDisk") == ("-", "warn")

    async def test_kata_with_empty_pool_is_disabled_not_ok(self, sm, fake_auto_ready, client):
        """RuntimeClass 在、kata 池没有 Ready 节点 → disabled(未启用),不是绿勾。

        挂了说明独享档开不了机却显示全绿。库存解读不进体检项:能不能卖看 pools_ready。
        """
        from app.modules.nodes.patrol import node_spec_patrol

        fake_auto_ready.pool_capacity.pop("kata", None)
        await node_spec_patrol(sm)
        headers = await admin_headers(sm, client, role="readonly")
        body = (await client.get("/api/admin/v1/cluster/status", headers=headers)).json()
        kata = {c["key"]: c for c in body["components"]}["kata_runtimeclass"]
        assert kata["state"] == "disabled"
        assert kata["headline"] == {"key": "poolNodes", "value": "0", "tone": "normal"}
        assert _fact(kata, "runtimeClass")[0] == "kata-qemu"
        assert body["pools_ready"].get("kata", 0) == 0

    async def test_kata_pool_counts_only_ready_nodes(self, sm, fake_auto_ready, client):
        """池里节点全 NotReady = 没有节点。

        挂了说明又在数原始池成员:三台全 NotReady 的 kata 节点会显示「kata 池 3 节点」
        并判绿,独享档实际一台也开不了。
        """
        from app.core.k8s.base import NodeInfo
        from app.modules.nodes.patrol import node_spec_patrol

        fake_auto_ready.pool_capacity.pop("kata", None)
        fake_auto_ready.extra_nodes = [
            NodeInfo(
                name=f"kata-dead-{i}",
                pool_label="kata",
                gpu_total=8,
                gpu_used=0,
                status="NotReady",
            )
            for i in range(3)
        ]
        await node_spec_patrol(sm)
        headers = await admin_headers(sm, client, role="readonly")
        body = (await client.get("/api/admin/v1/cluster/status", headers=headers)).json()
        assert body["pools"]["kata"] == 3 and body["pools_ready"].get("kata", 0) == 0
        kata = {c["key"]: c for c in body["components"]}["kata_runtimeclass"]
        assert kata["state"] == "disabled" and kata["headline"]["value"] == "0"
        # 三台都在抽屉对象表里,各自带状态
        assert {o["fields"]["status"] for o in kata["objects"]} == {"NotReady"}

    async def test_partial_operand_rollout_is_degraded(self, sm, fake_auto_ready, client):
        """gpu-operator operand 没铺满 → degraded。

        挂了说明退回了「名字存在即绿」:今天判据是 Deployment 名字子串匹配,
        0/8 全崩的 operator 照样绿,体检等于没做。
        """
        from app.modules.nodes.patrol import node_spec_patrol

        fake_auto_ready.probe_gpu_operand_ready = 1
        await node_spec_patrol(sm)
        headers = await admin_headers(sm, client, role="readonly")
        body = (await client.get("/api/admin/v1/cluster/status", headers=headers)).json()
        comp = {c["key"]: c for c in body["components"]}
        assert comp["gpu_operator"]["state"] == "degraded"
        assert comp["gpu_operator"]["headline"]["value"] == "3/9"
        assert "apply.sh" in comp["gpu_operator"]["fix_hint"]
        assert comp["dcgm"]["state"] == "degraded" and comp["dcgm"]["headline"]["value"] == "1/3"

    async def test_one_bad_listener_degrades_gateway(self, sm, fake_auto_ready, client):
        """单个 listener 未 Programmed → 整体降级,对象表点名是哪个。

        挂了说明又只看整体 Programmed 条件:svc 子域全挂而面板显示绿勾,
        runbook J 节要人手敲 kubectl 才看得见。
        """
        from app.modules.nodes.patrol import node_spec_patrol

        fake_auto_ready.probe_unprogrammed_listeners = ("svc-https",)
        await node_spec_patrol(sm)
        headers = await admin_headers(sm, client, role="readonly")
        body = (await client.get("/api/admin/v1/cluster/status", headers=headers)).json()
        gateway = {c["key"]: c for c in body["components"]}["gateway"]
        assert gateway["state"] == "degraded" and gateway["headline"]["value"] == "5/6"
        bad = next(o for o in gateway["objects"] if o["name"] == "svc-https")
        assert bad["fields"]["programmed"] == "false" and bad["fields"]["reason"] == "Invalid"

    async def test_test_connection_upserts_and_returns(self, sm, fake_auto_ready, client):
        headers = await admin_headers(sm, client)
        resp = await client.post("/api/admin/v1/cluster/test-connection", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["api_reachable"] is True
        async with sm() as session:
            row = await service.get_cluster_status(session)
        assert row is not None and row.hami_ready

    async def test_test_connection_unreachable_502(self, sm, fake_auto_ready, client):
        fake_auto_ready.fail_probe = True
        headers = await admin_headers(sm, client)
        resp = await client.post("/api/admin/v1/cluster/test-connection", headers=headers)
        assert resp.status_code == 502, resp.text
        body = resp.json()
        assert body["code"] == "CLUSTER_NOT_READY"
        assert "fake: connection refused" in body["message"]
        async with sm() as session:
            row = await service.get_cluster_status(session)
        assert row is not None and row.api_reachable is False and row.error


class TestComponentProbe:
    """实时深探:请求路径直连 K8s 的第二个只读例外,降级路径必须兜住。"""

    async def test_probe_returns_pod_level_reasons(self, sm, fake_auto_ready, client):
        """快照答「就绪几个」,深探答「为什么不就绪」。

        挂了说明面板又只剩 x/y:DaemonSet 5/8 时管理员还是得自己去敲 kubectl describe。
        """
        from app.core.k8s.base import ComponentObject

        fake_auto_ready.detail_pods = {
            "hami": (
                ComponentObject(
                    name="hami-scheduler-abc",
                    fields={
                        "namespace": "kube-system",
                        "phase": "Pending",
                        "node": "spark-fdd3",
                        "reason": "ImagePullBackOff",
                        "restarts": "0",
                    },
                ),
            )
        }
        fake_auto_ready.detail_events = {
            "hami": (
                ComponentObject(
                    name="hami-scheduler-abc",
                    fields={
                        "reason": "Failed",
                        "message": "Back-off pulling image",
                        "count": "7",
                        "lastSeen": "2026-09-14T10:00:00Z",
                    },
                ),
            )
        }
        headers = await admin_headers(sm, client, role="readonly")
        resp = await client.get("/api/admin/v1/cluster/components/hami/probe", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["key"] == "hami"
        assert body["pods"][0]["fields"]["reason"] == "ImagePullBackOff"
        assert body["events"][0]["fields"]["count"] == "7"
        assert _fact(body, "podsNotReady") == ("1", "bad")

    async def test_probe_failure_degrades_to_503_not_500(self, sm, fake_auto_ready, client):
        """集群 API 抖动不能打穿管理端:503 + 快照仍可读。

        挂了说明深探把集群故障放大成了页面故障。
        """
        fake_auto_ready.fail_probe = True
        headers = await admin_headers(sm, client, role="readonly")
        resp = await client.get("/api/admin/v1/cluster/components/gateway/probe", headers=headers)
        assert resp.status_code == 503, resp.text
        assert resp.json()["message_key"] == "nodes.componentProbeFailed"
        # 快照端点不受影响
        assert (
            await client.get("/api/admin/v1/cluster/status", headers=headers)
        ).status_code == 200

    async def test_probe_unknown_key_404(self, sm, fake_auto_ready, client):
        headers = await admin_headers(sm, client, role="readonly")
        resp = await client.get("/api/admin/v1/cluster/components/nope/probe", headers=headers)
        assert resp.status_code == 404, resp.text

    async def test_probe_rate_limited(self, sm, fake_auto_ready, client):
        """限流护住集群 API:管理端刷新再快也打不爆 apiserver。"""
        from app.modules.nodes import service as nodes_service

        headers = await admin_headers(sm, client, role="readonly")
        for _ in range(nodes_service.COMPONENT_PROBE_MAX_PER_HOUR):
            assert (
                await client.get("/api/admin/v1/cluster/components/dcgm/probe", headers=headers)
            ).status_code == 200
        resp = await client.get("/api/admin/v1/cluster/components/dcgm/probe", headers=headers)
        assert resp.status_code == 429, resp.text

    async def test_probe_denied_for_finance_role(self, sm, fake_auto_ready, client):
        """角色边界:财务角色读不到集群诊断。"""
        headers = await admin_headers(sm, client, role="finance")
        resp = await client.get("/api/admin/v1/cluster/components/hami/probe", headers=headers)
        assert resp.status_code == 403, resp.text
