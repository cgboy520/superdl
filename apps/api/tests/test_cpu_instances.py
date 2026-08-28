"""纯 CPU 实例(tier=cpu / gpu_count=0)的资源申请、计费份数、容量与配额。

这一档与 GPU 档的差别集中在四个「0 是合法值」的位置,每处坏掉的后果都不一样:
- 资源申请:漏判会替不用卡的实例申请 nvidia.com/gpu,占掉真正卖卡的名额
- 计费份数:漏判会按 单价 × 0 卡 算出 ¥0.00,整档免费
- Pod 规格:漏判会把 0 卡当 1 卡「放大」,结果碰巧对、语义全错
- 配额:漏判会拿 0 去比 GPU 上限,等于没有闸门
"""

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.gpu_adapter import POOL_NODE_LABEL, build_gpu_request, spec_to_gpu_request
from app.core.k8s.base import GPU_MODEL_NODE_LABEL
from app.core.money import billing_units, hourly_cost
from app.core.policies import set_policy_overrides
from app.core.timeutil import now_utc
from app.modules.billing.models import BillHourly
from app.modules.billing.settlement import bill_amount
from app.modules.catalog import service as catalog_service
from app.modules.catalog.models import Sku
from app.modules.nodes.models import NodeSpec
from app.modules.orchestrator.models import Instance
from app.modules.orchestrator.reconciler import reconcile_once
from app.modules.orchestrator.service import _encode_token, build_pod_spec
from tests.helpers import (
    admin_headers,
    create_test_sku,
    create_user_with_key,
    drain,
    fund_wallet,
    seed_node_spec,
)

CPU_SKU = {
    "name": "CPU-8C16G",
    "gpu_model": "",
    "tier": "cpu",
    "gpu_cores_pct": 0,
    "vram_gb": 0,
    "oversell_cores": Decimal("1.00"),
    "pool_label": "cpu",
    "vcpu": 8,
    "mem_gb": 16,
    "price_hourly": Decimal("0.4900"),
    "max_gpus_per_instance": 0,
    "cuda_max": None,
}


def _cpu_spec(pool: str = "cpu", **extra) -> dict:
    """CPU SKU 落库时的 spec 快照(与 orchestrator._snapshot_spec 同构)。"""
    base = {
        "tier": "cpu",
        "pool_label": pool,
        "gpu_model": "",
        "gpu_model_selector": None,
        "mig_profile": None,
        "gpu_cores_pct": 0,
        "vram_gb": 0,
        "vcpu": 8,
        "mem_gb": 16,
        "disk_gb": 100,
    }
    base.update(extra)
    return base


def _instance(spec: dict, gpu_count: int = 0) -> Instance:
    return Instance(
        user_id=1,
        uuid="i-cpu",
        image_ref="img:latest",
        spec=spec,
        gpu_count=gpu_count,
        ssh_port=30022,
        jupyter_token=_encode_token("tok", instance_uuid="i-cpu"),
        authorized_keys=[],
        data_disk_id=None,
        k8s_namespace="tenant-1",
        status="creating",
        sku_id=1,
    )


class TestGpuRequest:
    def test_cpu_instance_requests_no_gpu_resource(self):
        """CPU 实例不申请任何 nvidia.com/*,且走 runc + userns。

        挂了 = 不用卡的实例也会占一张卡的 device-plugin 名额。
        """
        req = build_gpu_request(
            pool_label="cpu", gpu_count=0, gpu_cores_pct=0, vram_gb=0, mig_profile=None
        )
        assert req.resources == {}
        assert req.runtime_class is None
        assert req.host_users is False
        assert req.scheduler_name is None and req.annotations == {}
        assert req.node_selector == {POOL_NODE_LABEL: "cpu"}

    async def test_cpu_on_hami_pool_not_gated_on_hami(self, sm, fake):
        """HAMi 未就绪时,挂 hami 池的 CPU 实例仍可下发。

        挂了说明:门禁按池判而非按「要不要卡」判 —— HAMi 一挂,连根本不申请 GPU 的
        CPU 实例也开不出来。门禁判据必须与 build_gpu_request 同源:先看要不要卡,再看落哪个池。
        """
        from app.core.errors import AppError, ErrorCode
        from app.modules.nodes import service as nodes_service
        from app.modules.orchestrator.service import _require_cluster_for_pool

        fake.probe_hami_ready = False
        async with sm() as session:
            await nodes_service.save_cluster_probe(session, await fake.probe_cluster())
            await session.commit()
        async with sm() as session:
            # 要卡的:hami 未就绪 → 409
            with pytest.raises(AppError) as exc:
                await _require_cluster_for_pool(session, "hami", 1)
            assert exc.value.code == ErrorCode.CLUSTER_NOT_READY
            # 不要卡的:同一个池、同样未就绪 → 放行
            await _require_cluster_for_pool(session, "hami", 0)

    def test_cpu_on_hami_pool_still_requests_no_gpu(self):
        """gpu_count==0 的判定必须在池分支**之前**。

        挂了 = CPU 档挂 hami 池时会照 HAMi 语法申请 gpu/gpucores/gpumem,
        把 GPU 机上一张真卡判给一台不用卡的实例(本档允许挂 hami 正是为了吃空闲 CPU)。
        """
        req = build_gpu_request(
            pool_label="hami",
            gpu_count=0,
            gpu_cores_pct=50,  # 即便 SKU 侧脏数据带了份额,也不许拼进资源请求
            vram_gb=24,
            mig_profile=None,
            gpu_model="RTX4090",
            hami_gputype="NVIDIA GeForce RTX 4090",
        )
        assert not any(k.startswith("nvidia.com/") for k in req.resources)
        assert req.resources == {}
        assert req.scheduler_name is None
        # 不钉型号:无卡节点没有 superdl.io/gpu-model 标签,钉了必然 Pending
        assert req.node_selector == {POOL_NODE_LABEL: "hami"}
        assert GPU_MODEL_NODE_LABEL not in req.node_selector

    def test_snapshot_path_matches(self):
        req = spec_to_gpu_request(_cpu_spec("hami"), 0, hami_use_gputype=True)
        assert req.resources == {} and req.annotations == {}


class TestPodSpec:
    def test_cpu_instance_does_not_scale_vcpu_mem(self):
        """CPU 档倍率恒 1(不是「把 0 卡当 1 卡放大」)。

        挂了 = 倍率跟着 gpu_count 走,给 CPU 档加多份规格时会照着 0 卡乘。
        """
        pod = build_pod_spec(_instance(_cpu_spec()))
        assert pod.vcpu == 8 and pod.mem_gb == 16
        assert pod.gpu_resources == {}
        assert pod.disk_gb == 100

    def test_gpu_instance_still_scales(self):
        from tests.test_gpu_adapter import _spec

        inst = _instance(_spec("dedicated", "kata"), gpu_count=4)
        pod = build_pod_spec(inst)
        assert pod.vcpu == 8 * 4 and pod.mem_gb == 32 * 4


class TestBillingUnits:
    def test_zero_gpu_bills_one_unit(self):
        """CPU 实例按「整机一份」收,不是 单价 × 0 卡 = ¥0。

        挂了 = 纯 CPU 实例全平台免费,且余额护栏/停机判据一并归零(燃烧率恒 0)。
        """
        assert billing_units(0) == 1
        assert billing_units(1) == 1
        assert billing_units(8) == 8
        assert bill_amount(Decimal("0.4900"), 0, 3600) == Decimal("0.49")
        assert bill_amount(Decimal("0.4900"), 0, 1800) == Decimal("0.24")  # 0.245 → HALF_EVEN
        assert hourly_cost(Decimal("0.4900"), 0) == Decimal("0.49")
        assert hourly_cost(Decimal("1.6800"), 2) == Decimal("3.36")

    def test_negative_gpu_count_rejected(self):
        with pytest.raises(ValueError, match="gpu_count out of range"):
            billing_units(-1)


class TestCpuSkuValidation:
    """跨字段规则:CPU 规格三项恒 0 + 无型号,GPU 规格三项都不许为 0。"""

    async def _create(self, client: AsyncClient, headers: dict, **overrides):
        body = {
            "name": "x",
            "gpu_model": "",
            "tier": "cpu",
            "gpu_cores_pct": 0,
            "vram_gb": 0,
            "pool_label": "cpu",
            "vcpu": 8,
            "mem_gb": 16,
            "price_hourly": "0.49",
            "max_gpus_per_instance": 0,
        }
        body.update(overrides)
        return await client.post("/api/admin/v1/skus", json=body, headers=headers)

    async def test_cpu_sku_with_gpu_fields_rejected(self, client: AsyncClient, sm):
        headers = await admin_headers(sm, client)
        for field, value in (
            ("gpu_model", "RTX4090"),
            ("gpu_cores_pct", 50),
            ("vram_gb", 24),
            ("max_gpus_per_instance", 1),
            ("mig_profile", "1g.10gb"),
        ):
            resp = await self._create(client, headers, **{field: value})
            assert resp.status_code == 422, (field, resp.text)

    async def test_cpu_sku_clean_accepted(self, client: AsyncClient, sm):
        headers = await admin_headers(sm, client)
        resp = await self._create(client, headers, name="CPU-8C16G")
        assert resp.status_code == 201, resp.text
        assert resp.json()["tier"] == "cpu"

    async def test_gpu_sku_with_zeroed_fields_rejected(self, client: AsyncClient, sm):
        headers = await admin_headers(sm, client)
        gpu_body = {
            "tier": "shared",
            "pool_label": "hami",
            "gpu_model": "RTX4090",
            "gpu_cores_pct": 50,
            "vram_gb": 24,
            "max_gpus_per_instance": 1,
        }
        for field in ("gpu_cores_pct", "vram_gb", "max_gpus_per_instance"):
            resp = await self._create(client, headers, **{**gpu_body, field: 0})
            assert resp.status_code == 422, (field, resp.text)

    async def test_cpu_tier_cannot_take_kata_pool(self, client: AsyncClient, sm):
        """档位×池配对表对 CPU 档同样生效:cpu 档只许 cpu / hami 两池。"""
        headers = await admin_headers(sm, client)
        resp = await self._create(client, headers, pool_label="kata")
        assert resp.status_code == 400, resp.text
        assert resp.json()["message_key"] == "catalog.tierPoolMismatch"

    async def test_update_checks_merged_final_state(self, client: AsyncClient, sm):
        """部分更新只看终态:单改 vram_gb=0 的 GPU SKU 也要被拒。

        挂了 = 运营能把在售 GPU 规格改成「0 显存」,下发即 Pending 到超时。
        """
        sku_id = await create_test_sku(sm)
        headers = await admin_headers(sm, client)
        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}",
            json={"vram_gb": 0, "reason": "用例"},
            headers=headers,
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["message_key"] == "catalog.gpuSkuNeedsGpuFields"


class TestCapacity:
    async def _cpu_sku(self, sm, **overrides) -> int:
        async with sm() as session:
            sku = Sku(**{**CPU_SKU, "status": "on", **overrides})
            session.add(sku)
            await session.commit()
            return sku.id

    async def test_cpu_pool_node_yields_slots(self, client: AsyncClient, sm):
        """无卡节点整机可售:32 vCPU / 128G 上放 8C16G,受内存维封顶为 4 台。"""
        await seed_node_spec(sm, node_name="cpu-1", pool_label="cpu", gpu_model=None, gpu_count=0)
        async with sm() as session:
            spec = (await session.execute(select(Sku))).scalars()  # 触发 flush,无副作用
            _ = list(spec)
        await self._set_node_size(sm, "cpu-1", vcpu=32, mem_gb=64)
        sku_id = await self._cpu_sku(sm)
        market = (await client.get("/api/v1/skus")).json()
        row = next(s for s in market if s["id"] == sku_id)
        assert row["available_count"] == 4  # min(32//8, 64//16)

    async def test_cpu_on_gpu_node_capped_by_policy(self, client: AsyncClient, sm):
        """挂 hami 池时每节点只让出 gpu_node_cpu_instance_vcpu_cap 核;0 = 一台不卖。

        挂了 = CPU 实例会把 GPU 机的 CPU 吃光,卡还在却没有 CPU 可配,GPU 档跟着卖不动。
        """
        await seed_node_spec(sm, node_name="gpu-1", pool_label="hami")
        await self._set_node_size(sm, "gpu-1", vcpu=64, mem_gb=256)
        sku_id = await self._cpu_sku(sm, pool_label="hami")

        async def free() -> int:
            market = (await client.get("/api/v1/skus")).json()
            return next(s for s in market if s["id"] == sku_id)["available_count"]

        assert await free() == 2  # 默认 cap=16 → 16//8=2,内存按同比例折算 64//16=4
        async with sm() as session:
            await set_policy_overrides(session, {"gpu_node_cpu_instance_vcpu_cap": "0"})
            await session.commit()
        assert await free() == 0

    async def test_no_capacity_rejects_create(self, client: AsyncClient, sm, fake):
        """cap=0 时创建即 409(不是放行后 Pending 到超时)。"""
        await seed_node_spec(sm, node_name="gpu-1", pool_label="hami")
        sku_id = await self._cpu_sku(sm, pool_label="hami")
        async with sm() as session:
            await set_policy_overrides(session, {"gpu_node_cpu_instance_vcpu_cap": "0"})
            await session.commit()
        headers, user_id, key_id = await create_user_with_key(client, "13900000301")
        await fund_wallet(sm, user_id)
        resp = await client.post(
            "/api/v1/instances",
            json={
                "sku_id": sku_id,
                "gpu_count": 0,
                "image_ref": "registry.superdl.local/pytorch:2.9.0-cu128",
                "ssh_key_ids": [key_id],
            },
            headers=headers,
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["message_key"] == "orchestrator.noCapacityCpu"

    @staticmethod
    async def _set_node_size(
        sm: async_sessionmaker[AsyncSession], node_name: str, *, vcpu: int, mem_gb: int
    ) -> None:
        from app.modules.nodes.models import NodeSpec

        async with sm() as session:
            row = (
                await session.execute(select(NodeSpec).where(NodeSpec.node_name == node_name))
            ).scalar_one()
            row.vcpu, row.mem_gb = vcpu, mem_gb
            await session.commit()


class TestSellableGate:
    async def test_cpu_sku_listing_checks_pool_only(self, client: AsyncClient, sm):
        """CPU 规格上架只看「池里有 Ready 节点」——按型号匹配对它恒不成立。"""
        async with sm() as session:
            sku = Sku(**{**CPU_SKU, "status": "off"})
            session.add(sku)
            await session.commit()
            sku_id = sku.id
        headers = await admin_headers(sm, client)
        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}", json={"status": "on", "reason": "用例"}, headers=headers
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["message_key"] == "catalog.skuNotSellableCpu"

        await seed_node_spec(sm, node_name="cpu-1", pool_label="cpu", gpu_model=None, gpu_count=0)
        resp = await client.patch(
            f"/api/admin/v1/skus/{sku_id}", json={"status": "on", "reason": "用例"}, headers=headers
        )
        assert resp.status_code == 200, resp.text


class TestFullChain:
    async def test_cpu_instance_creates_bills_and_frees_quota(self, client: AsyncClient, sm, fake):
        """gpu_count=0 全链路:创建 → Pod 无 GPU 资源 → running → 尾账非 0。

        挂了 = CPU 档要么下发时抢卡,要么整档免费(单价 × 0 卡)。
        """
        await seed_node_spec(sm, node_name="cpu-1", pool_label="cpu", gpu_model=None, gpu_count=0)
        async with sm() as session:
            sku = Sku(**{**CPU_SKU, "status": "on"})
            session.add(sku)
            await session.commit()
            sku_id = sku.id

        headers, user_id, key_id = await create_user_with_key(client, "13900000302")
        await fund_wallet(sm, user_id)
        resp = await client.post(
            "/api/v1/instances",
            json={
                "sku_id": sku_id,
                "gpu_count": 0,
                "image_ref": "registry.superdl.local/pytorch:2.9.0-cu128",
                "ssh_key_ids": [key_id],
            },
            headers=headers,
        )
        assert resp.status_code == 202, resp.text
        uuid = resp.json()["uuid"]
        assert resp.json()["gpu_count"] == 0

        await drain(sm)
        pod = fake.pods[(f"tenant-{user_id}", uuid)].spec
        assert pod.gpu_resources == {}
        assert not any(k.startswith("nvidia.com/") for k in pod.gpu_resources)
        assert pod.node_selector == {POOL_NODE_LABEL: "cpu"}
        assert pod.vcpu == 8 and pod.mem_gb == 16

        fake.mark_ready(f"tenant-{user_id}", uuid)
        await reconcile_once(sm)
        assert (await client.get(f"/api/v1/instances/{uuid}", headers=headers)).json()[
            "status"
        ] == "running"

        # 跑 30 分钟后停机 → 尾账必须 > 0(0.49/时 × 0.5h ≈ 0.25)
        from tests.test_billing_flow import backdate_running_event

        await backdate_running_event(sm, uuid, 30)
        assert (await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)).status_code
        async with sm() as session:
            inst = (
                await session.execute(select(Instance).where(Instance.uuid == uuid))
            ).scalar_one()
            bills = list(
                (
                    await session.execute(
                        select(BillHourly).where(BillHourly.instance_id == inst.id)
                    )
                ).scalars()
            )
        assert bills, "CPU 实例必须出账单行"
        assert sum(b.amount for b in bills) > Decimal("0.00")
        # 账单行照实存 gpu_count=0:份数由 billing_units 还原,不靠往列里塞 1 来自洽
        assert all(b.gpu_count == 0 for b in bills)

    async def test_gpu_count_must_be_zero_for_cpu_sku(self, client: AsyncClient, sm, fake):
        await seed_node_spec(sm, node_name="cpu-1", pool_label="cpu", gpu_model=None, gpu_count=0)
        async with sm() as session:
            sku = Sku(**{**CPU_SKU, "status": "on"})
            session.add(sku)
            await session.commit()
            sku_id = sku.id
        headers, user_id, key_id = await create_user_with_key(client, "13900000303")
        await fund_wallet(sm, user_id)
        resp = await client.post(
            "/api/v1/instances",
            json={
                "sku_id": sku_id,
                "gpu_count": 1,
                "image_ref": "registry.superdl.local/pytorch:2.9.0-cu128",
                "ssh_key_ids": [key_id],
            },
            headers=headers,
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["message_key"] == "orchestrator.cpuSkuNoGpu"

    async def test_gpu_sku_rejects_zero_gpu_count(self, client: AsyncClient, sm, fake):
        """契约层允许 gpu_count ge=0,GPU 规格的「0 卡实例」必须被服务层挡住。

        挂了 = 用户能以 0 卡下单 GPU 规格,拿到一台不带卡却按整机价计费的机器。
        """
        await seed_node_spec(sm)
        sku_id = await create_test_sku(sm)
        headers, user_id, key_id = await create_user_with_key(client, "13900000304")
        await fund_wallet(sm, user_id)
        resp = await client.post(
            "/api/v1/instances",
            json={
                "sku_id": sku_id,
                "gpu_count": 0,
                "image_ref": "registry.superdl.local/pytorch:2.9.0-cu128",
                "ssh_key_ids": [key_id],
            },
            headers=headers,
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["message_key"] == "orchestrator.gpuCountRange"


class TestVcpuQuota:
    async def test_vcpu_capped_and_freed_on_release(self, client: AsyncClient, sm, fake):
        """max_vcpus_per_user 封顶 CPU 实例的 vCPU 合计,释放后名额归还。

        挂了 = 单个用户可以无限开 CPU 实例(GPU 维的闸门对 gpu_count=0 恒不触发)。
        """
        await seed_node_spec(sm, node_name="cpu-1", pool_label="cpu", gpu_model=None, gpu_count=0)
        async with sm() as session:
            sku = Sku(**{**CPU_SKU, "status": "on"})
            session.add(sku)
            await set_policy_overrides(session, {"max_vcpus_per_user": "8"})  # 只放得下一台
            await session.commit()
            sku_id = sku.id

        headers, user_id, key_id = await create_user_with_key(client, "13900000305")
        await fund_wallet(sm, user_id)

        async def create():
            return await client.post(
                "/api/v1/instances",
                json={
                    "sku_id": sku_id,
                    "gpu_count": 0,
                    "image_ref": "registry.superdl.local/pytorch:2.9.0-cu128",
                    "ssh_key_ids": [key_id],
                },
                headers=headers,
            )

        first = await create()
        assert first.status_code == 202, first.text
        uuid = first.json()["uuid"]

        second = await create()
        assert second.status_code == 400, second.text
        # 三个同族配额同码不同 message_key:前端按 message_key 分文案(reference/i18n.md),
        # 给 vCPU 单开一个 ErrorCode 只会让三兄弟长出两种码
        assert second.json()["code"] == "VALIDATION_ERROR"
        assert second.json()["message_key"] == "orchestrator.vcpuQuota"

        # 释放后名额归还
        await drain(sm)
        fake.mark_ready(f"tenant-{user_id}", uuid)
        await reconcile_once(sm)
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        await client.delete(f"/api/v1/instances/{uuid}", headers=headers)
        await drain(sm)
        await reconcile_once(sm)

        third = await create()
        assert third.status_code == 202, third.text

    async def test_gpu_instance_does_not_consume_vcpu_quota(self, client: AsyncClient, sm, fake):
        """两维互不相交:GPU 实例不吃 vCPU 额度(否则 8 卡户会被 CPU 额度先卡死)。"""
        await seed_node_spec(sm)
        sku_id = await create_test_sku(sm)
        async with sm() as session:
            await set_policy_overrides(session, {"max_vcpus_per_user": "1"})
            await session.commit()
        headers, user_id, key_id = await create_user_with_key(client, "13900000306")
        await fund_wallet(sm, user_id)
        resp = await client.post(
            "/api/v1/instances",
            json={
                "sku_id": sku_id,
                "gpu_count": 1,
                "image_ref": "registry.superdl.local/pytorch:2.9.0-cu128",
                "ssh_key_ids": [key_id],
            },
            headers=headers,
        )
        assert resp.status_code == 202, resp.text


class TestSellableCpuSlots:
    """纯函数口径:预算按池分化,内存维与 vCPU 维取小。"""

    @staticmethod
    def _node(pool: str, vcpu: int, mem_gb: int, status: str = "Ready") -> NodeSpec:
        return NodeSpec(
            node_name=f"n-{pool}",
            pool_label=pool,
            vcpu=vcpu,
            mem_gb=mem_gb,
            status=status,
            last_seen=now_utc(),
        )

    def test_cpu_pool_uses_whole_node(self):
        nodes = [self._node("cpu", 32, 128)]
        assert catalog_service.sellable_cpu_slots(8, 16, nodes, gpu_node_vcpu_cap=16) == 4

    def test_memory_dimension_binds(self):
        """内存不够时以内存为准:8 台的 vCPU、只有 2 台的内存 → 2。"""
        nodes = [self._node("cpu", 64, 32)]
        assert catalog_service.sellable_cpu_slots(8, 16, nodes, gpu_node_vcpu_cap=16) == 2

    def test_gpu_node_capped_and_memory_prorated(self):
        """GPU 节点按 cap 折算,内存按同比例折算(否则一台就把整机内存吃光)。"""
        nodes = [self._node("hami", 64, 256)]
        # cap=16 → vCPU 预算 16(2 台),内存预算 256×16//64=64(1 台)→ 取 1
        assert catalog_service.sellable_cpu_slots(8, 64, nodes, gpu_node_vcpu_cap=16) == 1

    def test_cap_zero_forbids_gpu_nodes(self):
        nodes = [self._node("hami", 64, 256)]
        assert catalog_service.sellable_cpu_slots(8, 16, nodes, gpu_node_vcpu_cap=0) == 0

    def test_non_ready_nodes_excluded(self):
        nodes = [self._node("cpu", 32, 128, status="NotReady")]
        assert catalog_service.sellable_cpu_slots(8, 16, nodes, gpu_node_vcpu_cap=16) == 0
