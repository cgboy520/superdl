"""计费/钱包硬化批次:燃烧率开户校验、巡检实时估算停机、结算缺口、增量核对、营收归属。

每条用例对应一份审计论断的修复验收。
"""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update

from app.core.errors import AppError, ErrorCode
from app.core.timeutil import billing_day_floor, day_floor, hour_floor, now_utc
from app.modules.billing import patrol, settlement, wallet
from app.modules.billing.models import (
    BalanceLedger,
    BillDailyDisk,
    BillHourly,
    ReconcileCheckpoint,
    SettlementGap,
    Wallet,
)
from app.modules.billing.reconcile import reconcile_funds
from app.modules.billing.settlement import (
    DEAD_LETTER_AFTER,
    get_watermark,
    settle_daily_disks,
    settle_due_hours,
)
from app.modules.orchestrator.models import DataDisk
from tests.test_billing_settlement import H_END, H, seed_instance


@pytest.fixture(autouse=True)
def _clear_failure_streaks():
    """死信连败计数是进程内存,用例间必须互不影响。"""
    settlement._failure_streaks.clear()
    yield
    settlement._failure_streaks.clear()


async def _fund(sm, user_id: int, amount: str) -> None:
    async with sm() as session:
        await wallet.credit(session, user_id, Decimal(amount), type_="recharge", remark="seed")
        await session.commit()


class TestWalletLockGuards:
    """钱路行锁变异守护:实测去掉 with_for_update 后 876 条测试只有 1 条会红,
    这三条是必须存在的最低守护集(它们挂了 = 行锁被改回去了/锁内读到旧值)。"""

    async def test_concurrent_credit_debit_no_lost_update(self, sm):
        """同一钱包并发 credit/debit:无丢失更新,且 balance_after 链单调接续。"""
        await _fund(sm, 1, "100.00")
        gate = asyncio.Barrier(9)  # 4 credit + 4 debit + 主控,对齐起跑线

        async def do_credit():
            await gate.wait()
            async with sm() as s:
                await wallet.credit(s, 1, Decimal("10.00"), type_="recharge")
                await s.commit()

        async def do_debit():
            await gate.wait()
            async with sm() as s:
                await wallet.debit(s, 1, Decimal("3.00"), allow_negative=True)
                await s.commit()

        await asyncio.gather(
            do_credit(),
            do_credit(),
            do_credit(),
            do_credit(),
            do_debit(),
            do_debit(),
            do_debit(),
            do_debit(),
            gate.wait(),
        )
        async with sm() as s:
            w = (await s.execute(select(Wallet).where(Wallet.user_id == 1))).scalar_one()
            entries = (
                (await s.execute(select(BalanceLedger).order_by(BalanceLedger.id))).scalars().all()
            )
        assert w.balance == Decimal("128.00")  # 100 + 4×10 − 4×3,一分不差
        # 流水链:每行 balance_after = 上行 balance_after + 本行 amount;
        # 首行是种子充值(0→100),链尾即当前余额
        expected = Decimal("0.00")
        for e in entries:
            assert e.balance_after == expected + e.amount
            expected = e.balance_after
        assert expected == w.balance

    async def test_payout_balance_recheck_reads_fresh_row(self, sm, client):
        """打款锁内余额复检必须读到行锁后的新值:同会话早前装进 identity map 的旧
        Wallet 副本不得让复检看见消费前余额(看见即误判放行,把钱包打成负的)。
        注意:identity map 持弱引用,必须持强引用才能留住陈旧副本(get_balance
        返回即被 GC,复现不了)。"""
        from app.modules.adminapi.models import AdminUser
        from app.modules.billing import refunds
        from app.modules.billing.models import Order
        from tests.test_payment import user_headers
        from tests.test_refunds import apply_refund, finance_pair, paid_order

        headers = await user_headers(client, "13700000116")
        order = await paid_order(client, headers, "50.00")
        rid = (await apply_refund(client, headers, order["order_no"], "40.00")).json()["id"]
        reviewer, _payer_headers = await finance_pair(sm, client)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "同意"},
            headers=reviewer,
        )
        assert resp.status_code == 200, resp.text
        async with sm() as s:
            uid = (
                await s.execute(select(Order.user_id).where(Order.order_no == order["order_no"]))
            ).scalar_one()
            payer_id = (
                await s.execute(select(AdminUser.id).where(AdminUser.username == "finance-payer"))
            ).scalar_one()
        # 持强引用把陈旧副本(50)留在 identity map,再由另一事务消费 30(行真值变 20):
        # lock_wallet 若不重读行值,复检看到的仍是 50 → 误判放行
        async with sm() as session:
            _stale_wallet = (
                await session.execute(select(Wallet).where(Wallet.user_id == uid))
            ).scalar_one()
            async with sm() as s2:
                await wallet.debit(s2, uid, Decimal("30.00"), allow_negative=True)
                await s2.commit()  # 行真值 20 < 应退 40
            with pytest.raises(AppError) as exc:
                await refunds.payout_refund(
                    session, rid, channel="offline", ref="OFF-TOCTOU", operator_id=payer_id
                )
            assert exc.value.http_status == 409
        async with sm() as s:
            w = (await s.execute(select(Wallet).where(Wallet.user_id == uid))).scalar_one()
        assert w.balance == Decimal("20.00")  # 未出金,未被写成 10(50−40 的错觉)


async def _seed_disk(
    sm,
    user_id: int,
    *,
    size_gb: int = 100,
    price: str = "0.3500",
    status: str = "active",
    created_at: datetime | None = None,
) -> int:
    async with sm() as session:
        disk = DataDisk(
            uuid=f"d{user_id}{now_utc().timestamp()}".replace(".", ""),
            user_id=user_id,
            name="t",
            size_gb=size_gb,
            juicefs_subpath=f"disk-{user_id}-{now_utc().timestamp()}".replace(".", ""),
            price_gb_month=Decimal(price),
            status=status,
            created_at=created_at or now_utc(),
        )
        session.add(disk)
        await session.commit()
        return disk.id


class TestAffordGuard:
    """assert_can_afford 燃烧率感知校验:挡住「¥1.68 串行开 8 台」。"""

    async def test_first_instance_passes_with_one_hour_cover(self, sm):
        """无在途资源:余额 ≥ 新增 1 小时费即放行(门槛适度,不要求预存巨款)。"""
        await _fund(sm, 1, "1.68")
        async with sm() as session:
            await wallet.assert_can_afford(session, 1, additional_hourly=Decimal("1.68"))

    async def test_serial_create_blocked_by_inflight_burn(self, sm):
        """已在跑一台 ¥1.68/时:同样的余额再开第二台必须被拒,并报明在途消耗。"""
        await seed_instance(sm, user_id=1, price="1.6800", status="running")
        async with sm() as session:
            await session.execute(
                update(Wallet).where(Wallet.user_id == 1).values(balance=Decimal("1.68"))
            )
            await session.commit()
        async with sm() as session:
            with pytest.raises(AppError) as exc:
                await wallet.assert_can_afford(session, 1, additional_hourly=Decimal("1.68"))
        assert exc.value.code is ErrorCode.INSUFFICIENT_BALANCE
        assert exc.value.message_key == "billing.insufficientForInFlight"
        # 文案参数必须写清「在途资源预计消耗」
        assert exc.value.params == {
            "balance": "1.68",
            "required": "3.36",
            "inflight": "1.68",
        }
        assert "在途资源" in exc.value.message

    async def test_inflight_disk_daily_fee_counted(self, sm):
        """在途数据盘按「日费 × 宽限天数」计入门槛(宽限期内盘仍在计费)。"""
        # 100GB × 0.35/GB·月 → 均摊日费 1.17;× 默认 7 天宽限 = 8.19
        await _seed_disk(sm, 1, size_gb=100, price="0.3500")
        await _fund(sm, 1, "5.00")
        async with sm() as session:
            with pytest.raises(AppError) as exc:
                await wallet.assert_can_afford(session, 1)
        assert exc.value.params is not None
        assert exc.value.params["inflight"] == "8.19"
        async with sm() as session:  # 余额盖过宽限期消耗 → 放行
            await session.execute(
                update(Wallet).where(Wallet.user_id == 1).values(balance=Decimal("8.19"))
            )
            await session.commit()
        async with sm() as session:
            await wallet.assert_can_afford(session, 1)

    async def test_additional_disk_needs_grace_days_cover(self, sm):
        """新建数据盘:余额 ≥ 新增日费 × 宽限天数。"""
        await _fund(sm, 1, "0.70")
        async with sm() as session:
            await wallet.assert_can_afford(session, 1, additional_daily_disk=Decimal("0.10"))
        async with sm() as session:
            await session.execute(
                update(Wallet).where(Wallet.user_id == 1).values(balance=Decimal("0.69"))
            )
            await session.commit()
        async with sm() as session:
            with pytest.raises(AppError):
                await wallet.assert_can_afford(session, 1, additional_daily_disk=Decimal("0.10"))

    async def test_frozen_disk_not_counted(self, sm):
        """frozen 盘不计费(见 disks.BILLABLE_STATUSES),不应占燃烧率额度。"""
        await _seed_disk(sm, 1, status="frozen")
        await _fund(sm, 1, "0.01")
        async with sm() as session:
            await wallet.assert_can_afford(session, 1)

    async def test_cover_hours_policy_tunable(self, sm):
        """门槛常量走 policies 在线可调:afford_cover_hours=2 时按 2 小时要求。"""
        from app.core.policies import PolicyOverride

        await seed_instance(sm, user_id=1, price="1.6800", status="running")
        async with sm() as session:
            session.add(PolicyOverride(key="afford_cover_hours", value="2"))
            await session.execute(
                update(Wallet).where(Wallet.user_id == 1).values(balance=Decimal("3.36"))
            )
            await session.commit()
        async with sm() as session:  # 在途 1.68 × 2h = 3.36,刚好覆盖
            await wallet.assert_can_afford(session, 1)
        async with sm() as session:
            with pytest.raises(AppError):  # 再加 1 台 → 需 6.72
                await wallet.assert_can_afford(session, 1, additional_hourly=Decimal("1.68"))


class TestPatrolUnsettledBurn:
    """停机判据 = 余额 − 当前小时未结算消耗 ≤ 0,盲区压到巡检周期内。"""

    FIXED_NOW = datetime(2026, 8, 22, 10, 35, tzinfo=UTC)  # 当前小时已过半

    @pytest.fixture
    def _freeze_now(self, monkeypatch):
        monkeypatch.setattr(patrol, "now_utc", lambda: self.FIXED_NOW)

    async def test_unsettled_burn_triggers_stop_before_settlement(self, sm, _freeze_now):
        """余额 > 0 但盖不住当前小时已跑消耗 → 当轮停机(不等次小时 :02 落账)。"""
        h0 = hour_floor(self.FIXED_NOW)  # 10:00
        inst_id = await seed_instance(
            sm,
            user_id=1,
            price="1.6800",
            status="running",
            events=[(h0 + timedelta(minutes=5), "creating", "running")],
        )
        async with sm() as session:
            await session.execute(
                update(Wallet).where(Wallet.user_id == 1).values(balance=Decimal("0.40"))
            )
            await session.commit()
        # 已跑 30 分钟 ≈ ¥0.84 未结算;0.40 − 0.84 ≤ 0 → 停机
        counts = await patrol.balance_patrol(sm)
        assert counts["stopped"] == 1
        async with sm() as session:
            from app.modules.orchestrator.models import Instance

            inst = await session.get(Instance, inst_id)
            assert inst.status == "stopping"
            # 欠费通知的短信走 outbox 入队,不在巡检事务里发
            from app.core.outbox import OutboxTask

            tasks = (await session.execute(select(OutboxTask))).scalars().all()
        assert any(t.type == "notify.sms" for t in tasks)
        assert any(t.type == "instance.stop" for t in tasks)

    async def test_coverable_burn_only_warns(self, sm, _freeze_now):
        """余额盖得住未结算消耗 → 不停机;预估时长低于阈值只预警。"""
        h0 = hour_floor(self.FIXED_NOW)
        await seed_instance(
            sm,
            user_id=1,
            price="1.6800",
            status="running",
            events=[(h0 + timedelta(minutes=5), "creating", "running")],
        )
        async with sm() as session:
            await session.execute(
                update(Wallet).where(Wallet.user_id == 1).values(balance=Decimal("5.00"))
            )
            await session.commit()
        counts = await patrol.balance_patrol(sm)
        assert counts["stopped"] == 0
        assert counts["warned"] == 1  # (5.00 − 0.84) / 1.68 ≈ 2.5h < 24h 阈值

    async def test_tail_billed_segment_not_double_counted(self, sm, _freeze_now):
        """当前小时已尾账出费的时段不得重复估进「未结算消耗」(否则会误停机)。"""
        h0 = hour_floor(self.FIXED_NOW)
        # 10:00–10:10 跑过一段(已尾账 600 秒),10:30 又开机至今(10:35,300 秒未结)
        inst_id = await seed_instance(
            sm,
            user_id=1,
            price="1.6800",
            status="running",
            events=[
                (h0, "creating", "running"),
                (h0 + timedelta(minutes=10), "running", "stopping"),
                (h0 + timedelta(minutes=30), "stopped", "starting"),
                (h0 + timedelta(minutes=30), "starting", "running"),
            ],
        )
        async with sm() as session:
            session.add(
                BillHourly(
                    instance_id=inst_id,
                    user_id=1,
                    hour_start=h0,
                    seconds_used=600,
                    unit_price=Decimal("1.6800"),
                    gpu_count=1,
                    amount=Decimal("0.28"),
                    detail={"charged": True},
                )
            )
            await session.execute(
                update(Wallet).where(Wallet.user_id == 1).values(balance=Decimal("0.20"))
            )
            await session.commit()
        # 未结算仅第二段 300 秒 ≈ ¥0.14;0.20 − 0.14 > 0 → 不停机
        counts = await patrol.balance_patrol(sm)
        assert counts["stopped"] == 0

    async def test_lagged_watermark_extends_unsettled_window(self, sm, _freeze_now):
        """结算水位线滞后 5 小时:未落账小时全量计入停机判据(结算故障≠免费算力)。"""
        from app.modules.billing.settlement import _advance_watermark

        h0 = hour_floor(self.FIXED_NOW)  # 10:00
        # 06:35 起跑至今(4h);水位线停在 05:00(结算停摆),06:00 起的小时全未落账
        await seed_instance(
            sm,
            user_id=1,
            price="1.6800",
            status="running",
            events=[(h0 - timedelta(hours=4), "creating", "running")],
        )
        await _advance_watermark(sm, "hourly", h0 - timedelta(hours=5))
        async with sm() as session:
            await session.execute(
                update(Wallet).where(Wallet.user_id == 1).values(balance=Decimal("1.00"))
            )
            await session.commit()
        # 未结算 4h × 1.68 = 6.72;1.00 − 6.72 ≤ 0 → 停机
        # (旧口径只看当前小时 0.98,1.00 − 0.98 > 0 会漏停)
        counts = await patrol.balance_patrol(sm)
        assert counts["stopped"] == 1

    async def test_lagged_watermark_billed_hours_not_double_counted(self, sm, _freeze_now):
        """水位线滞后但窗口内小时已出账(尾账/补结):不得重复估进未结算消耗。"""
        from app.modules.billing.settlement import _advance_watermark

        h0 = hour_floor(self.FIXED_NOW)
        inst_id = await seed_instance(
            sm,
            user_id=1,
            price="1.6800",
            status="running",
            events=[(h0 - timedelta(hours=2), "creating", "running")],
        )
        await _advance_watermark(sm, "hourly", h0 - timedelta(hours=3))
        async with sm() as session:
            # 08:00、09:00 两小时已落账(各 3600 秒 × 1.68 = 1.68),仅当前小时未结
            for h in (h0 - timedelta(hours=2), h0 - timedelta(hours=1)):
                session.add(
                    BillHourly(
                        instance_id=inst_id,
                        user_id=1,
                        hour_start=h,
                        seconds_used=3600,
                        unit_price=Decimal("1.6800"),
                        gpu_count=1,
                        amount=Decimal("1.68"),
                        detail={"charged": True},
                    )
                )
            await session.execute(
                update(Wallet).where(Wallet.user_id == 1).values(balance=Decimal("1.00"))
            )
            await session.commit()
        # 未结算仅当前小时 35 分钟 ≈ 0.98;1.00 − 0.98 > 0 → 不停机
        counts = await patrol.balance_patrol(sm)
        assert counts["stopped"] == 0


class TestSettlementGaps:
    """截断/死信跳窗必须登记缺口,告警指标单调不自愈。"""

    async def test_catchup_truncation_records_gaps(self, sm):
        """停机超追平上限:被跳过的窗口逐一登记 settlement_gaps(整窗,object_id=0)。"""
        from app.modules.billing.settlement import MAX_CATCHUP_HOURS

        await seed_instance(
            sm, events=[(H - timedelta(hours=1), "creating", "running")], status="running"
        )
        await settle_due_hours(sm, at=H_END + timedelta(minutes=2))  # 水位线落在 H
        far = H_END + timedelta(hours=MAX_CATCHUP_HOURS + 10)
        await settle_due_hours(sm, at=far)

        target = H_END + timedelta(hours=MAX_CATCHUP_HOURS + 10 - 1)  # far 的上一整点
        floor = target - timedelta(hours=MAX_CATCHUP_HOURS - 1)
        skipped = int((floor - (H + timedelta(hours=1))).total_seconds() // 3600)
        async with sm() as session:
            gaps = (await session.execute(select(SettlementGap))).scalars().all()
            wm = await get_watermark(session, "hourly")
        assert skipped > 0
        assert len([g for g in gaps if g.reason == "catchup_truncated"]) == skipped
        assert all(g.kind == "hourly" and g.object_id == 0 for g in gaps)
        assert wm == target  # 水位线推进了,但缺口留痕,不静默跳过

    async def test_dead_letter_after_consecutive_failures(self, sm, monkeypatch):
        """单实例连续失败 N 轮 → 死信记缺口,水位线越过,不再反复重试。"""
        from app.modules.orchestrator import service as orchestrator_service

        good = await seed_instance(
            sm,
            user_id=1,
            events=[(H - timedelta(hours=1), "creating", "running")],
            status="running",
        )
        bad = await seed_instance(
            sm,
            user_id=2,
            events=[(H - timedelta(hours=1), "creating", "running")],
            status="running",
        )

        real_lock = orchestrator_service.lock_instance_for_billing

        async def flaky_lock(session, instance_id):
            if instance_id == bad:
                raise RuntimeError("seeded persistent failure")
            await real_lock(session, instance_id)

        monkeypatch.setattr(orchestrator_service, "lock_instance_for_billing", flaky_lock)

        at = H_END + timedelta(minutes=2)
        for round_ in range(1, DEAD_LETTER_AFTER + 1):
            await settle_due_hours(sm, at=at)
            async with sm() as session:
                wm = await get_watermark(session, "hourly")
            if round_ < DEAD_LETTER_AFTER:
                assert wm is None  # 水位线被坏实例卡住
            else:
                assert wm == H  # 死信后水位线越过
        async with sm() as session:
            gaps = (await session.execute(select(SettlementGap))).scalars().all()
            bills = (await session.execute(select(BillHourly))).scalars().all()
        gap_set = {(g.kind, g.object_id, g.reason) for g in gaps}
        assert gap_set == {
            # 前两轮水位线被坏实例卡住无法建立:watermark_missing 如实留痕(幂等,一轮一行)
            ("hourly", 0, "watermark_missing"),
            ("hourly", bad, "dead_letter"),
        }
        assert [b.instance_id for b in bills] == [good]  # 好实例正常入账

        # 下一轮:死信窗口已被水位线越过,不再产生失败
        settlement._failure_streaks.clear()
        assert await settle_due_hours(sm, at=at) == 0

    async def test_persistent_failure_72h_keeps_other_instances_billed(self, sm, monkeypatch):
        """实例稳定失败 72h:坏实例逐窗死信记缺口,好实例 72 个窗口的账一笔不丢。"""
        from app.modules.billing.settlement import _advance_watermark
        from app.modules.orchestrator import service as orchestrator_service

        good = await seed_instance(
            sm,
            user_id=1,
            events=[(H - timedelta(hours=1), "creating", "running")],
            status="running",
        )
        bad = await seed_instance(
            sm,
            user_id=2,
            events=[(H - timedelta(hours=1), "creating", "running")],
            status="running",
        )
        await _advance_watermark(sm, "hourly", H)  # 水位线就位,坏实例从下一窗开始失败

        async def always_fail_lock(session, instance_id):
            if instance_id == bad:
                raise RuntimeError("seeded persistent failure")

        monkeypatch.setattr(orchestrator_service, "lock_instance_for_billing", always_fail_lock)

        # 坏实例的每个窗口要各自连败 DEAD_LETTER_AFTER 轮才死信:
        # +24/+48/+72h 三轮追平后,只有前 24 窗死信,水位线推进到 H+24h
        for round_ in range(1, DEAD_LETTER_AFTER + 1):
            await settle_due_hours(sm, at=H_END + timedelta(hours=24 * round_, minutes=2))
        async with sm() as session:
            assert await get_watermark(session, "hourly") == H + timedelta(hours=24)
        # 再补两轮(同一 at):剩余窗口陆续死信,水位线追平到 H+72h
        for _ in range(DEAD_LETTER_AFTER - 1):
            await settle_due_hours(sm, at=H_END + timedelta(hours=72, minutes=2))
        async with sm() as session:
            good_bills = (
                (await session.execute(select(BillHourly).where(BillHourly.instance_id == good)))
                .scalars()
                .all()
            )
            bad_bills = (
                (await session.execute(select(BillHourly).where(BillHourly.instance_id == bad)))
                .scalars()
                .all()
            )
            gaps = (
                (
                    await session.execute(
                        select(SettlementGap).where(SettlementGap.reason == "dead_letter")
                    )
                )
                .scalars()
                .all()
            )
            wm = await get_watermark(session, "hourly")
        assert len(good_bills) == 72  # 一笔不丢
        assert len(bad_bills) == 0
        assert len(gaps) == 72  # 坏实例每窗一条死信缺口
        assert all(g.object_id == bad for g in gaps)
        assert wm == H + timedelta(hours=72)  # 水位线不再被卡死

    async def test_daily_disk_truncation_records_gaps(self, sm):
        """日结侧同构修复:超追平上限的日期登记 settlement_gaps(kind=daily_disk)。"""
        from app.modules.billing.settlement import MAX_CATCHUP_DAYS, _advance_watermark

        old_day = billing_day_floor(now_utc()) - timedelta(days=MAX_CATCHUP_DAYS + 10)
        await _seed_disk(sm, 1, created_at=old_day)
        await _fund(sm, 1, "100.00")
        await _advance_watermark(sm, "daily_disk", old_day)
        await settle_daily_disks(sm)

        async with sm() as session:
            gaps = (
                (
                    await session.execute(
                        select(SettlementGap).where(SettlementGap.kind == "daily_disk")
                    )
                )
                .scalars()
                .all()
            )
            bills = (await session.execute(select(BillDailyDisk))).scalars().all()
        assert all(g.reason == "catchup_truncated" and g.object_id == 0 for g in gaps)
        # 水位线在 MAX+10 天前 → first_day=水位+1,截到 floor=昨日-(MAX-1) → 缺 9 天
        target_day = billing_day_floor(now_utc()) - timedelta(days=1)
        expected = (target_day - timedelta(days=MAX_CATCHUP_DAYS - 1) - old_day).days - 1
        assert len(gaps) == expected == 9
        assert len(bills) == MAX_CATCHUP_DAYS  # 追平上限内的日子照常出账


class TestReconcileAttribution:
    """日终核对按账单归属期切窗,跨日补差价不误报。"""

    async def test_cross_day_topup_no_false_positive(self, sm):
        """23 点的账单在次日 00:02 被补差价:两侧都归到账单所属日,不误判差异。"""
        await _fund(sm, 1, "100.00")
        yesterday_23h = day_floor(now_utc()) - timedelta(hours=1)
        async with sm() as session:
            bill = BillHourly(
                instance_id=1,
                user_id=1,
                hour_start=yesterday_23h,
                seconds_used=3000,
                unit_price=Decimal("1.6800"),
                gpu_count=1,
                amount=Decimal("1.40"),  # 补差价后的当前值
                detail={"charged": True, "topped_up": True},
                created_at=yesterday_23h + timedelta(minutes=30),  # 首笔尾账在昨天写入
            )
            session.add(bill)
            await session.flush()
            # 尾账(昨天)与补差(今天)两笔流水,ref 同一账单
            await wallet.debit(
                session,
                1,
                Decimal("0.84"),
                type_="consume",
                ref_type="bill_hourly",
                ref_id=str(bill.id),
                allow_negative=True,
            )
            await wallet.debit(
                session,
                1,
                Decimal("0.56"),
                type_="consume",
                ref_type="bill_hourly",
                ref_id=str(bill.id),
                allow_negative=True,
            )
            await session.commit()
        counts = await reconcile_funds(sm)
        assert counts == {"wallet_mismatch": 0, "bill_mismatch": 0}

    async def test_dangling_consume_ref_detected(self, sm):
        """consume 流水回连不到账单(有扣款无出账)必须报差。"""
        await _fund(sm, 1, "100.00")
        async with sm() as session:
            await wallet.debit(
                session,
                1,
                Decimal("3.00"),
                type_="consume",
                ref_type="bill_hourly",
                ref_id="999999999",  # 不存在的账单
                allow_negative=True,
            )
            await session.commit()
        counts = await reconcile_funds(sm)
        assert counts["bill_mismatch"] == 1


class TestWalletChainCheck:
    """钱包核对为增量链式校验:只扫新增流水,断链可定位。"""

    async def test_checkpoint_written_and_second_run_skips(self, sm):
        """首轮全量验过即落游标;无新流水时第二轮不再重扫(游标不动)。"""
        await _fund(sm, 1, "100.00")
        assert (await reconcile_funds(sm))["wallet_mismatch"] == 0
        async with sm() as session:
            cp = await session.get(ReconcileCheckpoint, 1)
            first_last_id = cp.last_ledger_id
        assert first_last_id > 0
        assert (await reconcile_funds(sm))["wallet_mismatch"] == 0
        async with sm() as session:
            cp2 = await session.get(ReconcileCheckpoint, 1)
        assert cp2.last_ledger_id == first_last_id  # 没被无谓推进

    async def test_new_entries_verified_incrementally(self, sm):
        """新流水触发重验,游标跟进到最新一笔。"""
        await _fund(sm, 1, "100.00")
        await reconcile_funds(sm)
        async with sm() as session:
            await wallet.debit(session, 1, Decimal("5.00"), type_="consume", allow_negative=True)
            await session.commit()
        assert (await reconcile_funds(sm))["wallet_mismatch"] == 0
        async with sm() as session:
            cp = await session.get(ReconcileCheckpoint, 1)
            last = (
                await session.execute(
                    select(func.max(BalanceLedger.id)).where(BalanceLedger.user_id == 1)
                )
            ).scalar_one()
        assert cp.last_ledger_id == last

    async def test_chain_break_localized_to_entry(self, sm):
        """手工塞进一笔 balance_after 造假的流水:报差且游标停在断链之前。"""
        await _fund(sm, 1, "100.00")
        await reconcile_funds(sm)
        async with sm() as session:
            session.add(
                BalanceLedger(
                    user_id=1,
                    type="consume",
                    amount=Decimal("-5.00"),
                    balance_after=Decimal("999.00"),  # 应为 95.00
                )
            )
            await session.commit()
        counts = await reconcile_funds(sm)
        assert counts["wallet_mismatch"] == 1
        async with sm() as session:
            cp = await session.get(ReconcileCheckpoint, 1)
            entries = (
                (await session.execute(select(BalanceLedger).order_by(BalanceLedger.id)))
                .scalars()
                .all()
            )
        assert cp.last_ledger_id == entries[0].id  # 游标停在断链之前,下一轮重验

    async def test_checkpoint_boundary_row_deleted_detected(self, sm):
        """游标所指的流水行被删/被改:边界复核必须发现(不能只信游标)。"""
        from sqlalchemy import delete

        await _fund(sm, 1, "100.00")
        await reconcile_funds(sm)
        async with sm() as session:
            last = (
                await session.execute(
                    select(func.max(BalanceLedger.id)).where(BalanceLedger.user_id == 1)
                )
            ).scalar_one()
            await session.execute(delete(BalanceLedger).where(BalanceLedger.id == last))
            await session.execute(
                update(Wallet).where(Wallet.user_id == 1).values(balance=Decimal("0.00"))
            )
            await session.commit()
        counts = await reconcile_funds(sm)
        assert counts["wallet_mismatch"] == 1


class TestRevenueAttribution:
    """营收按账单归属期(hour_start/day)计,不按扣款入账时间。"""

    async def test_last_hour_of_day_attributed_to_that_day(self, sm):
        """昨日 23 点的消费在今日 00:02 才扣款:报表必须归到昨日。"""
        await _fund(sm, 1, "100.00")
        yesterday_23h = day_floor(now_utc()) - timedelta(hours=1)
        today_00_30 = day_floor(now_utc()) + timedelta(minutes=30)
        async with sm() as session:
            bill = BillHourly(
                instance_id=1,
                user_id=1,
                hour_start=yesterday_23h,
                seconds_used=3600,
                unit_price=Decimal("1.6800"),
                gpu_count=1,
                amount=Decimal("1.68"),
                detail={"charged": True},
                created_at=today_00_30,  # 次小时 :02 才入账
            )
            session.add(bill)
            await session.flush()
            await wallet.debit(
                session,
                1,
                Decimal("1.68"),
                type_="consume",
                ref_type="bill_hourly",
                ref_id=str(bill.id),
                allow_negative=True,
            )
            await session.commit()
        async with sm() as session:
            summary = await wallet.revenue_summary(session, tz_offset_minutes=0)
        assert summary["yesterday_revenue"] == "1.68"
        assert summary["today_revenue"] == "0"
        # 每月 1 号凌晨跑时昨日 23 点落在上个月:月合计跟着归属期走
        month_start = day_floor(now_utc()).replace(day=1)
        assert summary["month_revenue"] == ("1.68" if yesterday_23h >= month_start else "0")


class TestSmsOutbox:
    """短信经 outbox 异步投递,业务事务里不做网络调用。"""

    async def test_notify_enqueues_sms_and_handler_sends(self, client, sm):
        from app.core.outbox import OutboxTask, drain
        from app.core.sms import set_sms_channel
        from app.modules.notify import service as notify_service
        from tests.test_account_auth import register

        sent: list[dict] = []

        class SpySms:
            async def send(self, phone, template, params):
                sent.append({"phone": phone, "params": params})

        # 先注册再装探针:注册流程的验证码短信不走本用例的探针
        data = await register(client, "13900000077")
        set_sms_channel(SpySms())
        try:
            async with sm() as session:
                ok = await notify_service.notify(
                    session,
                    data["user"]["id"],
                    type_="balance_warn",
                    title="余额不足预警",
                    content="t",
                    dedup_key="test:sms:outbox",
                    sms=True,
                )
                await session.commit()
            assert ok
            assert sent == []  # 事务里不发短信,只入队
            async with sm() as session:
                tasks = (await session.execute(select(OutboxTask))).scalars().all()
            sms_tasks = [t for t in tasks if t.type == "notify.sms"]
            assert len(sms_tasks) == 1
            assert sms_tasks[0].payload["title"] == "余额不足预警"
            await drain(sm)
            assert len(sent) == 1
            assert sent[0]["params"] == {"title": "余额不足预警"}
        finally:
            set_sms_channel(None)

    async def test_notify_without_sms_enqueues_nothing(self, client, sm):
        from app.core.outbox import OutboxTask
        from app.modules.notify import service as notify_service
        from tests.test_account_auth import register

        data = await register(client, "13900000078")
        async with sm() as session:
            await notify_service.notify(
                session, data["user"]["id"], type_="announcement", title="t", content="c"
            )
            await session.commit()
        async with sm() as session:
            tasks = (await session.execute(select(OutboxTask))).scalars().all()
        assert [t for t in tasks if t.type == "notify.sms"] == []


class TestPriceFloor:
    """时价折算满 1 小时不足 ¥0.01 的 SKU 恒免费,上架/改价必须拦。"""

    def test_sub_half_cent_price_rejected(self):
        from app.modules.catalog.service import _checked_price

        with pytest.raises(AppError) as exc:
            _checked_price(Decimal("0.0049"))
        assert exc.value.message_key == "catalog.priceBelowBillable"

    def test_half_cent_tie_rejected(self):
        """0.0050 恰是分位 tie,ROUND_HALF_EVEN 向偶舍为 0.00 —— 同样免费,必须拒。"""
        from app.modules.catalog.service import _checked_price

        with pytest.raises(AppError) as exc:
            _checked_price(Decimal("0.0050"))
        assert exc.value.message_key == "catalog.priceBelowBillable"

    def test_min_billable_price_accepted(self):
        from app.modules.catalog.service import _checked_price

        assert _checked_price(Decimal("0.01")) == Decimal("0.01")
        assert _checked_price(Decimal("1.6800")) == Decimal("1.6800")

    def test_sub_cent_precision_rejected(self):
        """按小时计费的 SKU 超过 2 位小数即拒:逐小时独立舍入会单向漂移
        (0.0051 被按 0.01/时近翻倍收;1.2345 满月少收 0.36%)。"""
        from app.modules.catalog.service import _checked_price

        with pytest.raises(AppError) as exc:
            _checked_price(Decimal("0.0051"))
        assert exc.value.message_key == "catalog.priceHourlyTwoDecimals"
        with pytest.raises(AppError) as exc:
            _checked_price(Decimal("1.2345"))
        assert exc.value.message_key == "catalog.priceHourlyTwoDecimals"

    def test_zero_price_still_rejected_with_original_key(self):
        from app.modules.catalog.service import _checked_price

        with pytest.raises(AppError) as exc:
            _checked_price(Decimal("0.0000"))
        assert exc.value.message_key == "catalog.priceTooSmall"
