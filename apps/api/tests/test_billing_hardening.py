"""Billing and wallet: burn-rate creation check, live-estimate stops in the patrol, settlement gaps,
incremental reconciliation, revenue attribution."""

# pyright: reportPrivateUsage=false

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update

from app.core.errors import AppError, ErrorCode
from app.core.money import money_label
from app.core.timeutil import billing_day_floor, hour_floor, now_utc
from app.modules.account.models import User
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
from tests.helpers import (
    H_END,
    H,
    apply_refund,
    drain,
    finance_pair,
    fund_wallet,
    paid_order,
    register,
    seed_disk,
    seed_instance,
    user_headers,
)


@pytest.fixture(autouse=True)
def _clear_failure_streaks():
    """Reset the in-process dead-letter failure streak."""
    settlement._failure_streaks.clear()
    yield
    settlement._failure_streaks.clear()


def _utc_day_start() -> datetime:
    """Start of the UTC calendar day."""
    return now_utc().replace(hour=0, minute=0, second=0, microsecond=0)


class TestDebitFrozenGuard:
    """Frozen gate (debit's allow_frozen): new consumption must not break through the frozen
    amount."""

    async def test_debit_into_frozen_rejected_by_default(self, sm):
        """Default: a debit leaving the balance < frozen is refused."""
        await fund_wallet(sm, 1, "100.00")
        async with sm() as s:
            await wallet.freeze(s, 1, Decimal("60.00"), ref_id="ord-1", remark="channel reversal")
            with pytest.raises(AppError) as ei:
                await wallet.debit(s, 1, Decimal("50.00"), allow_negative=False)
            assert ei.value.code == ErrorCode.INSUFFICIENT_BALANCE
            assert ei.value.message_key == "billing.insufficientAvailableFrozen"
            await s.rollback()
        async with sm() as s:
            assert (await wallet.lock_wallet(s, 1)).balance == Decimal("100.00")

    async def test_debit_within_available_passes(self, sm):
        """A debit that stays above the frozen amount passes (available = balance - frozen)."""
        await fund_wallet(sm, 1, "100.00")
        async with sm() as s:
            await wallet.freeze(s, 1, Decimal("60.00"), ref_id="ord-1", remark="channel reversal")
            await wallet.debit(s, 1, Decimal("40.00"), allow_negative=False)
            await s.commit()
        async with sm() as s:
            assert (await wallet.lock_wallet(s, 1)).balance == Decimal("60.00")

    async def test_settlement_debit_may_dip_into_frozen(self, sm):
        """Settlement debits with allow_frozen=True may break through the freeze."""
        await fund_wallet(sm, 1, "100.00")
        async with sm() as s:
            await wallet.freeze(s, 1, Decimal("90.00"), ref_id="ord-1", remark="channel reversal")
            await wallet.debit(s, 1, Decimal("50.00"), allow_negative=True, allow_frozen=True)
            await s.commit()
        async with sm() as s:
            w = await wallet.lock_wallet(s, 1)
            assert w.balance == Decimal("50.00")
            assert w.frozen == Decimal("90.00")

    async def test_unfreeze_then_debit_is_unblocked(self, sm):
        """The write-off path (resolve_reversal unfreezes, then debits) is not bound by the frozen
        gate."""
        await fund_wallet(sm, 1, "100.00")
        async with sm() as s:
            await wallet.freeze(s, 1, Decimal("100.00"), ref_id="ord-1", remark="channel reversal")
            await wallet.release_freeze(s, 1, Decimal("100.00"))
            await wallet.debit(s, 1, Decimal("100.00"), allow_negative=True)
            await s.commit()
        async with sm() as s:
            assert (await wallet.lock_wallet(s, 1)).balance == Decimal("0.00")


class TestWalletLockGuards:
    """Wallet row lock and balance reads under the lock."""

    async def test_concurrent_credit_debit_no_lost_update(self, sm):
        """Concurrent credit/debit on one wallet: no lost update, the balance_after chain stays
        contiguous."""
        await fund_wallet(sm, 1, "100.00")
        gate = asyncio.Barrier(9)

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
        assert w.balance == Decimal("128.00")
        expected = Decimal("0.00")
        for e in entries:
            assert e.balance_after == expected + e.amount
            expected = e.balance_after
        assert expected == w.balance

    async def test_payout_balance_recheck_reads_fresh_row(self, sm, client):
        """The balance re-check under the lock reads the current database value."""
        from app.modules.adminapi.models import AdminUser
        from app.modules.billing import refunds
        from app.modules.billing.models import Order

        headers = await user_headers(client, "u13700000116@test.local")
        order = await paid_order(client, headers, "50.00")
        rid = (await apply_refund(client, headers, order["order_no"], "40.00")).json()["id"]
        reviewer, _payer_headers = await finance_pair(sm, client)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "approved"},
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
        async with sm() as session:
            _stale_wallet = (
                await session.execute(select(Wallet).where(Wallet.user_id == uid))
            ).scalar_one()
            async with sm() as s2:
                await wallet.debit(s2, uid, Decimal("30.00"), allow_negative=True)
                await s2.commit()
            with pytest.raises(AppError) as exc:
                await refunds.payout_refund(
                    session, rid, channel="offline", ref="OFF-TOCTOU", operator_id=payer_id
                )
            assert exc.value.http_status == 409
        async with sm() as s:
            w = (await s.execute(select(Wallet).where(Wallet.user_id == uid))).scalar_one()
        assert w.balance == Decimal("20.00")


class TestAffordGuard:
    """assert_can_afford burn-rate check."""

    async def test_first_instance_passes_with_one_hour_cover(self, sm):
        """No in-flight resources: balance ≥ one hour of the new fee passes."""
        await fund_wallet(sm, 1, "1.68")
        async with sm() as session:
            await wallet.assert_can_afford(session, 1, additional_hourly=Decimal("1.68"))

    async def test_serial_create_blocked_by_inflight_burn(self, sm):
        """One instance already running at 1.68/h: the same balance refuses a second one, the copy
        carries the in-flight burn."""
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
        assert exc.value.params == {
            "balance": money_label("1.68"),
            "required": money_label("3.36"),
            "inflight": money_label("1.68"),
        }
        assert exc.value.message_key == "billing.insufficientForInFlight"

    async def test_pending_starting_instance_counted(self, sm):
        """creating/starting instances count towards the burn rate."""
        await seed_instance(sm, user_id=1, price="1.6800", status="starting")
        async with sm() as session:
            await session.execute(
                update(Wallet).where(Wallet.user_id == 1).values(balance=Decimal("1.68"))
            )
            await session.commit()
        async with sm() as session:
            with pytest.raises(AppError) as exc:
                await wallet.assert_can_afford(session, 1, additional_hourly=Decimal("1.68"))
        assert exc.value.code is ErrorCode.INSUFFICIENT_BALANCE
        assert exc.value.params == {
            "balance": money_label("1.68"),
            "required": money_label("3.36"),
            "inflight": money_label("1.68"),
        }

    async def test_pending_subscription_instance_not_counted(self, sm):
        """Subscription creating/starting instances do not count as pending burn."""
        await seed_instance(sm, user_id=1, price="1.6800", status="starting", market="subscription")
        await fund_wallet(sm, 1, "1.68")
        async with sm() as session:
            await wallet.assert_can_afford(session, 1, additional_hourly=Decimal("1.68"))

    async def test_inflight_disk_daily_fee_counted(self, sm):
        """In-flight data disks count as daily fee × grace days."""
        await seed_disk(sm, 1, size_gb=100, price="0.3500")
        await fund_wallet(sm, 1, "5.00")
        async with sm() as session:
            with pytest.raises(AppError) as exc:
                await wallet.assert_can_afford(session, 1)
        assert exc.value.params is not None
        assert exc.value.params["inflight"] == money_label("8.19")
        async with sm() as session:
            await session.execute(
                update(Wallet).where(Wallet.user_id == 1).values(balance=Decimal("8.19"))
            )
            await session.commit()
        async with sm() as session:
            await wallet.assert_can_afford(session, 1)

    async def test_additional_disk_needs_grace_days_cover(self, sm):
        """New data disk: balance ≥ new daily fee × grace days."""
        await fund_wallet(sm, 1, "0.70")
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
        """frozen disks are not billed (disks.BILLABLE_STATUSES) and take no burn-rate room."""
        await seed_disk(sm, 1, status="frozen")
        await fund_wallet(sm, 1, "0.01")
        async with sm() as session:
            await wallet.assert_can_afford(session, 1)

    async def test_cover_hours_policy_tunable(self, sm):
        """afford_cover_hours is adjustable online through policies."""
        from app.core.platform_config import PlatformSetting

        await seed_instance(sm, user_id=1, price="1.6800", status="running")
        async with sm() as session:
            session.add(PlatformSetting(key="afford_cover_hours", value="2", updated_by=None))
            await session.execute(
                update(Wallet).where(Wallet.user_id == 1).values(balance=Decimal("3.36"))
            )
            await session.commit()
        async with sm() as session:
            await wallet.assert_can_afford(session, 1)
        async with sm() as session:
            with pytest.raises(AppError):
                await wallet.assert_can_afford(session, 1, additional_hourly=Decimal("1.68"))


class TestPatrolUnsettledBurn:
    """Stop criterion = balance − unsettled consumption ≤ 0."""

    FIXED_NOW = datetime(2026, 8, 22, 10, 35, tzinfo=UTC)

    @pytest.fixture
    def _freeze_now(self, monkeypatch):
        monkeypatch.setattr(patrol, "now_utc", lambda: self.FIXED_NOW)

    async def test_unsettled_burn_triggers_stop_before_settlement(self, sm, _freeze_now):
        """Balance > 0 but below the consumption already run this hour → stopped this round."""
        h0 = hour_floor(self.FIXED_NOW)
        inst_id, _ = await seed_instance(
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
        counts = await patrol.balance_patrol(sm)
        assert counts["stopped"] == 1
        async with sm() as session:
            from app.modules.orchestrator.models import Instance

            inst = await session.get(Instance, inst_id)
            assert inst.status == "stopping"
            from app.core.outbox import OutboxTask

            tasks = (await session.execute(select(OutboxTask))).scalars().all()
        assert any(t.type == "notify.sms" for t in tasks)
        assert any(t.type == "instance.stop" for t in tasks)

    async def test_tail_billed_segment_not_double_counted(self, sm, _freeze_now):
        """A stretch already tail-billed this hour is not estimated again as unsettled."""
        h0 = hour_floor(self.FIXED_NOW)
        inst_id, _ = await seed_instance(
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
                )
            )
            await session.execute(
                update(Wallet).where(Wallet.user_id == 1).values(balance=Decimal("0.20"))
            )
            await session.commit()
        counts = await patrol.balance_patrol(sm)
        assert counts["stopped"] == 0

    async def test_lagged_watermark_extends_unsettled_window(self, sm, _freeze_now):
        """Settlement watermark 5 hours behind: every unbilled hour counts in the stop criterion."""
        from app.modules.billing.settlement import _advance_watermark

        h0 = hour_floor(self.FIXED_NOW)
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
        counts = await patrol.balance_patrol(sm)
        assert counts["stopped"] == 1

    async def test_lagged_watermark_billed_hours_not_double_counted(self, sm, _freeze_now):
        """Watermark behind but an hour inside the window already billed: not estimated again."""
        from app.modules.billing.settlement import _advance_watermark

        h0 = hour_floor(self.FIXED_NOW)
        inst_id, _ = await seed_instance(
            sm,
            user_id=1,
            price="1.6800",
            status="running",
            events=[(h0 - timedelta(hours=2), "creating", "running")],
        )
        await _advance_watermark(sm, "hourly", h0 - timedelta(hours=3))
        async with sm() as session:
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
                    )
                )
            await session.execute(
                update(Wallet).where(Wallet.user_id == 1).values(balance=Decimal("1.00"))
            )
            await session.commit()
        counts = await patrol.balance_patrol(sm)
        assert counts["stopped"] == 0


class TestSettlementGaps:
    """Truncation / dead-letter skips record gaps."""

    async def test_catchup_truncation_records_gaps(self, sm):
        """Outage beyond the catch-up cap: the skipped windows are recorded one by one in
        settlement_gaps (whole window, object_id=0)."""
        from app.modules.billing.settlement import MAX_CATCHUP_HOURS

        await seed_instance(
            sm, events=[(H - timedelta(hours=1), "creating", "running")], status="running"
        )
        await settle_due_hours(sm, at=H_END + timedelta(minutes=2))
        far = H_END + timedelta(hours=MAX_CATCHUP_HOURS + 10)
        assert await settle_due_hours(sm, at=far) == MAX_CATCHUP_HOURS

        target = H_END + timedelta(hours=MAX_CATCHUP_HOURS + 10 - 1)
        floor = target - timedelta(hours=MAX_CATCHUP_HOURS - 1)
        skipped = int((floor - (H + timedelta(hours=1))).total_seconds() // 3600)
        async with sm() as session:
            gaps = (await session.execute(select(SettlementGap))).scalars().all()
            wm = await get_watermark(session, "hourly")
        assert skipped > 0
        assert len([g for g in gaps if g.reason == "catchup_truncated"]) == skipped
        assert all(g.kind == "hourly" and g.object_id == 0 for g in gaps)
        assert wm == target

    async def test_dead_letter_after_consecutive_failures(self, sm, monkeypatch):
        """One instance failing N rounds in a row → dead-letter gap, the watermark passes."""
        from app.modules.orchestrator import queries as orchestrator_queries

        good, _ = await seed_instance(
            sm,
            user_id=1,
            events=[(H - timedelta(hours=1), "creating", "running")],
            status="running",
        )
        bad, _ = await seed_instance(
            sm,
            user_id=2,
            events=[(H - timedelta(hours=1), "creating", "running")],
            status="running",
        )

        real_lock = orchestrator_queries.lock_instance_for_billing

        async def flaky_lock(session, instance_id):
            if instance_id == bad:
                raise RuntimeError("seeded persistent failure")
            await real_lock(session, instance_id)

        monkeypatch.setattr(orchestrator_queries, "lock_instance_for_billing", flaky_lock)

        at = H_END + timedelta(minutes=2)
        for round_ in range(1, DEAD_LETTER_AFTER + 1):
            await settle_due_hours(sm, at=at)
            async with sm() as session:
                wm = await get_watermark(session, "hourly")
            if round_ < DEAD_LETTER_AFTER:
                assert wm is None
            else:
                assert wm == H
        async with sm() as session:
            gaps = (await session.execute(select(SettlementGap))).scalars().all()
            bills = (await session.execute(select(BillHourly))).scalars().all()
        gap_set = {(g.kind, g.object_id, g.reason) for g in gaps}
        assert gap_set == {
            ("hourly", 0, "watermark_missing"),
            ("hourly", bad, "dead_letter"),
        }
        assert [b.instance_id for b in bills] == [good]

        settlement._failure_streaks.clear()
        assert await settle_due_hours(sm, at=at) == 0

    async def test_persistent_failure_keeps_other_instances_billed(self, sm, monkeypatch):
        """The bad instance dead-letters window by window, the good instance loses no bill."""
        from app.modules.billing.settlement import _advance_watermark
        from app.modules.orchestrator import queries as orchestrator_queries

        good, _ = await seed_instance(
            sm,
            user_id=1,
            events=[(H - timedelta(hours=1), "creating", "running")],
            status="running",
        )
        bad, _ = await seed_instance(
            sm,
            user_id=2,
            events=[(H - timedelta(hours=1), "creating", "running")],
            status="running",
        )
        await _advance_watermark(sm, "hourly", H)

        async def always_fail_lock(session, instance_id):
            if instance_id == bad:
                raise RuntimeError("seeded persistent failure")

        monkeypatch.setattr(orchestrator_queries, "lock_instance_for_billing", always_fail_lock)

        at = H_END + timedelta(hours=3, minutes=2)
        for round_ in range(1, DEAD_LETTER_AFTER + 1):
            await settle_due_hours(sm, at=at)
            async with sm() as session:
                wm = await get_watermark(session, "hourly")
            assert wm == (H + timedelta(hours=3) if round_ == DEAD_LETTER_AFTER else H)
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
        assert len(good_bills) == 3
        assert len(bad_bills) == 0
        assert len(gaps) == 3
        assert all(g.object_id == bad for g in gaps)
        assert wm == H + timedelta(hours=3)

    async def test_daily_disk_truncation_records_gaps(self, sm):
        """Daily settlement beyond the catch-up cap records settlement_gaps (kind=daily_disk)."""
        from app.modules.billing.settlement import MAX_CATCHUP_DAYS, _advance_watermark

        old_day = billing_day_floor(now_utc()) - timedelta(days=MAX_CATCHUP_DAYS + 10)
        await seed_disk(sm, 1, created_at=old_day)
        await fund_wallet(sm, 1, "100.00")
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
        target_day = billing_day_floor(now_utc()) - timedelta(days=1)
        expected = (target_day - timedelta(days=MAX_CATCHUP_DAYS - 1) - old_day).days - 1
        assert len(gaps) == expected == 9
        assert len(bills) == MAX_CATCHUP_DAYS


class TestReconcileAttribution:
    """End-of-day reconciliation windows by bill attribution period."""

    async def test_cross_day_topup_no_false_positive(self, sm):
        """The 23:00 bill topped up at 00:02 the next day: both sides attribute to the bill's
        day."""
        await fund_wallet(sm, 1, "100.00")
        yesterday_23h = _utc_day_start() - timedelta(hours=1)
        async with sm() as session:
            bill = BillHourly(
                instance_id=1,
                user_id=1,
                hour_start=yesterday_23h,
                seconds_used=3000,
                unit_price=Decimal("1.6800"),
                gpu_count=1,
                amount=Decimal("1.40"),
                detail={"topped_up": True},
                created_at=yesterday_23h + timedelta(minutes=30),
            )
            session.add(bill)
            await session.flush()
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
        """A consume ledger row that links to no bill is reported."""
        await fund_wallet(sm, 1, "100.00")
        async with sm() as session:
            await wallet.debit(
                session,
                1,
                Decimal("3.00"),
                type_="consume",
                ref_type="bill_hourly",
                ref_id="999999999",
                allow_negative=True,
            )
            await session.commit()
        counts = await reconcile_funds(sm)
        assert counts["bill_mismatch"] == 1


class TestWalletChainCheck:
    """Incremental chain verification of wallets."""

    async def test_checkpoint_written_and_second_run_skips(self, sm):
        """The first full round stores the cursor; without new rows the second round leaves it."""
        await fund_wallet(sm, 1, "100.00")
        assert (await reconcile_funds(sm))["wallet_mismatch"] == 0
        async with sm() as session:
            cp = await session.get(ReconcileCheckpoint, 1)
            first_last_id = cp.last_ledger_id
        assert first_last_id > 0
        assert (await reconcile_funds(sm))["wallet_mismatch"] == 0
        async with sm() as session:
            cp2 = await session.get(ReconcileCheckpoint, 1)
        assert cp2.last_ledger_id == first_last_id

    async def test_new_entries_verified_incrementally(self, sm):
        """New ledger rows trigger re-verification and the cursor follows to the latest row."""
        await fund_wallet(sm, 1, "100.00")
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
        """A broken balance_after chain is reported and the cursor stays before the break."""
        await fund_wallet(sm, 1, "100.00")
        await reconcile_funds(sm)
        async with sm() as session:
            session.add(
                BalanceLedger(
                    user_id=1,
                    type="consume",
                    amount=Decimal("-5.00"),
                    balance_after=Decimal("999.00"),
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
        assert cp.last_ledger_id == entries[0].id

    async def test_checkpoint_boundary_row_deleted_detected(self, sm):
        """The ledger row the cursor points at was deleted / changed: the boundary re-check
        reports."""
        from sqlalchemy import delete

        await fund_wallet(sm, 1, "100.00")
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
    """Revenue is attributed by bill period (hour_start/day)."""

    async def test_last_hour_of_day_attributed_to_that_day(self, sm):
        """Yesterday's 23:00 consumption debited at 00:02 today: attributed to yesterday."""
        await fund_wallet(sm, 1, "100.00")
        yesterday_23h = _utc_day_start() - timedelta(hours=1)
        today_00_30 = _utc_day_start() + timedelta(minutes=30)
        async with sm() as session:
            bill = BillHourly(
                instance_id=1,
                user_id=1,
                hour_start=yesterday_23h,
                seconds_used=3600,
                unit_price=Decimal("1.6800"),
                gpu_count=1,
                amount=Decimal("1.68"),
                created_at=today_00_30,
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
        month_start = _utc_day_start().replace(day=1)
        assert summary["month_revenue"] == ("1.68" if yesterday_23h >= month_start else "0")


class TestSmsOutbox:
    """SMS goes out asynchronously through the outbox."""

    async def test_notify_enqueues_sms_and_handler_sends(self, client, sm):
        from app.core.outbox import OutboxTask
        from app.core.sms import set_sms_channel
        from app.modules.notify import service as notify_service

        sent: list[dict] = []

        class SpySms:
            async def send(self, phone, kind, params, *, locale="en-US"):
                sent.append({"phone": phone, "params": params})

        data = await register(client, "u13900000077@test.local")
        async with sm() as session:
            await session.execute(
                update(User).where(User.id == data["user"]["id"]).values(phone="+8613900000077")
            )
            await session.commit()
        set_sms_channel(SpySms())
        try:
            async with sm() as session:
                ok = await notify_service.notify(
                    session,
                    data["user"]["id"],
                    type_="balance_warn",
                    title="Low balance warning",
                    content="t",
                    dedup_key="test:sms:outbox",
                    sms=True,
                )
                await session.commit()
            assert ok
            assert sent == []
            async with sm() as session:
                tasks = (await session.execute(select(OutboxTask))).scalars().all()
            sms_tasks = [t for t in tasks if t.type == "notify.sms"]
            assert len(sms_tasks) == 1
            assert sms_tasks[0].payload["title"] == "Low balance warning"
            await drain(sm)
            assert len(sent) == 1
            assert sent[0]["params"] == {"title": "Low balance warning"}
        finally:
            set_sms_channel(None)

    async def test_notify_without_sms_enqueues_nothing(self, client, sm):
        from app.core.outbox import OutboxTask
        from app.modules.notify import service as notify_service

        data = await register(client, "u13900000078@test.local")
        async with sm() as session:
            await notify_service.notify(
                session, data["user"]["id"], type_="announcement", title="t", content="c"
            )
            await session.commit()
        async with sm() as session:
            tasks = (await session.execute(select(OutboxTask))).scalars().all()
        assert [t for t in tasks if t.type == "notify.sms"] == []
