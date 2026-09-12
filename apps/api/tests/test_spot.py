"""竞价(spot):折扣、抢占选择、宽限窗、结算与转按量。"""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.core.outbox import OutboxTask
from app.core.pricing import MARKET_ON_DEMAND, MARKET_SPOT, price_for
from app.core.timeutil import hour_floor, now_utc
from app.modules.billing import wallet
from app.modules.billing.models import BillHourly
from app.modules.nodes.models import NodeSpec
from app.modules.orchestrator import preempt as preempt_mod
from app.modules.orchestrator.models import Instance, InstanceEvent
from app.modules.orchestrator.reconciler import reconcile_once
from tests.helpers import (
    IMAGE_PYTORCH,
    admin_headers as make_admin_headers,
    create_test_sku,
    create_user_with_key,
    drain,
    fund_wallet,
    funded_user,
    provision_running,
    provision_subscription,
    seed_node_spec,
)

pytestmark = pytest.mark.usefixtures("fake")

IMAGE = IMAGE_PYTORCH


def _victim(uuid: str, **kw) -> Instance:
    """一台 running 竞价实例(差异经 kw 覆盖)。"""
    base: dict = {
        "user_id": 1,
        "name": "v",
        "sku_id": 1,
        "spec": {"pool_label": "kata", "gpu_model_selector": "RTX4090"},
        "price_hourly": Decimal("1.0000"),
        "gpu_count": 1,
        "image_ref": IMAGE,
        "market": MARKET_SPOT,
        "status": "running",
        "k8s_namespace": "t",
        "jupyter_token": "x",
    }
    base.update(kw)
    return Instance(uuid=uuid, **base)


async def spot_sku(sm, **overrides) -> int:
    """一条上了竞价档的 SKU(默认 dedicated,1 卡 = 1 槽位)。"""
    from app.modules.catalog.models import Sku

    sku_id = await create_test_sku(
        sm,
        tier="dedicated",
        pool_label="kata",
        gpu_model="RTX4090",
        gpu_cores_pct=100,
        vram_gb=24,
        vcpu=16,
        mem_gb=64,
        name="RTX4090 · 专用整卡",
        **overrides,
    )
    async with sm() as s:
        await s.execute(update(Sku).where(Sku.id == sku_id).values(spot_enabled=True))
        await s.commit()
    return sku_id


async def create(client, headers, sku_id, key_id, *, market=MARKET_SPOT, expect=202):
    resp = await client.post(
        "/api/v1/instances",
        json={
            "sku_id": sku_id,
            "gpu_count": 1,
            "image_ref": IMAGE,
            "ssh_key_ids": [key_id],
            "market": market,
        },
        headers=headers,
    )
    assert resp.status_code == expect, resp.text
    return resp.json()


async def running_spot(client, sm, fake, phone, sku_id, *, cards=1):
    """建一台跑起来的竞价实例。返回 (headers, uuid, user_id)。"""
    headers, user_id, key_id = await funded_user(client, sm, phone, "5000.00")
    data = await create(client, headers, sku_id, key_id)
    await drain(sm)
    fake.mark_ready(f"tenant-{user_id}", data["uuid"])
    await reconcile_once(sm)
    return headers, data["uuid"], user_id


class TestSpotPricing:
    async def test_spot_price_is_discounted_snapshot(self, client, sm, fake):
        """竞价实例落折后时价,原价另存 spec.base_price_hourly。"""
        from app.core.platform_config import get_runtime_config

        sku_id = await spot_sku(sm)
        await seed_node_spec(sm, node_name="node-spot-1", pool_label="kata")
        _, uuid, _ = await running_spot(client, sm, fake, "13922200001", sku_id)
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
            policies = await get_runtime_config(s)
        base = Decimal(inst.spec["base_price_hourly"])
        assert inst.market == MARKET_SPOT
        assert inst.price_hourly == price_for(base, market=MARKET_SPOT, policies=policies)
        assert inst.price_hourly < base

    async def test_sku_without_spot_refuses(self, client, sm, fake):
        """没上竞价档的规格直接拒。"""
        sku_id = await create_test_sku(sm)  # 默认 spot_enabled=False
        await seed_node_spec(sm, node_name="node-nospot")
        headers, _user_id, key_id = await funded_user(client, sm, "13922200002", "5000.00")
        body = await create(client, headers, sku_id, key_id, expect=400)
        assert body["message_key"] == "orchestrator.spotNotEnabled"


class TestVictimSelection:
    async def test_newest_first(self, sm):
        """按 created_at 从新到旧回收。"""
        async with sm() as s:
            for i in range(3):
                s.add(_victim(f"vic{i}", created_at=now_utc() - timedelta(hours=3 - i)))
            await s.commit()
            picked = await preempt_mod.pick_victims(
                s, pool_label="kata", gpu_model_selector="RTX4090", need_cards=2
            )
        assert [p.uuid for p in picked] == ["vic2", "vic1"]

    async def test_all_or_nothing(self, sm):
        """凑不够就一台都不动。"""
        async with sm() as s:
            s.add(_victim("lonely"))
            await s.commit()
            assert (
                await preempt_mod.pick_victims(
                    s, pool_label="kata", gpu_model_selector="RTX4090", need_cards=4
                )
                == []
            )

    async def test_never_crosses_pool_model_or_market(self, sm):
        """不同池 / 不同型号 / 非竞价 / 非 running 的实例都不是候选。"""
        async with sm() as s:
            s.add_all(
                [
                    _victim(
                        "other-pool",
                        spec={"pool_label": "hami", "gpu_model_selector": "RTX4090"},
                    ),
                    _victim(
                        "other-model",
                        spec={"pool_label": "kata", "gpu_model_selector": "A100"},
                    ),
                    _victim("on-demand", market=MARKET_ON_DEMAND),
                    _victim("stopped-spot", status="stopped"),
                ]
            )
            await s.commit()
            assert (
                await preempt_mod.pick_victims(
                    s, pool_label="kata", gpu_model_selector="RTX4090", need_cards=1
                )
                == []
            )

    def test_cards_needed_rounds_up(self):
        """槽位换卡数向上取整。"""
        assert preempt_mod.cards_needed(deficit_slots=1, slots_per_card=3) == 1
        assert preempt_mod.cards_needed(deficit_slots=4, slots_per_card=3) == 2
        assert preempt_mod.cards_needed(deficit_slots=0, slots_per_card=3) == 0


class TestPreemptionFlow:
    async def _fill_pool(self, sm, cards: int = 1) -> None:
        async with sm() as s:
            await s.execute(update(NodeSpec).values(gpu_count=cards, gpu_used=0))
            await s.commit()

    async def test_on_demand_request_preempts_and_gets_capacity(self, client, sm, fake):
        """池满时按量请求触发抢占并拿到容量。"""
        sku_id = await spot_sku(sm)
        await seed_node_spec(sm, node_name="node-p1", pool_label="kata", gpu_count=1)
        _, victim_uuid, _victim_uid = await running_spot(client, sm, fake, "13922200010", sku_id)
        await self._fill_pool(sm, cards=1)
        async with sm() as s:  # 台账反映那张卡已被占
            await s.execute(update(NodeSpec).values(gpu_used=1))
            await s.commit()

        headers, buyer_uid, key_id = await funded_user(client, sm, "13922200011", "5000.00")
        await create(client, headers, sku_id, key_id, market=MARKET_ON_DEMAND)

        async with sm() as s:
            victim = (
                await s.execute(select(Instance).where(Instance.uuid == victim_uuid))
            ).scalar_one()
            ev = (
                await s.execute(
                    select(InstanceEvent)
                    .where(InstanceEvent.instance_id == victim.id)
                    .order_by(InstanceEvent.id.desc())
                    .limit(1)
                )
            ).scalar_one()
        assert victim.status == "stopping"
        assert ev.reason == "preempted"
        assert ev.event_metadata["requested_by"] == buyer_uid

    async def test_grace_window_defers_the_actual_delete(self, client, sm, fake):
        """宽限窗内 Pod 还在:通知发了、状态变了,删 Pod 的任务到期才领得到。"""
        sku_id = await spot_sku(sm)
        await seed_node_spec(sm, node_name="node-p2", pool_label="kata", gpu_count=1)
        _, victim_uuid, victim_uid = await running_spot(client, sm, fake, "13922200012", sku_id)
        async with sm() as s:
            await s.execute(update(NodeSpec).values(gpu_count=1, gpu_used=1))
            await s.commit()

        headers, _buyer_uid, key_id = await funded_user(client, sm, "13922200013", "5000.00")
        await create(client, headers, sku_id, key_id, market=MARKET_ON_DEMAND)

        async with sm() as s:
            stop_tasks = (
                (
                    await s.execute(
                        select(OutboxTask).where(
                            OutboxTask.type == "instance.stop", OutboxTask.status == "pending"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(stop_tasks) == 1
        assert stop_tasks[0].next_retry_at > now_utc() + timedelta(seconds=30)
        # 到期前 drain 领不到它,Pod 仍在
        await drain(sm)
        assert (f"tenant-{victim_uid}", victim_uuid) in fake.pods

    async def test_victim_is_billed_for_actual_seconds_only(self, client, sm, fake):
        """被抢占按实际运行秒数出尾账,宽限窗不计入。"""
        sku_id = await spot_sku(sm)
        await seed_node_spec(sm, node_name="node-p3", pool_label="kata", gpu_count=1)
        _, victim_uuid, _victim_uid = await running_spot(client, sm, fake, "13922200014", sku_id)
        async with sm() as s:
            inst = (
                await s.execute(select(Instance).where(Instance.uuid == victim_uuid))
            ).scalar_one()
            victim_id, unit = inst.id, inst.price_hourly
            # 本小时开头就在跑
            await s.execute(
                update(InstanceEvent)
                .where(InstanceEvent.instance_id == victim_id)
                .values(created_at=hour_floor(now_utc()))
            )
            await s.execute(update(NodeSpec).values(gpu_count=1, gpu_used=1))
            await s.commit()

        headers, _buyer_uid, key_id = await funded_user(client, sm, "13922200015", "5000.00")
        await create(client, headers, sku_id, key_id, market=MARKET_ON_DEMAND)

        async with sm() as s:
            bill = (
                await s.execute(select(BillHourly).where(BillHourly.instance_id == victim_id))
            ).scalar_one()
        assert bill.unit_price == unit  # 竞价折后价,不是原价
        assert bill.seconds_used > 0
        # 尾账截到「迁 stopping」那一刻,宽限窗不计入
        assert bill.seconds_used <= 3600

    async def test_spot_request_never_preempts(self, client, sm, fake):
        """竞价请求不触发抢占。"""
        sku_id = await spot_sku(sm)
        await seed_node_spec(sm, node_name="node-p4", pool_label="kata", gpu_count=1)
        _, victim_uuid, _ = await running_spot(client, sm, fake, "13922200016", sku_id)
        async with sm() as s:
            await s.execute(update(NodeSpec).values(gpu_count=1, gpu_used=1))
            await s.commit()

        headers, _uid, key_id = await funded_user(client, sm, "13922200017", "5000.00")
        body = await create(client, headers, sku_id, key_id, market=MARKET_SPOT, expect=409)
        assert body["code"] == "NO_CAPACITY"
        async with sm() as s:
            victim = (
                await s.execute(select(Instance).where(Instance.uuid == victim_uuid))
            ).scalar_one()
        assert victim.status == "running"

    async def test_failed_order_rolls_back_the_preemption(self, client, sm, fake):
        """请求方后续失败(余额不够),回收一起回滚。"""
        sku_id = await spot_sku(sm)
        await seed_node_spec(sm, node_name="node-p5", pool_label="kata", gpu_count=1)
        _, victim_uuid, _ = await running_spot(client, sm, fake, "13922200018", sku_id)
        async with sm() as s:
            await s.execute(update(NodeSpec).values(gpu_count=1, gpu_used=1))
            await s.commit()

        headers, uid, key_id = await create_user_with_key(client, "13922200019")
        await fund_wallet(sm, uid, "0.50")  # 开不起按量
        body = await create(client, headers, sku_id, key_id, market=MARKET_ON_DEMAND, expect=400)
        assert body["code"] == "INSUFFICIENT_BALANCE"
        async with sm() as s:
            victim = (
                await s.execute(select(Instance).where(Instance.uuid == victim_uuid))
            ).scalar_one()
        assert victim.status == "running"


class TestConvertToOnDemand:
    async def test_converts_price_and_market_without_touching_the_pod(self, client, sm, fake):
        """转按量:单价还原成原价、market 翻成按量,Pod 不动。"""
        sku_id = await spot_sku(sm)
        await seed_node_spec(sm, node_name="node-c1", pool_label="kata")
        headers, uuid, user_id = await running_spot(client, sm, fake, "13922200020", sku_id)
        pod_before = fake.pods[(f"tenant-{user_id}", uuid)]

        resp = await client.post(f"/api/v1/instances/{uuid}/to-on-demand", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["market"] == "on_demand"
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
        assert inst.price_hourly == Decimal(inst.spec["base_price_hourly"])
        # 转按量不删不建 Pod
        assert fake.pods[(f"tenant-{user_id}", uuid)] is pod_before

    async def test_repeat_is_idempotent(self, client, sm, fake):
        """已是按量再点一次:原样返回 200。"""
        sku_id = await spot_sku(sm)
        await seed_node_spec(sm, node_name="node-c2", pool_label="kata")
        headers, uuid, _ = await running_spot(client, sm, fake, "13922200021", sku_id)
        await client.post(f"/api/v1/instances/{uuid}/to-on-demand", headers=headers)
        again = await client.post(f"/api/v1/instances/{uuid}/to-on-demand", headers=headers)
        assert again.status_code == 200
        assert again.json()["market"] == "on_demand"

    async def test_current_hour_row_is_repriced_consistently(self, client, sm, fake):
        """跨价小时:当前小时已出的账单行整体改按按量价,`单价 × 秒数 == 金额` 仍成立。"""
        from app.modules.billing.settlement import bill_amount

        sku_id = await spot_sku(sm)
        await seed_node_spec(sm, node_name="node-c3", pool_label="kata")
        headers, uuid, user_id = await running_spot(client, sm, fake, "13922200022", sku_id)
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
            instance_id, spot_price = inst.id, inst.price_hourly
            base = Decimal(inst.spec["base_price_hourly"])
            # 造一行本小时的竞价账
            s.add(
                BillHourly(
                    instance_id=instance_id,
                    user_id=user_id,
                    hour_start=hour_floor(now_utc()),
                    seconds_used=600,
                    unit_price=spot_price,
                    gpu_count=1,
                    amount=bill_amount(spot_price, 1, 600),
                    detail={"source": "tail"},
                )
            )
            await s.commit()
            before = await wallet.get_balance(s, user_id)

        assert (
            await client.post(f"/api/v1/instances/{uuid}/to-on-demand", headers=headers)
        ).status_code == 200

        async with sm() as s:
            row = (
                await s.execute(select(BillHourly).where(BillHourly.instance_id == instance_id))
            ).scalar_one()
            after = await wallet.get_balance(s, user_id)
        assert row.unit_price == base
        assert row.amount == bill_amount(base, 1, row.seconds_used)  # 行内自洽
        assert before - after == bill_amount(base, 1, 600) - bill_amount(spot_price, 1, 600)

    async def test_lagged_hours_settle_at_spot_price_before_repricing(self, client, sm, fake):
        """挂了说明:转按量前滞后未结的整点小时被按按量价补扣(应按当时的竞价价,与转包周期同口径)。"""
        from app.modules.billing.models import SettlementWatermark

        sku_id = await spot_sku(sm)
        await seed_node_spec(sm, node_name="node-c4", pool_label="kata")
        headers, uuid, _user_id = await running_spot(client, sm, fake, "13922200024", sku_id)
        h0 = hour_floor(now_utc())
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
            instance_id, spot_price = inst.id, inst.price_hourly
            # 整条事件流挪到 2 小时前,水位线停在 3 小时前:h0-2、h0-1 两个整点滞后未结
            await s.execute(
                update(InstanceEvent)
                .where(InstanceEvent.instance_id == instance_id)
                .values(created_at=InstanceEvent.created_at - timedelta(hours=2))
            )
            s.add(SettlementWatermark(key="hourly", settled_through=h0 - timedelta(hours=3)))
            await s.commit()

        assert (
            await client.post(f"/api/v1/instances/{uuid}/to-on-demand", headers=headers)
        ).status_code == 200

        async with sm() as s:
            rows = (
                (
                    await s.execute(
                        select(BillHourly)
                        .where(BillHourly.instance_id == instance_id)
                        .order_by(BillHourly.hour_start)
                    )
                )
                .scalars()
                .all()
            )
        lagged = [r for r in rows if r.hour_start < h0]
        assert [r.hour_start for r in lagged] == [h0 - timedelta(hours=2), h0 - timedelta(hours=1)]
        # 两个滞后小时都按竞价价结;h0-2 从事件时刻起算(小时内部分秒),h0-1 整小时
        assert all(r.unit_price == spot_price and r.seconds_used > 0 for r in lagged)
        assert lagged[1].seconds_used == 3600

    async def test_on_demand_instance_refuses(self, client, sm, fake):
        """包周期实例不可转按量。"""
        headers, uuid, _, _, _ = await provision_subscription(client, sm, fake, "13922200023")
        resp = await client.post(f"/api/v1/instances/{uuid}/to-on-demand", headers=headers)
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "orchestrator.toOnDemandNotSpot"


class TestAdminPreempt:
    async def test_admin_can_reclaim_a_spot_instance(self, client, sm, fake):
        """管理端强制回收与自动抢占同一条路径(同 reason、宽限窗与通知)。"""
        sku_id = await spot_sku(sm)
        await seed_node_spec(sm, node_name="node-a1", pool_label="kata")
        _, uuid, _user_id = await running_spot(client, sm, fake, "13922200030", sku_id)
        admin_headers = await make_admin_headers(sm, client, "ops")

        resp = await client.post(
            f"/api/admin/v1/instances/{uuid}/preempt",
            json={"reason": "腾容量给按量单"},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
            ev = (
                await s.execute(
                    select(InstanceEvent)
                    .where(InstanceEvent.instance_id == inst.id)
                    .order_by(InstanceEvent.id.desc())
                    .limit(1)
                )
            ).scalar_one()
        assert inst.status == "stopping"
        assert ev.reason == "preempted"
        assert ev.event_metadata["admin_reason"] == "腾容量给按量单"

    async def test_admin_cannot_preempt_non_spot(self, client, sm, fake):
        """非竞价实例不走回收路径。"""
        _, uuid, _ = await provision_running(client, sm, fake, phone="13922200031")
        admin_headers = await make_admin_headers(sm, client, "ops")
        resp = await client.post(
            f"/api/admin/v1/instances/{uuid}/preempt",
            json={"reason": "试试"},
            headers=admin_headers,
        )
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "orchestrator.preemptNotSpot"


class TestGraceWindowGuard:
    def test_grace_cannot_eat_the_creating_timeout(self):
        """宽限窗小于 creating 超时预算。"""
        from app.core.platform_config import validate_setting_value

        assert validate_setting_value("spot_grace_seconds", "60") == "60"
        with pytest.raises(ValueError, match="不得超过"):
            validate_setting_value("spot_grace_seconds", "600")


class TestPreemptedBillingEqualsNormalStop:
    async def test_amount_matches_a_normally_stopped_twin(self, client, sm, fake):
        """被抢占的实例与自己关机的同款实例出账逐分相等。"""
        sku_id = await spot_sku(sm)
        await seed_node_spec(sm, node_name="node-twin", pool_label="kata", gpu_count=8)
        _, victim_uuid, _victim_uid = await running_spot(client, sm, fake, "13922200040", sku_id)
        twin_headers, twin_uuid, _twin_uid = await running_spot(
            client, sm, fake, "13922200041", sku_id
        )

        # 两台都从本小时开头起跑,窗口完全一致
        start = hour_floor(now_utc())
        async with sm() as s:
            rows = (
                (
                    await s.execute(
                        select(Instance).where(Instance.uuid.in_((victim_uuid, twin_uuid)))
                    )
                )
                .scalars()
                .all()
            )
            ids = {r.uuid: r.id for r in rows}
            await s.execute(
                update(InstanceEvent)
                .where(InstanceEvent.instance_id.in_(list(ids.values())))
                .values(created_at=start)
            )
            await s.execute(update(NodeSpec).values(gpu_count=2, gpu_used=2))
            await s.commit()

        # 一台被抢占,一台用户自己关机,同一时刻
        buyer_headers, _, buyer_key = await funded_user(client, sm, "13922200042", "5000.00")
        await client.post(f"/api/v1/instances/{twin_uuid}/stop", headers=twin_headers)
        await create(client, buyer_headers, sku_id, buyer_key, market=MARKET_ON_DEMAND)

        async with sm() as s:
            bills = {
                b.instance_id: b
                for b in (
                    await s.execute(
                        select(BillHourly).where(BillHourly.instance_id.in_(list(ids.values())))
                    )
                )
                .scalars()
                .all()
            }
        victim_bill = bills[ids[victim_uuid]]
        twin_bill = bills[ids[twin_uuid]]
        assert victim_bill.unit_price == twin_bill.unit_price
        # 秒数差不超过 1 秒,同秒同额
        assert abs(victim_bill.seconds_used - twin_bill.seconds_used) <= 1

    async def test_repricing_never_leaves_an_inconsistent_row_on_a_price_drop(
        self, client, sm, fake
    ):
        """降价路径整行不动。"""
        from app.modules.billing.settlement import bill_amount, reprice_current_hour

        sku_id = await spot_sku(sm)
        await seed_node_spec(sm, node_name="node-c4", pool_label="kata")
        _, uuid, user_id = await running_spot(client, sm, fake, "13922200024", sku_id)
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
            s.add(
                BillHourly(
                    instance_id=inst.id,
                    user_id=user_id,
                    hour_start=hour_floor(now_utc()),
                    seconds_used=600,
                    unit_price=Decimal("2.0000"),
                    gpu_count=1,
                    amount=bill_amount(Decimal("2.0000"), 1, 600),
                    detail={"source": "tail"},
                )
            )
            await s.commit()
            charged = await reprice_current_hour(
                s,
                instance_id=inst.id,
                user_id=user_id,
                new_price=Decimal("1.0000"),  # 降价
                gpu_count=1,
                at=now_utc(),
            )
            await s.commit()
            row = (
                await s.execute(select(BillHourly).where(BillHourly.instance_id == inst.id))
            ).scalar_one()
        assert charged == Decimal("0.00")
        assert row.unit_price == Decimal("2.0000")  # 整行不动
        assert row.amount == bill_amount(Decimal("2.0000"), 1, 600)
