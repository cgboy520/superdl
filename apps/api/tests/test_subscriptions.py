"""包周期(预付)全链路。

每条用例要能答出「它挂了说明什么坏了」:

- 折扣算术:页面显示的原价/优惠/应付三行对不上实扣金额。
- 零重复扣款:同一个 Idempotency-Key 重放会二次扣一整个月的钱。
- 结算跳过:预付过的实例又被按小时扣了一遍(二次收费)。
- 三处配套过滤:包月用户余额为 0 时被欠费巡检误停机 / 开不出新机 / 关机时被出尾账。
- 库存预留:包月用户关机一晚,早上开不了机(容量被别人买走)。
- 到期链路:到期不停机(免费继续跑)或停机后永不回收(实例盘泄漏)。
"""

import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.core.policies import EffectivePolicies, get_effective_policies
from app.core.pricing import (
    MARKET_SUBSCRIPTION,
    PERIOD_HOURS,
    price_for,
    quote_subscription,
)
from app.core.timeutil import now_utc
from app.modules.billing import wallet
from app.modules.billing.models import BalanceLedger, BillHourly, Subscription, Wallet
from app.modules.billing.patrol import balance_patrol
from app.modules.billing.subscriptions import subscription_patrol
from app.modules.catalog.models import Sku
from app.modules.notify.models import Notification
from app.modules.orchestrator.models import Instance
from app.modules.orchestrator.reconciler import reconcile_once
from tests.helpers import (
    IMAGE_PYTORCH,
    buy_subscription,
    create_test_sku,
    create_user_with_key,
    drain,
    fund_wallet,
    get_instance,
    provision_running,
    provision_subscription,
    seed_node_spec,
)

pytestmark = pytest.mark.usefixtures("fake")

IMAGE = IMAGE_PYTORCH


class TestExpiringEndpoint:
    async def test_expiring_lists_only_horizon_hits_sorted(self, client, sm, fake):
        """到期横幅轻端点:只回临期(active 且 ≤ within_days)实例,升序,不分页;
        路由注册顺序守护:/instances/expiring 不被 /instances/{uuid} 吃掉。"""
        headers, uuid, _user_id, _sku, _key = await provision_subscription(
            client, sm, fake, "13910000101"
        )
        async with sm() as session:
            await session.execute(
                update(Subscription)
                .where(
                    Subscription.instance_id
                    == select(Instance.id).where(Instance.uuid == uuid).scalar_subquery()
                )
                .values(expires_at=now_utc() + timedelta(days=3))
            )
            await session.commit()
        resp = await client.get("/api/v1/instances/expiring?within_days=7", headers=headers)
        assert resp.status_code == 200, resp.text
        items = resp.json()
        assert isinstance(items, list)  # 列表而非 404/单对象:{uuid} 路由没吃掉 expiring
        assert [i["uuid"] for i in items] == [uuid]
        assert items[0]["subscription"]["status"] == "active"
        # 窗口收窄到 1 天:窗口外即空
        resp = await client.get("/api/v1/instances/expiring?within_days=1", headers=headers)
        assert resp.json() == []


async def _policies(sm) -> EffectivePolicies:
    async with sm() as session:
        return await get_effective_policies(session)


class TestQuoteArithmetic:
    """报价三件套:前端逐行渲染的那三个数必须与实扣金额自洽。"""

    def test_month_matches_hand_math(self):
        """¥3.99/时 × 720 时 × 8 折 = ¥2298.24 —— UI 稿上写死的那个数。

        挂了说明折扣口径变了(或周期小时数不再是 720),市场页与续费 modal 上
        用户看到的价格会和实扣对不上。
        """
        policies = EffectivePolicies(
            **{
                **{
                    f: 1
                    for f in EffectivePolicies.__dataclass_fields__
                    if f != "disk_price_gb_month"
                },
                "disk_price_gb_month": Decimal("0.01"),
                "period_discount_month": 80,
            }
        )
        q = quote_subscription(
            Decimal("3.9900"), gpu_count=1, period="month", period_count=1, policies=policies
        )
        assert q.hours == 720
        assert q.unit_price == Decimal("3.1920")
        assert q.list_amount == Decimal("2872.80")
        assert q.amount == Decimal("2298.24")
        assert q.discount_amount == Decimal("574.56")

    def test_three_amounts_are_self_consistent(self):
        """discount == list - amount 恒成立(前端直接减也得到同一个数)。"""
        policies = EffectivePolicies(
            **{
                **{
                    f: 3
                    for f in EffectivePolicies.__dataclass_fields__
                    if f != "disk_price_gb_month"
                },
                "disk_price_gb_month": Decimal("0.01"),
                "period_discount_year": 70,
            }
        )
        q = quote_subscription(
            Decimal("0.9999"), gpu_count=2, period="year", period_count=2, policies=policies
        )
        assert q.hours == PERIOD_HOURS["year"] * 2
        assert q.discount_amount == q.list_amount - q.amount
        # 应付 = 折后时价 × 份数 × 小时数,与前端能看到的口径逐字一致
        assert q.amount == (q.unit_price * 2 * q.hours).quantize(Decimal("0.01"))

    def test_cpu_instance_bills_one_unit(self):
        """CPU 实例 gpu_count=0 收 1 份(billing_units),不是 0 份 —— 收 0 份即整档免费。"""
        policies = EffectivePolicies(
            **{
                **{
                    f: 1
                    for f in EffectivePolicies.__dataclass_fields__
                    if f != "disk_price_gb_month"
                },
                "disk_price_gb_month": Decimal("0.01"),
                "period_discount_day": 100,
            }
        )
        q = quote_subscription(
            Decimal("0.5000"), gpu_count=0, period="day", period_count=1, policies=policies
        )
        assert q.amount == Decimal("12.00")


class TestOrderAndIdempotency:
    async def test_order_debits_once_and_snapshots_discounted_price(self, client, sm, fake):
        """下单即扣整段周期的钱,实例落的是**折后**时价。

        挂了说明预扣没走通(用户白拿一个月)或时价快照错(列表页价格失真)。
        """
        _headers, uuid, user_id, _, _ = await provision_subscription(
            client, sm, fake, "13911100001"
        )
        policies = await _policies(sm)
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
            sub = (
                await s.execute(select(Subscription).where(Subscription.instance_id == inst.id))
            ).scalar_one()
            ledger = (
                (
                    await s.execute(
                        select(BalanceLedger).where(
                            BalanceLedger.user_id == user_id,
                            BalanceLedger.ref_type == "subscription",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert inst.market == MARKET_SUBSCRIPTION
        assert inst.price_hourly == price_for(
            sub.unit_price, market=MARKET_SUBSCRIPTION, policies=policies, period="month"
        )
        assert len(ledger) == 1
        assert ledger[0].amount == -sub.amount_paid
        assert ledger[0].ref_id == str(sub.id)
        # 到期时刻与定价同源:720 小时,不是自然月
        assert sub.expires_at - sub.started_at == timedelta(hours=720)

    async def test_replayed_key_charges_once(self, client, sm, fake):
        """同一个 Idempotency-Key 重放:**零重复扣款**、零重复订阅行。

        挂了说明用户点两次「确认」会被扣两个月的钱 —— 计费面最贵的一类 bug。
        """
        headers, user_id, key_id = await create_user_with_key(client, "13911100002")
        await fund_wallet(sm, user_id, "5000.00")
        sku_id = await create_test_sku(sm)
        await seed_node_spec(sm, node_name="node-idem")

        code, first = await buy_subscription(client, headers, sku_id, key_id, idem="k-1")
        assert code == 202
        code2, second = await buy_subscription(client, headers, sku_id, key_id, idem="k-1")
        assert code2 == 200  # 重放
        assert second["uuid"] == first["uuid"]

        async with sm() as s:
            subs = (
                (await s.execute(select(Subscription).where(Subscription.user_id == user_id)))
                .scalars()
                .all()
            )
            balance = await wallet.get_balance(s, user_id)
        assert len(subs) == 1
        assert balance == Decimal("5000.00") - subs[0].amount_paid

    async def test_insufficient_balance_never_reaches_creating(self, client, sm, fake):
        """余额不够买一个月:直接 400,不留半台 creating 实例、不扣钱。"""
        headers, user_id, key_id = await create_user_with_key(client, "13911100003")
        await fund_wallet(sm, user_id, "10.00")
        sku_id = await create_test_sku(sm)
        await seed_node_spec(sm, node_name="node-poor")

        code, body = await buy_subscription(client, headers, sku_id, key_id)
        assert code == 400
        assert body["code"] == "INSUFFICIENT_BALANCE"
        async with sm() as s:
            count = len(
                (await s.execute(select(Instance).where(Instance.user_id == user_id)))
                .scalars()
                .all()
            )
            assert await wallet.get_balance(s, user_id) == Decimal("10.00")
        assert count == 0

    async def test_period_field_rejected_on_on_demand(self, client, sm):
        """按量单里带 period 一律 422:静默忽略会让用户以为自己买的是包月。"""
        headers, user_id, key_id = await create_user_with_key(client, "13911100004")
        await fund_wallet(sm, user_id)
        sku_id = await create_test_sku(sm)
        resp = await client.post(
            "/api/v1/instances",
            json={
                "sku_id": sku_id,
                "gpu_count": 1,
                "image_ref": IMAGE,
                "ssh_key_ids": [key_id],
                "period": "month",
            },
            headers=headers,
        )
        assert resp.status_code == 422

    async def test_sku_with_period_disabled_refuses_subscription(self, client, sm, fake):
        """运营关掉这条 SKU 的包周期后,直调接口也买不到(前端只是把 chips 置灰)。"""
        headers, user_id, key_id = await create_user_with_key(client, "13911100005")
        await fund_wallet(sm, user_id, "5000.00")
        sku_id = await create_test_sku(sm, gpu_cores_pct=45, vcpu=7)
        await seed_node_spec(sm, node_name="node-nop")
        async with sm() as s:
            await s.execute(update(Sku).where(Sku.id == sku_id).values(period_enabled=False))
            await s.commit()
        code, body = await buy_subscription(client, headers, sku_id, key_id)
        assert code == 400
        assert body["message_key"] == "orchestrator.periodNotEnabled"


class TestSettlementSkip:
    async def test_no_hourly_bill_for_subscription(self, client, sm, fake):
        """包周期实例跑一整段、再关机,都不产生任何 bills_hourly 行。

        挂了 = 二次收费:用户先付了一个月,又被按小时扣了一遍。
        """
        from app.core.timeutil import hour_floor
        from app.modules.billing.settlement import settle_due_hours
        from app.modules.orchestrator.models import InstanceEvent

        headers, uuid, _user_id, _, _ = await provision_subscription(
            client, sm, fake, "13911100010"
        )
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
            # 把进入 running 的事件回拨到上一个自然小时,让整点结算有窗口可算
            await s.execute(
                update(InstanceEvent)
                .where(InstanceEvent.instance_id == inst.id)
                .values(created_at=hour_floor(now_utc()) - timedelta(hours=2))
            )
            await s.commit()
            instance_id = inst.id
        await settle_due_hours(sm)
        # 关机的尾账同样不出
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        async with sm() as s:
            bills = (
                (await s.execute(select(BillHourly).where(BillHourly.instance_id == instance_id)))
                .scalars()
                .all()
            )
        assert bills == []


class TestCompanionFilters:
    async def test_zero_balance_subscriber_is_not_stopped(self, client, sm, fake):
        """余额清零的包月用户不被欠费巡检停机 —— 他已经付过钱了。

        挂了 = 包月用户在周期中途被平台停机,最难解释的一类事故。
        """
        _headers, uuid, user_id, _, _ = await provision_subscription(
            client, sm, fake, "13911100020"
        )
        async with sm() as s:
            w = await wallet.lock_wallet(s, user_id)
            w.balance = Decimal("0.00")
            await s.commit()
        await balance_patrol(sm)
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
        assert inst.status == "running"

    async def test_subscription_does_not_block_new_on_demand_instance(self, client, sm, fake):
        """包月实例不进在途燃烧率:否则把余额几乎买光的用户**开不出任何新机**。"""
        headers, _uuid, user_id, sku_id, key_id = await provision_subscription(
            client, sm, fake, "13911100021"
        )
        async with sm() as s:
            w = await wallet.lock_wallet(s, user_id)
            w.balance = Decimal("50.00")  # 够按量开一台,不够再买一个月
            await s.commit()
        resp = await client.post(
            "/api/v1/instances",
            json={
                "sku_id": sku_id,
                "gpu_count": 1,
                "image_ref": IMAGE,
                "ssh_key_ids": [key_id],
            },
            headers=headers,
        )
        assert resp.status_code == 202, resp.text

    async def test_stopped_subscription_is_not_frozen_by_arrears(self, client, sm, fake):
        """周期内关机 + 余额 0:不进欠费冻结链(冻结条件是「到期」不是「没钱」)。"""
        headers, uuid, user_id, _, _ = await provision_subscription(client, sm, fake, "13911100022")
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        async with sm() as s:
            w = await wallet.lock_wallet(s, user_id)
            w.balance = Decimal("0.00")
            await s.commit()
        await balance_patrol(sm)
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
        assert inst.status == "stopped"
        assert inst.frozen_deadline is None


class TestCapacityReservation:
    async def test_stopped_subscription_still_holds_capacity(self, client, sm, fake):
        """未到期的包周期实例即使已停机,库存仍算它占着。

        挂了 = 包月用户关机一晚,早上开不了机(卡被别人买走了)。
        """
        headers, uuid, _user_id, sku_id, _key_id = await provision_subscription(
            client, sm, fake, "13911100030", sku={"gpu_cores_pct": 100, "vcpu": 16, "mem_gb": 64}
        )
        # 池里只剩这一张卡的容量
        async with sm() as s:
            from app.modules.nodes.models import NodeSpec

            await s.execute(update(NodeSpec).values(gpu_count=1, gpu_used=0))
            await s.commit()
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)

        other_headers, other_uid, other_key = await create_user_with_key(client, "13911100031")
        await fund_wallet(sm, other_uid, "5000.00")
        resp = await client.post(
            "/api/v1/instances",
            json={
                "sku_id": sku_id,
                "gpu_count": 1,
                "image_ref": IMAGE,
                "ssh_key_ids": [other_key],
            },
            headers=other_headers,
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "NO_CAPACITY"
        # 市场页的可开数必须与软准入同源:一个说有货、一个 409,用户只能靠试错才知道真相
        market = (await client.get("/api/v1/skus")).json()
        assert next(s["available_count"] for s in market if s["id"] == sku_id) == 0


class TestRenewal:
    async def test_renew_extends_from_old_expiry_and_chains(self, client, sm, fake):
        """提前续费从**老到期时刻**起算,并串上 renewed_from_id。

        挂了 = 提前续费的用户白丢手上剩余的天数。
        """
        headers, uuid, user_id, _, _ = await provision_subscription(client, sm, fake, "13911100040")
        async with sm() as s:
            old = (
                await s.execute(select(Subscription).where(Subscription.user_id == user_id))
            ).scalar_one()
            old_expiry, old_id = old.expires_at, old.id

        resp = await client.post(
            f"/api/v1/instances/{uuid}/renew",
            json={"period": "month", "period_count": 2},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        quote = resp.json()["quote"]
        assert quote["period_count"] == 2
        assert quote["hours"] == 1440

        async with sm() as s:
            rows = (
                (
                    await s.execute(
                        select(Subscription)
                        .where(Subscription.user_id == user_id)
                        .order_by(Subscription.id)
                    )
                )
                .scalars()
                .all()
            )
        assert [r.status for r in rows] == ["expired", "active"]
        assert rows[1].renewed_from_id == old_id
        assert rows[1].started_at == old_expiry
        assert rows[1].expires_at == old_expiry + timedelta(hours=1440)

    async def test_renew_prices_from_original_snapshot_not_current_sku(self, client, sm, fake):
        """SKU 涨价不追已购用户:续费按 subscriptions.unit_price(下单时的原价快照)算。"""
        headers, uuid, user_id, sku_id, _ = await provision_subscription(
            client, sm, fake, "13911100041"
        )
        async with sm() as s:
            before = (
                await s.execute(select(Subscription).where(Subscription.user_id == user_id))
            ).scalar_one()
            paid_before = before.amount_paid
            await s.execute(
                update(Sku).where(Sku.id == sku_id).values(price_hourly=Decimal("99.0000"))
            )
            await s.commit()
        resp = await client.post(
            f"/api/v1/instances/{uuid}/renew",
            json={"period": "month", "period_count": 1},
            headers=headers,
        )
        assert resp.status_code == 200
        assert Decimal(resp.json()["quote"]["amount"]) == paid_before

    async def test_renew_is_idempotent(self, client, sm, fake):
        """续费带同一个 Idempotency-Key 重放:零重复扣款。"""
        headers, uuid, user_id, _, _ = await provision_subscription(client, sm, fake, "13911100042")
        body = {"period": "week", "period_count": 1}
        h = {**headers, "Idempotency-Key": "renew-1"}
        first = await client.post(f"/api/v1/instances/{uuid}/renew", json=body, headers=h)
        second = await client.post(f"/api/v1/instances/{uuid}/renew", json=body, headers=h)
        assert first.status_code == 200 and second.status_code == 200
        async with sm() as s:
            rows = (
                (await s.execute(select(Subscription).where(Subscription.user_id == user_id)))
                .scalars()
                .all()
            )
            consume = (
                (
                    await s.execute(
                        select(BalanceLedger).where(
                            BalanceLedger.user_id == user_id,
                            BalanceLedger.ref_type == "subscription",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 2  # 首单 + 一次续费
        assert len(consume) == 2

    async def test_renew_rejects_on_demand_instance(self, client, sm, fake):
        headers, uuid, _ = await provision_running(client, sm, fake, phone="13911100043")
        resp = await client.post(
            f"/api/v1/instances/{uuid}/renew",
            json={"period": "month", "period_count": 1},
            headers=headers,
        )
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "orchestrator.renewNotSubscription"


class TestExpiryChain:
    async def test_expire_stops_then_freezes_then_reclaims(self, client, sm, fake):
        """到期 → 停机 → 冻结 → 回收全链路。

        挂在第一步 = 到期后仍免费在跑;挂在后两步 = 实例盘永远收不回来。
        """
        _headers, uuid, user_id, _, _ = await provision_subscription(
            client, sm, fake, "13911100050"
        )
        async with sm() as s:
            await s.execute(
                update(Subscription)
                .where(Subscription.user_id == user_id)
                .values(expires_at=now_utc() - timedelta(minutes=1))
            )
            await s.commit()

        counts = await subscription_patrol(sm)
        assert counts["stopped"] == 1
        await drain(sm)
        await reconcile_once(sm)
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
        assert inst.status == "stopped"

        # 停稳后的下一轮把它送进 frozen 并起 72h 倒计时
        counts = await subscription_patrol(sm)
        assert counts["frozen"] == 1
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
            assert inst.status == "frozen"
            await s.execute(
                update(Instance)
                .where(Instance.id == inst.id)
                .values(frozen_deadline=now_utc() - timedelta(minutes=1))
            )
            await s.commit()

        # 回收仍由 balance_patrol 的既有分支做(状态机只有一处实现)
        await balance_patrol(sm)
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
        assert inst.status in ("releasing", "released")

    async def test_frozen_subscription_is_not_unfrozen_by_balance(self, client, sm, fake):
        """到期冻结的实例不会因为「余额充足」被解冻 —— 解冻条件是续费。

        挂了 = 余额充足的用户到期后被无限解冻,冻结倒计时永远走不到头(免费续期)。
        """
        _headers, uuid, user_id, _, _ = await provision_subscription(
            client, sm, fake, "13911100051"
        )
        async with sm() as s:
            await s.execute(
                update(Subscription)
                .where(Subscription.user_id == user_id)
                .values(expires_at=now_utc() - timedelta(minutes=1))
            )
            await s.commit()
        await subscription_patrol(sm)
        await drain(sm)
        await reconcile_once(sm)
        await subscription_patrol(sm)
        await balance_patrol(sm)  # 余额还有几千块
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
        assert inst.status == "frozen"

    async def test_expired_subscription_cannot_start(self, client, sm, fake):
        """到期后开机被拒(先续费):放行等于到期后免费续跑。"""
        headers, uuid, user_id, _, _ = await provision_subscription(client, sm, fake, "13911100052")
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        async with sm() as s:
            await s.execute(
                update(Subscription)
                .where(Subscription.user_id == user_id)
                .values(expires_at=now_utc() - timedelta(minutes=1))
            )
            await s.commit()
        resp = await client.post(f"/api/v1/instances/{uuid}/start", headers=headers)
        assert resp.status_code == 409
        assert resp.json()["code"] == "SUBSCRIPTION_EXPIRED"

    async def test_renew_unfreezes(self, client, sm, fake):
        """冻结中续费即解冻(回到 stopped 由用户自己开机)。"""
        headers, uuid, user_id, _, _ = await provision_subscription(client, sm, fake, "13911100053")
        async with sm() as s:
            await s.execute(
                update(Subscription)
                .where(Subscription.user_id == user_id)
                .values(expires_at=now_utc() - timedelta(minutes=1))
            )
            await s.commit()
        await subscription_patrol(sm)
        await drain(sm)
        await reconcile_once(sm)
        await subscription_patrol(sm)

        resp = await client.post(
            f"/api/v1/instances/{uuid}/renew",
            json={"period": "month", "period_count": 1},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["instance"]["status"] == "stopped"
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
        assert inst.frozen_deadline is None

    async def test_auto_renew_charges_and_extends(self, client, sm, fake):
        """自动续费:到期那一刻扣款续期,实例不停机。"""
        headers, uuid, user_id, _, _ = await provision_subscription(client, sm, fake, "13911100054")
        resp = await client.post(
            f"/api/v1/instances/{uuid}/auto-renew", json={"enabled": True}, headers=headers
        )
        assert resp.status_code == 200
        async with sm() as s:
            await s.execute(
                update(Subscription)
                .where(Subscription.user_id == user_id)
                .values(expires_at=now_utc() - timedelta(minutes=1))
            )
            await s.commit()
        counts = await subscription_patrol(sm)
        assert counts["renewed"] == 1
        assert counts["stopped"] == 0
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
            rows = (
                (await s.execute(select(Subscription).where(Subscription.user_id == user_id)))
                .scalars()
                .all()
            )
        assert inst.status == "running"
        assert len(rows) == 2
        assert rows[-1].auto_renew is True  # 续费链继承开关

    async def test_auto_renew_without_balance_falls_back_to_stop(self, client, sm, fake):
        """自动续费余额不足:不透支,走到期停机链路,且老订阅行不被改坏。"""
        headers, uuid, user_id, _, _ = await provision_subscription(client, sm, fake, "13911100055")
        await client.post(
            f"/api/v1/instances/{uuid}/auto-renew", json={"enabled": True}, headers=headers
        )
        async with sm() as s:
            w = await wallet.lock_wallet(s, user_id)
            w.balance = Decimal("1.00")
            await s.execute(
                update(Subscription)
                .where(Subscription.user_id == user_id)
                .values(expires_at=now_utc() - timedelta(minutes=1))
            )
            await s.commit()
        counts = await subscription_patrol(sm)
        assert counts["renew_failed"] == 1
        assert counts["stopped"] == 1
        async with sm() as s:
            rows = (
                (await s.execute(select(Subscription).where(Subscription.user_id == user_id)))
                .scalars()
                .all()
            )
            assert await wallet.get_balance(s, user_id) == Decimal("1.00")
        assert len(rows) == 1
        assert rows[0].status == "expired"

    async def test_expiring_warning_is_sent_once(self, client, sm, fake):
        """临期预警每个到期时刻只发一条(巡检 30 分钟一轮,不去重就是每半小时一条短信)。"""
        from app.modules.notify.models import Notification

        _headers, _uuid, user_id, _, _ = await provision_subscription(
            client, sm, fake, "13911100056"
        )
        async with sm() as s:
            await s.execute(
                update(Subscription)
                .where(Subscription.user_id == user_id)
                .values(expires_at=now_utc() + timedelta(hours=12))
            )
            await s.commit()
        assert (await subscription_patrol(sm))["warned"] == 1
        assert (await subscription_patrol(sm))["warned"] == 0
        async with sm() as s:
            notes = (
                (
                    await s.execute(
                        select(Notification).where(
                            Notification.user_id == user_id, Notification.type == "subscription"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(notes) == 1


class TestRenewConcurrency:
    """续费并发(手动 × 自动)的零重复扣款纪律。

    挂了 = 审计 P1-2 竞态回归:两条路径产出第二条 active 订阅行,
    或同一周期(同一 renewed_from_id)被续出两行、二次扣款。
    """

    async def test_concurrent_manual_and_auto_renew_never_double_charges(self, client, sm, fake):
        """无论谁先赢:一实例仅一行 active,同一周期不被续两次,余额与链上实扣自洽。"""
        headers, uuid, user_id, _, _ = await provision_subscription(client, sm, fake, "13911100096")
        async with sm() as s:
            await s.execute(
                update(Subscription)
                .where(Subscription.user_id == user_id)
                .values(auto_renew=True, expires_at=now_utc() - timedelta(minutes=1))
            )
            await s.commit()
            balance_before = await wallet.get_balance(s, user_id)

        async def manual() -> None:
            resp = await client.post(
                f"/api/v1/instances/{uuid}/renew",
                json={"period": "month", "period_count": 1},
                headers=headers,
            )
            assert resp.status_code == 200, resp.text

        await asyncio.gather(manual(), subscription_patrol(sm))

        async with sm() as s:
            rows = list(
                (await s.execute(select(Subscription).where(Subscription.user_id == user_id)))
                .scalars()
                .all()
            )
            active = [r for r in rows if r.status == "active"]
            assert len(active) == 1
            parents = [r.renewed_from_id for r in rows if r.renewed_from_id is not None]
            assert len(parents) == len(set(parents))  # 同一周期没有被续出两条
            charged = sum(r.amount_paid for r in rows if r.renewed_from_id is not None)
            assert await wallet.get_balance(s, user_id) == balance_before - charged

    async def test_auto_renew_after_manual_renew_is_noop(self, client, sm, fake):
        """手动续费已完成后再跑到期巡检:自动续费不重复扣款(等锁后复核命中)。

        挂了 = 巡检把已续过的周期再续一遍(老行 expired 仍被 UPDATE、插入第二条 active)。
        """
        headers, uuid, user_id, _, _ = await provision_subscription(client, sm, fake, "13911100097")
        async with sm() as s:
            await s.execute(
                update(Subscription)
                .where(Subscription.user_id == user_id)
                .values(auto_renew=True, expires_at=now_utc() - timedelta(minutes=1))
            )
            await s.commit()
        resp = await client.post(
            f"/api/v1/instances/{uuid}/renew",
            json={"period": "month", "period_count": 1},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        async with sm() as s:
            balance_after_manual = await wallet.get_balance(s, user_id)

        counts = await subscription_patrol(sm)
        assert counts["renewed"] == 0
        async with sm() as s:
            assert await wallet.get_balance(s, user_id) == balance_after_manual
            rows = list(
                (await s.execute(select(Subscription).where(Subscription.user_id == user_id)))
                .scalars()
                .all()
            )
            assert len([r for r in rows if r.status == "active"]) == 1


class TestRestartGate:
    """重启的资金门禁与开机同口径:包周期看订阅有效期,按量看余额。

    挂在第一例 = 已预付整周期的包月用户余额为 0(正常态)时重启被余额门禁拦下,
    付费资产须充值才能恢复;挂在第二例 = 已到期实例借重启绕过开机有效期门禁,
    重建 Pod 免费续跑;挂在第三例 = 按量余额不足分支被顺带改坏。
    """

    async def test_subscription_restart_ignores_balance(self, client, sm, fake):
        """包月实例余额为 0 重启照常:整周期已预付,余额门禁不该拦。"""
        headers, uuid, user_id, _, _ = await provision_subscription(client, sm, fake, "13911100090")
        async with sm() as s:
            await s.execute(update(Wallet).where(Wallet.user_id == user_id).values(balance=0))
            await s.commit()
        resp = await client.post(f"/api/v1/instances/{uuid}/restart", headers=headers)
        assert resp.status_code == 200, resp.text
        await drain(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "starting"
        fake.mark_ready(f"tenant-{user_id}", uuid)
        await reconcile_once(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "running"

    async def test_expired_subscription_restart_aborts_at_stopped(self, client, sm, fake):
        """到期包月实例重启:停在 stopped 并发通知,不能重建 Pod 绕过有效期门禁。"""
        headers, uuid, user_id, _, _ = await provision_subscription(client, sm, fake, "13911100091")
        async with sm() as s:
            await s.execute(
                update(Subscription)
                .where(Subscription.user_id == user_id)
                .values(expires_at=now_utc() - timedelta(minutes=1))
            )
            await s.commit()
        resp = await client.post(f"/api/v1/instances/{uuid}/restart", headers=headers)
        assert resp.status_code == 200, resp.text
        await drain(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "stopped"
        async with sm() as s:
            notice = (
                await s.execute(
                    select(Notification).where(
                        Notification.user_id == user_id,
                        Notification.dedup_key.like("restart_subscription_expired:%"),
                    )
                )
            ).scalar_one()
            assert "包周期已到期" in notice.title

    async def test_payg_restart_insufficient_balance_aborts_at_stopped(self, client, sm, fake):
        """按量实例余额不足:重启中止在 stopped 并发通知(原行为回归)。"""
        headers, uuid, user_id = await provision_running(client, sm, fake, "13900000150")
        async with sm() as s:
            await s.execute(update(Wallet).where(Wallet.user_id == user_id).values(balance=0))
            await s.commit()
        resp = await client.post(f"/api/v1/instances/{uuid}/restart", headers=headers)
        assert resp.status_code == 200, resp.text
        await drain(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "stopped"
        async with sm() as s:
            notice = (
                await s.execute(
                    select(Notification).where(
                        Notification.user_id == user_id,
                        Notification.dedup_key.like("restart_no_balance:%"),
                    )
                )
            ).scalar_one()
            assert "余额不足" in notice.title


class TestRelease:
    async def test_release_cancels_subscription_without_refund(self, client, sm, fake):
        """中途释放:订阅转 cancelled、**不退款**(预付语义),库存预留同时解除。"""
        headers, uuid, user_id, _, _ = await provision_subscription(client, sm, fake, "13911100060")
        async with sm() as s:
            balance_before = await wallet.get_balance(s, user_id)
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        resp = await client.delete(f"/api/v1/instances/{uuid}", headers=headers)
        assert resp.status_code == 200
        async with sm() as s:
            row = (
                await s.execute(select(Subscription).where(Subscription.user_id == user_id))
            ).scalar_one()
            assert await wallet.get_balance(s, user_id) == balance_before
        assert row.status == "cancelled"


class TestListView:
    async def test_list_inlines_subscription_summary(self, client, sm, fake):
        """列表页内联到期信息:一次批量查询,不逐行打接口(web.md 的硬规矩)。"""
        headers, uuid, _user_id, _, _ = await provision_subscription(
            client, sm, fake, "13911100070"
        )
        resp = await client.get("/api/v1/instances", headers=headers)
        assert resp.status_code == 200, resp.text
        item = next(i for i in resp.json()["items"] if i["uuid"] == uuid)
        assert item["market"] == "subscription"
        assert item["subscription"]["period"] == "month"
        assert item["subscription"]["status"] == "active"
        assert item["subscription"]["auto_renew"] is False


class TestReconcileAndReporting:
    async def test_prepaid_is_reconciled_and_counted_as_revenue(self, client, sm, fake):
        """预付这段钱必须被对账覆盖、也必须计进收入。

        对账挂了 = `ref_type='subscription'` 的扣款成了全无核对的一段钱(写错/写重/写漏
        都没有任何机制会发现);收入挂了 = 运营看到的「今日收入」把包周期全漏掉。
        """
        from app.modules.billing.reconcile import bills_vs_consume, dangling_consume_refs
        from app.modules.billing.wallet import revenue_summary

        _, _, user_id, _, _ = await provision_subscription(client, sm, fake, "13911100080")
        async with sm() as s:
            paid = (
                (await s.execute(select(Subscription).where(Subscription.user_id == user_id)))
                .scalar_one()
                .amount_paid
            )
            since = now_utc() - timedelta(days=1)
            billed, consumed = await bills_vs_consume(s, since, now_utc() + timedelta(minutes=1))
            dangling = await dangling_consume_refs(s)
            revenue = await revenue_summary(s)
        # 出账侧(subscriptions.amount_paid)与消费侧(ledger)逐分相等
        assert billed == consumed == paid
        assert dangling == 0
        assert Decimal(revenue["today_prepaid"]) == paid
        assert Decimal(revenue["month_revenue"]) >= paid

    async def test_overview_counts_active_subscriptions(self, client, sm, fake):
        """总览的「包周期在保数」按订阅行数,不按实例状态 —— 停机的包月实例仍在保。"""
        from app.modules.adminapi.service import overview

        headers, uuid, _, _, _ = await provision_subscription(client, sm, fake, "13911100081")
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        async with sm() as s:
            assert (await overview(s))["subscriptions_active"] == 1


class TestConvertToSubscription:
    """按量转包周期。要害只有一个:转换那一刀两侧的账,不重复也不留缝。"""

    async def _on_demand_running(self, client, sm, fake, phone: str):
        headers, uuid, user_id = await provision_running(client, sm, fake, phone=phone)
        await fund_wallet(sm, user_id, "5000.00")
        return headers, uuid, user_id

    async def test_pre_conversion_hours_are_settled_not_forgiven(self, client, sm, fake):
        """转换前那段按量时间必须先出账。

        挂了 = 用户开着按量机跑一整天,临睡前转包月,那一天的钱平台永远收不到
        —— `billing_candidates` 按**当前** market 挑候选,market 一翻就再也没人管那些小时了。
        """
        from app.core.timeutil import hour_floor
        from app.modules.orchestrator.models import InstanceEvent

        headers, uuid, _user_id = await self._on_demand_running(client, sm, fake, "13911100090")
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
            instance_id, unit = inst.id, inst.price_hourly
            # 已跑了本小时的前 30 分钟
            await s.execute(
                update(InstanceEvent)
                .where(InstanceEvent.instance_id == instance_id)
                .values(created_at=hour_floor(now_utc()))
            )
            await s.commit()

        resp = await client.post(
            f"/api/v1/instances/{uuid}/subscribe",
            json={"period": "month", "period_count": 1},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        async with sm() as s:
            bills = (
                (await s.execute(select(BillHourly).where(BillHourly.instance_id == instance_id)))
                .scalars()
                .all()
            )
            inst = (
                await s.execute(select(Instance).where(Instance.id == instance_id))
            ).scalar_one()
        # 转换前那一段有账单行,且用的是**转换前**的按量时价
        assert len(bills) == 1
        assert bills[0].unit_price == unit
        assert bills[0].detail["source"] == "convert"
        assert bills[0].seconds_used > 0
        # 转换后 market 翻了、时价换成折后价
        assert inst.market == "subscription"
        assert inst.price_hourly < unit

    async def test_after_conversion_settlement_skips_the_instance(self, client, sm, fake):
        """转换后小时结算不碰它:两段口径互斥,不能又收包月又收小时。"""
        from app.core.timeutil import hour_floor
        from app.modules.billing.settlement import settle_due_hours
        from app.modules.orchestrator.models import InstanceEvent

        headers, uuid, _user_id = await self._on_demand_running(client, sm, fake, "13911100091")
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
            instance_id = inst.id
            await s.execute(
                update(InstanceEvent)
                .where(InstanceEvent.instance_id == instance_id)
                .values(created_at=hour_floor(now_utc()) - timedelta(hours=2))
            )
            await s.commit()
        await client.post(
            f"/api/v1/instances/{uuid}/subscribe",
            json={"period": "month", "period_count": 1},
            headers=headers,
        )
        async with sm() as s:
            before = len(
                (await s.execute(select(BillHourly).where(BillHourly.instance_id == instance_id)))
                .scalars()
                .all()
            )
        await settle_due_hours(sm)
        async with sm() as s:
            after = len(
                (await s.execute(select(BillHourly).where(BillHourly.instance_id == instance_id)))
                .scalars()
                .all()
            )
        assert after == before

    async def test_convert_charges_once_under_replay(self, client, sm, fake):
        """同一个 Idempotency-Key 重放:零重复扣款、零重复订阅行。"""
        headers, uuid, user_id = await self._on_demand_running(client, sm, fake, "13911100092")
        h = {**headers, "Idempotency-Key": "conv-1"}
        body = {"period": "week", "period_count": 1}
        first = await client.post(f"/api/v1/instances/{uuid}/subscribe", json=body, headers=h)
        second = await client.post(f"/api/v1/instances/{uuid}/subscribe", json=body, headers=h)
        assert first.status_code == 200 and second.status_code == 200, second.text
        async with sm() as s:
            subs = (
                (await s.execute(select(Subscription).where(Subscription.user_id == user_id)))
                .scalars()
                .all()
            )
            entries = (
                (
                    await s.execute(
                        select(BalanceLedger).where(
                            BalanceLedger.user_id == user_id,
                            BalanceLedger.ref_type == "subscription",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(subs) == 1
        assert len(entries) == 1

    async def test_convert_twice_without_key_is_refused(self, client, sm, fake):
        """不带幂等键重复转:第二次拒掉,不开第二张单。"""
        headers, uuid, _user_id = await self._on_demand_running(client, sm, fake, "13911100093")
        body = {"period": "week", "period_count": 1}
        assert (
            await client.post(f"/api/v1/instances/{uuid}/subscribe", json=body, headers=headers)
        ).status_code == 200
        second = await client.post(
            f"/api/v1/instances/{uuid}/subscribe", json=body, headers=headers
        )
        assert second.status_code == 400
        assert second.json()["message_key"] == "orchestrator.convertNotOnDemand"

    async def test_convert_keeps_locked_in_price_not_current_sku_price(self, client, sm, fake):
        """转换按**建实例时**的价格快照报价:SKU 涨价不追已有实例。"""
        headers, uuid, _user_id = await self._on_demand_running(client, sm, fake, "13911100094")
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
            snapshot, sku_id = inst.price_hourly, inst.sku_id
            await s.execute(
                update(Sku).where(Sku.id == sku_id).values(price_hourly=Decimal("99.0000"))
            )
            await s.commit()
        resp = await client.post(
            f"/api/v1/instances/{uuid}/subscribe",
            json={"period": "month", "period_count": 1},
            headers=headers,
        )
        assert resp.status_code == 200
        assert Decimal(resp.json()["quote"]["base_hourly"]) == snapshot

    async def test_stopped_instance_converts_without_extra_bill(self, client, sm, fake):
        """已关机的实例转包周期:没有在跑的时间要结,不该凭空多出账单行。"""
        headers, uuid, _user_id = await self._on_demand_running(client, sm, fake, "13911100095")
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        async with sm() as s:
            instance_id = (
                (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one().id
            )
            before = len(
                (await s.execute(select(BillHourly).where(BillHourly.instance_id == instance_id)))
                .scalars()
                .all()
            )
        resp = await client.post(
            f"/api/v1/instances/{uuid}/subscribe",
            json={"period": "day", "period_count": 1},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        async with sm() as s:
            after = len(
                (await s.execute(select(BillHourly).where(BillHourly.instance_id == instance_id)))
                .scalars()
                .all()
            )
        assert after == before

    async def test_convert_refused_when_settlement_is_far_behind(self, client, sm, fake):
        """结算严重滞后时拒绝转换。

        挂了 = 结算出故障期间转包周期,会把那段真实消费永久免掉,而且没有任何痕迹。
        """
        from app.core.timeutil import hour_floor
        from app.modules.billing.models import SettlementWatermark

        headers, uuid, _user_id = await self._on_demand_running(client, sm, fake, "13911100096")
        async with sm() as s:
            s.add(
                SettlementWatermark(
                    key="hourly", settled_through=hour_floor(now_utc()) - timedelta(days=10)
                )
            )
            await s.commit()
        resp = await client.post(
            f"/api/v1/instances/{uuid}/subscribe",
            json={"period": "month", "period_count": 1},
            headers=headers,
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.settlementBehind"
