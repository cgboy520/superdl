# pyright: reportPrivateUsage=false
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select, update

from app.modules.billing import edge_listener, reconcile, settlement, wallet
from app.modules.billing.models import BalanceLedger, BillHourly, Wallet
from app.modules.billing.settlement import (
    _lag_windows,
    bill_amount,
    get_watermark,
    running_seconds_in_window,
    settle_due_hours,
    settle_instance_window,
    upsert_hour_bill,
)
from app.modules.orchestrator import transitions
from app.modules.orchestrator.models import Instance, InstanceEvent
from tests.helpers import H_END, H, admin_headers, seed_instance


def ev(minute: float, from_s: str | None, to_s: str) -> tuple[datetime, str | None, str]:
    return (H + timedelta(minutes=minute), from_s, to_s)


def evm(
    minute: float, from_s: str | None, to_s: str, meta: dict
) -> tuple[datetime, str | None, str, dict]:
    """Event with metadata."""
    return (H + timedelta(minutes=minute), from_s, to_s, meta)


class TestRunningSeconds:
    def test_full_hour(self):
        events = [ev(-120, None, "creating"), ev(-119, "creating", "running")]
        assert running_seconds_in_window(events, H, H_END) == 3600

    def test_enter_mid_window(self):
        events = [ev(-5, None, "creating"), ev(10, "creating", "running")]
        assert running_seconds_in_window(events, H, H_END) == 3000

    def test_multiple_segments(self):
        events = [
            ev(0, "creating", "running"),
            ev(10, "running", "stopping"),
            ev(10.5, "stopping", "stopped"),
            ev(20, "stopped", "starting"),
            ev(21, "starting", "running"),
            ev(31, "running", "stopping"),
        ]
        assert running_seconds_in_window(events, H, H_END) == 600 + 600

    def test_leave_only(self):
        events = [ev(-30, "creating", "running"), ev(15, "running", "failed")]
        assert running_seconds_in_window(events, H, H_END) == 900

    def test_boundary_events(self):
        assert running_seconds_in_window([ev(60, "starting", "running")], H, H_END) == 0
        events = [ev(-30, "creating", "running"), ev(0, "running", "stopping")]
        assert running_seconds_in_window(events, H, H_END) == 0

    def test_events_after_window_ignored(self):
        events = [ev(10, "creating", "running"), ev(70, "running", "stopping")]
        assert running_seconds_in_window(events, H, H_END) == 3000

    def test_subsecond_rounds_half_even_not_truncates(self):
        """Microsecond events: whole microseconds summed + HALF_EVEN rounding."""
        us = timedelta(microseconds=1)
        events = [
            (H, None, "creating"),
            (H, "creating", "running"),
            (H + 600_000 * us, "running", "stopping"),
        ]
        assert running_seconds_in_window(events, H, H_END) == 1
        events[2] = (H + 500_000 * us, "running", "stopping")
        assert running_seconds_in_window(events, H, H_END) == 0
        events[2] = (H + 1_500_000 * us, "running", "stopping")
        assert running_seconds_in_window(events, H, H_END) == 2
        events[2] = (H + 2_500_000 * us, "running", "stopping")
        assert running_seconds_in_window(events, H, H_END) == 2


class TestBillAmount:
    def test_exact(self):
        assert bill_amount(Decimal("1.6800"), 1, 3600) == Decimal("1.68")
        assert bill_amount(Decimal("1.6800"), 2, 1800) == Decimal("1.68")

    def test_half_even(self):
        """An amount exactly on a half cent rounds HALF_EVEN."""
        assert bill_amount(Decimal("0.5000"), 1, 900) == Decimal("0.12")
        assert bill_amount(Decimal("0.5400"), 1, 900) == Decimal("0.14")
        assert bill_amount(Decimal("3.0000"), 1, 3) == Decimal("0.00")
        assert bill_amount(Decimal("9.0000"), 1, 3) == Decimal("0.01")

    def test_zero(self):
        assert bill_amount(Decimal("9.9900"), 1, 0) == Decimal("0.00")

    def test_out_of_range_raises(self):
        with pytest.raises(ValueError):
            bill_amount(Decimal("1.0000"), 1, 3601)
        with pytest.raises(ValueError):
            bill_amount(Decimal("1.0000"), 1, -1)


class TestUpsertIdempotency:
    async def test_repeat_execution_no_double_charge(self, sm):
        inst_id, _ = await seed_instance(
            sm, events=[ev(0, "creating", "running"), ev(30, "running", "stopping")]
        )
        for _ in range(3):
            async with sm() as session:
                await settle_instance_window(
                    session,
                    instance_id=inst_id,
                    user_id=1,
                    unit_price=Decimal("1.6800"),
                    gpu_count=1,
                    window_start=H,
                    window_end=H_END,
                    source="hourly",
                )
                await session.commit()
        async with sm() as session:
            bills = (await session.execute(select(BillHourly))).scalars().all()
            w = (await session.execute(select(Wallet))).scalar_one()
            ledger = (await session.execute(select(BalanceLedger))).scalars().all()
        assert len(bills) == 1
        assert bills[0].seconds_used == 1800
        assert bills[0].amount == Decimal("0.84")
        assert w.balance == Decimal("99.16")
        assert len([e for e in ledger if e.type == "consume"]) == 1

    async def test_growth_tops_up_delta(self, sm):
        """Tail bill first (30 min) then clock-hour settlement (50 min) in the same hour → only the
        difference is topped up."""
        inst_id, _ = await seed_instance(
            sm, events=[ev(0, "creating", "running"), ev(30, "running", "stopping")]
        )
        async with sm() as session:
            first = await settle_instance_window(
                session,
                instance_id=inst_id,
                user_id=1,
                unit_price=Decimal("1.6800"),
                gpu_count=1,
                window_start=H,
                window_end=H + timedelta(minutes=30),
                source="tail",
            )
            await session.commit()
        assert first == Decimal("0.84")

        async with sm() as session:
            session.add_all(
                [
                    InstanceEvent(
                        instance_id=inst_id,
                        from_status="stopped",
                        to_status="starting",
                        reason="seed",
                        actor="system",
                        created_at=H + timedelta(minutes=30),
                    ),
                    InstanceEvent(
                        instance_id=inst_id,
                        from_status="starting",
                        to_status="running",
                        reason="seed",
                        actor="system",
                        created_at=H + timedelta(minutes=30),
                    ),
                    InstanceEvent(
                        instance_id=inst_id,
                        from_status="running",
                        to_status="stopping",
                        reason="seed",
                        actor="system",
                        created_at=H + timedelta(minutes=50),
                    ),
                ]
            )
            await session.commit()
        async with sm() as session:
            second = await settle_instance_window(
                session,
                instance_id=inst_id,
                user_id=1,
                unit_price=Decimal("1.6800"),
                gpu_count=1,
                window_start=H,
                window_end=H_END,
                source="hourly",
            )
            await session.commit()
        assert second == Decimal("0.56")
        async with sm() as session:
            bill = (await session.execute(select(BillHourly))).scalar_one()
            w = (await session.execute(select(Wallet))).scalar_one()
        assert bill.seconds_used == 3000
        assert bill.amount == Decimal("1.40")
        assert w.balance == Decimal("98.60")

    async def test_concurrent_settlement_single_charge(self, sm):
        inst_id, _ = await seed_instance(sm, events=[ev(0, "creating", "running")])

        gate = asyncio.Barrier(4)

        async def run():
            await gate.wait()
            async with sm() as session:
                await upsert_hour_bill(
                    session,
                    instance_id=inst_id,
                    user_id=1,
                    unit_price=Decimal("2.0000"),
                    gpu_count=1,
                    hour_start=H,
                    seconds=3600,
                    source="hourly",
                )
                await session.commit()

        await asyncio.gather(run(), run(), run(), gate.wait())
        async with sm() as session:
            bills = (await session.execute(select(BillHourly))).scalars().all()
            w = (await session.execute(select(Wallet))).scalar_one()
        assert len(bills) == 1
        assert w.balance == Decimal("98.00")

    async def test_concurrent_growth_tops_up_delta_once(self, sm):
        """Concurrent increments of an already charged bill row: the difference is added once."""
        inst_id, _ = await seed_instance(sm, events=[ev(0, "creating", "running")])
        async with sm() as session:
            await upsert_hour_bill(
                session,
                instance_id=inst_id,
                user_id=1,
                unit_price=Decimal("2.0000"),
                gpu_count=1,
                hour_start=H,
                seconds=1800,
                source="hourly",
            )
            await session.commit()

        gate = asyncio.Barrier(4)

        async def topup() -> Decimal:
            await gate.wait()
            async with sm() as session:
                charged = await upsert_hour_bill(
                    session,
                    instance_id=inst_id,
                    user_id=1,
                    unit_price=Decimal("2.0000"),
                    gpu_count=1,
                    hour_start=H,
                    seconds=3600,
                    source="hourly",
                )
                await session.commit()
                return charged

        results = await asyncio.gather(topup(), topup(), topup(), gate.wait())
        assert sorted(results[:3]) == [Decimal("0.00"), Decimal("0.00"), Decimal("1.00")]
        async with sm() as session:
            bill = (await session.execute(select(BillHourly))).scalar_one()
            w = (await session.execute(select(Wallet))).scalar_one()
        assert bill.seconds_used == 3600
        assert bill.amount == Decimal("2.00")
        assert w.balance == Decimal("98.00")

    async def test_settlement_waits_for_inflight_transition(self, sm):
        """Settlement takes the instance row lock before reading events; seconds are computed only
        after the in-flight stop transaction commits."""
        inst_id, _ = await seed_instance(sm, events=[ev(-30, "creating", "running")])
        started = asyncio.Event()
        release = asyncio.Event()

        async def inflight_stop() -> None:
            async with sm() as session:
                await session.execute(
                    update(Instance)
                    .where(Instance.id == inst_id)
                    .values(status="stopping", version=Instance.version + 1)
                )
                session.add(
                    InstanceEvent(
                        instance_id=inst_id,
                        from_status="running",
                        to_status="stopping",
                        reason="user_stop",
                        actor="user",
                        created_at=H + timedelta(minutes=59.5),
                    )
                )
                await session.flush()
                started.set()
                await release.wait()
                await session.commit()

        async def settle() -> Decimal:
            await started.wait()
            asyncio.get_running_loop().call_later(0.2, release.set)
            async with sm() as session:
                charged = await settle_instance_window(
                    session,
                    instance_id=inst_id,
                    user_id=1,
                    unit_price=Decimal("3.6000"),
                    gpu_count=1,
                    window_start=H,
                    window_end=H_END,
                    source="hourly",
                )
                await session.commit()
                return charged

        stop_task = asyncio.create_task(inflight_stop())
        charged = await asyncio.wait_for(settle(), timeout=20)
        await stop_task

        async with sm() as session:
            bill = (await session.execute(select(BillHourly))).scalar_one()
        assert bill.seconds_used == 3570
        assert charged == Decimal("3.57")

    async def test_zero_seconds_no_bill(self, sm):
        inst_id, _ = await seed_instance(sm, events=[ev(5, None, "creating")])
        async with sm() as session:
            charged = await settle_instance_window(
                session,
                instance_id=inst_id,
                user_id=1,
                unit_price=Decimal("1.6800"),
                gpu_count=1,
                window_start=H,
                window_end=H_END,
                source="hourly",
            )
            await session.commit()
        assert charged == Decimal("0.00")
        async with sm() as session:
            assert (await session.execute(select(BillHourly))).scalar_one_or_none() is None


class TestHourlySettlementJob:
    async def test_settles_prev_hour_and_idempotent(self, sm):
        await seed_instance(
            sm, events=[ev(10, "creating", "running"), ev(40, "running", "stopping")]
        )
        at = H_END + timedelta(minutes=2)
        assert await settle_due_hours(sm, at=at) == 1
        assert await settle_due_hours(sm, at=at) == 0
        async with sm() as session:
            bill = (await session.execute(select(BillHourly))).scalar_one()
            w = (await session.execute(select(Wallet))).scalar_one()
            assert await get_watermark(session, "hourly") == H
        assert bill.seconds_used == 1800
        assert w.balance == Decimal("99.16")

    async def test_long_running_instance_without_window_events(self, sm):
        """Continuous running across hours (no event inside the window) is settled too."""
        await seed_instance(
            sm, events=[(H - timedelta(hours=5), "creating", "running")], status="running"
        )
        assert await settle_due_hours(sm, at=H_END + timedelta(minutes=2)) == 1
        async with sm() as session:
            bill = (await session.execute(select(BillHourly))).scalar_one()
        assert bill.seconds_used == 3600
        assert bill.amount == Decimal("1.68")

    async def test_multi_gpu_price(self, sm):
        await seed_instance(
            sm,
            price="3.0000",
            gpu_count=4,
            events=[(H - timedelta(hours=1), "creating", "running")],
            status="running",
        )
        await settle_due_hours(sm, at=H_END + timedelta(minutes=2))
        async with sm() as session:
            bill = (await session.execute(select(BillHourly))).scalar_one()
        assert bill.amount == Decimal("12.00")


class TestTinyDurationTail:
    async def test_seconds_rounding_to_zero_amount_no_crash(self, sm):
        """Amount rounding to 0.00 → the bill row stays without a debit, later top-ups count from
        0."""
        inst_id, _ = await seed_instance(
            sm, events=[ev(0, "creating", "running"), ev(5 / 60, "running", "stopping")]
        )
        async with sm() as session:
            charged = await settle_instance_window(
                session,
                instance_id=inst_id,
                user_id=1,
                unit_price=Decimal("1.6800"),
                gpu_count=1,
                window_start=H,
                window_end=H + timedelta(seconds=5),
                source="tail",
            )
            await session.commit()
        assert charged == Decimal("0.00")
        async with sm() as session:
            bill = (await session.execute(select(BillHourly))).scalar_one()
            entries = (await session.execute(select(BalanceLedger))).scalars().all()
        assert bill.amount == Decimal("0.00")
        assert not [e for e in entries if e.type == "consume"]

        async with sm() as session:
            session.add_all(
                [
                    InstanceEvent(
                        instance_id=inst_id,
                        from_status="stopped",
                        to_status="running",
                        reason="seed",
                        actor="system",
                        created_at=H + timedelta(minutes=1),
                    ),
                    InstanceEvent(
                        instance_id=inst_id,
                        from_status="running",
                        to_status="stopping",
                        reason="seed",
                        actor="system",
                        created_at=H + timedelta(minutes=31),
                    ),
                ]
            )
            await session.commit()
        async with sm() as session:
            second = await settle_instance_window(
                session,
                instance_id=inst_id,
                user_id=1,
                unit_price=Decimal("1.6800"),
                gpu_count=1,
                window_start=H,
                window_end=H_END,
                source="hourly",
            )
            await session.commit()
        assert second == Decimal("0.84")


class TestCatchUpSettlement:
    """Catch-up after an outage across the clock hour: missed hours are filled, the amount equals
    continuous running."""

    async def test_missed_hours_are_caught_up(self, sm):
        await seed_instance(
            sm, events=[(H - timedelta(hours=1), "creating", "running")], status="running"
        )
        at = H + timedelta(hours=3, minutes=2)
        assert await settle_due_hours(sm, at=at) == 1
        at2 = H + timedelta(hours=6, minutes=2)
        assert await settle_due_hours(sm, at=at2) == 3
        async with sm() as session:
            bills = (
                (await session.execute(select(BillHourly).order_by(BillHourly.hour_start)))
                .scalars()
                .all()
            )
            w = (await session.execute(select(Wallet))).scalar_one()
        assert [b.hour_start.hour for b in bills] == [12, 13, 14, 15]
        assert all(b.seconds_used == 3600 for b in bills)
        assert w.balance == Decimal("100.00") - Decimal("1.68") * 4

    async def test_first_deploy_without_history_records_no_gap(self, sm):
        from app.modules.billing.models import SettlementGap
        from app.modules.billing.settlement import get_watermark

        await settle_due_hours(sm, at=H_END + timedelta(minutes=2))
        async with sm() as session:
            assert (await session.execute(select(SettlementGap))).scalars().all() == []
            assert await get_watermark(session, "hourly") == H

    async def test_watermark_missing_records_gap(self, sm):
        """Without a watermark a settlement_gaps row (watermark_missing) is recorded."""
        from app.modules.billing.models import SettlementGap

        await seed_instance(
            sm, events=[(H - timedelta(hours=1), "creating", "running")], status="running"
        )
        at = H_END + timedelta(minutes=2)
        await settle_due_hours(sm, at=at)
        async with sm() as session:
            gap = (
                await session.execute(
                    select(SettlementGap).where(SettlementGap.reason == "watermark_missing")
                )
            ).scalar_one()
            assert gap.kind == "hourly"
        await settle_due_hours(sm, at=at)
        async with sm() as session:
            count = len(
                (
                    await session.execute(
                        select(SettlementGap).where(SettlementGap.reason == "watermark_missing")
                    )
                )
                .scalars()
                .all()
            )
        assert count == 1

    async def test_clock_skew_refuses_round(self, sm, monkeypatch):
        """The worker clock jumps forward beyond the threshold: this settlement round is refused."""
        from app.modules.billing import settlement as st

        await seed_instance(
            sm, events=[(H - timedelta(hours=1), "creating", "running")], status="running"
        )
        monkeypatch.setattr(st, "now_utc", lambda: datetime.now(UTC) + timedelta(hours=1))
        assert await settle_due_hours(sm) == 0
        async with sm() as session:
            assert (await session.execute(select(BillHourly))).scalars().all() == []
            assert await get_watermark(session, "hourly") is None


@pytest.mark.parametrize(
    "anchor",
    [
        pytest.param(datetime(2026, 8, 31, 23, 0, tzinfo=UTC), id="cross-month"),
        pytest.param(datetime(2028, 2, 29, 23, 0, tzinfo=UTC), id="leap-day"),
    ],
)
class TestWindowBoundaries:
    """Settlement catch-up across month end / leap day (aware-UTC timedelta stepping)."""

    async def test_catchup_walks_over_boundary(self, sm, anchor):
        """The watermark catch-up crosses midnight / month end continuously."""
        inst_id, _ = await seed_instance(
            sm, events=[(anchor - timedelta(hours=2), "creating", "running")], status="running"
        )
        assert inst_id
        from app.modules.billing.settlement import _advance_watermark

        await _advance_watermark(sm, "hourly", anchor - timedelta(hours=2))
        await settle_due_hours(sm, at=anchor + timedelta(hours=1, minutes=2))
        async with sm() as session:
            hours = sorted(
                b.hour_start.replace(tzinfo=UTC)
                for b in (await session.execute(select(BillHourly))).scalars()
            )
            wm = await get_watermark(session, "hourly")
        assert hours == [anchor - timedelta(hours=1), anchor]
        assert wm == anchor


class TestOverdraftRefusal:
    """The allow_negative=False rejection path."""

    async def test_refusal_leaves_wallet_and_ledger_untouched(self, sm):
        from app.core.errors import AppError, ErrorCode

        async with sm() as session:
            await wallet.credit(session, 7, Decimal("1.00"), type_="recharge")
            await session.commit()
        async with sm() as session:
            with pytest.raises(AppError) as exc:
                await wallet.debit(
                    session, 7, Decimal("5.00"), type_="consume", allow_negative=False
                )
            assert exc.value.code is ErrorCode.INSUFFICIENT_BALANCE
            await session.rollback()
        async with sm() as session:
            w = (await session.execute(select(Wallet).where(Wallet.user_id == 7))).scalar_one()
            entries = (
                (await session.execute(select(BalanceLedger).where(BalanceLedger.user_id == 7)))
                .scalars()
                .all()
            )
        assert w.balance == Decimal("1.00")
        assert len(entries) == 1


class TestNodeLostBillingTruncation:
    """node_lost/pod_lost: billing truncated at metadata.unready_since, the grace observation period
    is not billed."""

    async def test_reconstruction_truncates_at_unready_since(self, sm):
        unready = H + timedelta(minutes=5)
        inst_id, _ = await seed_instance(
            sm,
            status="failed",
            events=[
                ev(-30, "creating", "running"),
                evm(15, "running", "failed", {"unready_since": unready.isoformat()}),
            ],
        )
        async with sm() as session:
            charged = await settle_instance_window(
                session,
                instance_id=inst_id,
                user_id=1,
                unit_price=Decimal("1.6800"),
                gpu_count=1,
                window_start=H,
                window_end=H_END,
                source="hourly",
            )
            await session.commit()
        assert charged == Decimal("0.14")
        async with sm() as session:
            bill = (await session.execute(select(BillHourly))).scalar_one()
        assert bill.seconds_used == 300
        assert bill.amount == Decimal("0.14")

    async def test_tail_listener_truncates_and_marks_detail(self, sm):
        """Tail bill (same transaction as the transition): truncated window + the truncation basis
        kept in bills_hourly.detail."""
        from app.modules.billing.edge_listener import on_instance_transition

        unready = H + timedelta(minutes=5)
        inst_id, _ = await seed_instance(
            sm, status="failed", events=[ev(-30, "creating", "running")]
        )
        async with sm() as session:
            inst = await session.get(Instance, inst_id)
            assert inst is not None
            event = InstanceEvent(
                instance_id=inst_id,
                from_status="running",
                to_status="failed",
                reason="node_lost",
                actor="system",
                event_metadata={"unready_since": unready.isoformat()},
                created_at=H + timedelta(minutes=15),
            )
            session.add(event)
            await on_instance_transition(session, inst, event)
            await session.commit()
        async with sm() as session:
            bill = (await session.execute(select(BillHourly))).scalar_one()
            w = (await session.execute(select(Wallet))).scalar_one()
        assert bill.seconds_used == 300
        assert bill.amount == Decimal("0.14")
        assert w.balance == Decimal("99.86")
        assert bill.detail is not None
        assert bill.detail["truncate_reason"] == "node_lost"
        assert bill.detail["truncated_at"] == unready.isoformat()

        async with sm() as session:
            topup = await settle_instance_window(
                session,
                instance_id=inst_id,
                user_id=1,
                unit_price=Decimal("1.6800"),
                gpu_count=1,
                window_start=H,
                window_end=H_END,
                source="hourly",
            )
            await session.commit()
        assert topup == Decimal("0.00")

    async def test_pod_lost_without_unready_bills_to_event(self, sm):
        """pod_lost with an empty unready_since: settled to the event time, no truncation."""
        from app.modules.billing.edge_listener import on_instance_transition

        inst_id, _ = await seed_instance(
            sm, status="failed", events=[ev(-30, "creating", "running")]
        )
        async with sm() as session:
            inst = await session.get(Instance, inst_id)
            assert inst is not None and inst.unready_since is None
            event = InstanceEvent(
                instance_id=inst_id,
                from_status="running",
                to_status="failed",
                reason="pod_lost",
                actor="system",
                event_metadata={"phase": "Missing", "ready": False},
                created_at=H + timedelta(minutes=15),
            )
            session.add(event)
            await on_instance_transition(session, inst, event)
            await session.commit()
        async with sm() as session:
            bill = (await session.execute(select(BillHourly))).scalar_one()
        assert bill.seconds_used == 900
        assert bill.detail is not None and "truncated_at" not in bill.detail


class TestLateFaultCorrection:
    async def _billed_instance(self, sm, *, anchor=H, price="1.6800", gpu_count=1):
        instance_id, _ = await seed_instance(
            sm,
            price=price,
            gpu_count=gpu_count,
            status="running",
            events=[(anchor, "creating", "running")],
        )
        async with sm() as session:
            for offset in range(3):
                start = anchor + timedelta(hours=offset)
                await settle_instance_window(
                    session,
                    instance_id=instance_id,
                    user_id=1,
                    unit_price=Decimal(price),
                    gpu_count=gpu_count,
                    window_start=start,
                    window_end=start + timedelta(hours=1),
                    source="hourly",
                )
            await session.commit()
        return instance_id

    async def _fault(self, session, instance_id, monkeypatch, *, cutoff, at):
        monkeypatch.setattr(transitions, "now_utc", lambda: at)
        monkeypatch.setattr(
            transitions, "_transition_listeners", [edge_listener.on_instance_transition]
        )
        instance = await session.get(Instance, instance_id)
        assert instance is not None
        return await transitions.transition(
            session,
            instance,
            "failed",
            reason="node_lost",
            actor="system",
            metadata={"unready_since": cutoff.isoformat()},
        )

    @pytest.mark.parametrize(
        ("anchor", "gpu_count", "market"),
        [
            (H, 0, "on_demand"),
            (datetime(2026, 8, 31, 23, tzinfo=UTC), 4, "spot"),
            (H, 1, "subscription"),
        ],
        ids=["cpu", "multi-gpu-cross-month", "historical-on-demand-after-conversion"],
    )
    async def test_multi_hour_refund_uses_bill_snapshot_and_reconciles(
        self, sm, monkeypatch, anchor, gpu_count, market
    ):
        instance_id = await self._billed_instance(sm, anchor=anchor, gpu_count=gpu_count)
        cutoff = anchor + timedelta(minutes=30)
        async with sm() as session:
            instance = await session.get(Instance, instance_id)
            assert instance is not None
            instance.price_hourly = Decimal("99.0000")
            instance.gpu_count = 8
            instance.market = market
            locked = await wallet.lock_wallet(session, 1)
            locked.frozen = Decimal("200.00")
            event = await self._fault(
                session, instance_id, monkeypatch, cutoff=cutoff, at=anchor + timedelta(hours=3)
            )
            await session.commit()
            event_id = event.id
        async with sm() as session:
            bills = (
                (await session.execute(select(BillHourly).order_by(BillHourly.hour_start)))
                .scalars()
                .all()
            )
            assert [b.seconds_used for b in bills] == [1800, 0, 0]
            expected = bill_amount(Decimal("1.6800"), gpu_count, 1800)
            assert [b.amount for b in bills] == [expected, Decimal("0.00"), Decimal("0.00")]
            for bill in bills:
                assert bill.unit_price == Decimal("1.6800") and bill.gpu_count == gpu_count
                assert bill.detail is not None
                correction = bill.detail["fault_corrections"][str(event_id)]
                assert correction["before_seconds"] == 3600
                assert Decimal(correction["before_amount"]) == expected * 2
                assert correction["after_seconds"] == bill.seconds_used
            entries = (
                (await session.execute(select(BalanceLedger).order_by(BalanceLedger.id)))
                .scalars()
                .all()
            )
            assert len([e for e in entries if e.type == "consume" and e.amount < 0]) == 3
            refunds = [e for e in entries if e.type == "refund"]
            assert len(refunds) == 3
            assert {e.ref_id for e in refunds} == {str(b.id) for b in bills}
            assert all(
                e.amount > 0 and e.remark is not None and str(event_id) in e.remark for e in refunds
            )
            assert await wallet.get_balance(session, 1) == Decimal("100.00") - expected
            locked = await wallet.lock_wallet(session, 1)
            assert locked.frozen == Decimal("200.00")
            assert await wallet.consumed_by_user(session, [1]) == {1: expected}
            assert await reconcile.bills_vs_consume(
                session, anchor, anchor + timedelta(hours=3)
            ) == (expected, expected)
            summary = await wallet.consumption_summary(
                session, 1, anchor, anchor + timedelta(hours=3)
            )
            assert summary.gpu_total == expected
            assert await reconcile.bills_vs_consume(
                session, anchor, anchor + timedelta(hours=1)
            ) == (expected, expected)
            assert await reconcile.bills_vs_consume(
                session, anchor + timedelta(hours=1), anchor + timedelta(hours=3)
            ) == (0, 0)
        assert await reconcile.wallet_ledger_chain_check(sm) == []

    async def test_concurrent_event_replay_and_hourly_settlement_do_not_recharge(
        self, sm, monkeypatch
    ):
        instance_id = await self._billed_instance(sm)
        async with sm() as session:
            event = await self._fault(
                session, instance_id, monkeypatch, cutoff=H, at=H + timedelta(hours=3)
            )
            await session.commit()
            event_id = event.id
        gate = asyncio.Barrier(3)

        async def replay():
            async with sm() as session:
                instance = await session.get(Instance, instance_id)
                event = await session.get(InstanceEvent, event_id)
                assert instance is not None and event is not None
                await gate.wait()
                await edge_listener.on_instance_transition(session, instance, event)
                for offset in range(3):
                    start = H + timedelta(hours=offset)
                    assert (
                        await settle_instance_window(
                            session,
                            instance_id=instance_id,
                            user_id=1,
                            unit_price=Decimal("1.6800"),
                            gpu_count=1,
                            window_start=start,
                            window_end=start + timedelta(hours=1),
                            source="gap_replay",
                        )
                        == 0
                    )
                await session.commit()

        await asyncio.wait_for(asyncio.gather(replay(), replay(), replay()), timeout=20)
        async with sm() as session:
            assert await wallet.get_balance(session, 1) == Decimal("100.00")
            refunds = (
                (await session.execute(select(BalanceLedger).where(BalanceLedger.type == "refund")))
                .scalars()
                .all()
            )
            assert len(refunds) == 3
            assert await reconcile.bills_vs_consume(session, H, H + timedelta(hours=3)) == (0, 0)

    async def test_posting_failure_rolls_back_event_all_bills_and_wallet(self, sm, monkeypatch):
        instance_id = await self._billed_instance(sm)
        original_credit = wallet.credit
        calls = 0

        async def failing_credit(*args, **kwargs):
            nonlocal calls
            result = await original_credit(*args, **kwargs)
            calls += 1
            if calls == 2:
                raise RuntimeError("injected refund failure")
            return result

        monkeypatch.setattr(wallet, "credit", failing_credit)
        async with sm() as session:
            with pytest.raises(RuntimeError, match="injected refund failure"):
                await self._fault(
                    session, instance_id, monkeypatch, cutoff=H, at=H + timedelta(hours=3)
                )
            await session.rollback()
        async with sm() as session:
            instance = await session.get(Instance, instance_id)
            assert instance is not None and instance.status == "running"
            bills = (await session.execute(select(BillHourly))).scalars().all()
            assert all(
                b.seconds_used == 3600 and "fault_corrections" not in (b.detail or {})
                for b in bills
            )
            assert await wallet.get_balance(session, 1) == Decimal("94.96")
            events = (await session.execute(select(InstanceEvent))).scalars().all()
            assert len(events) == 1
        monkeypatch.setattr(wallet, "credit", original_credit)
        async with sm() as session:
            await self._fault(
                session, instance_id, monkeypatch, cutoff=H, at=H + timedelta(hours=3)
            )
            await session.commit()
            assert await wallet.get_balance(session, 1) == Decimal("100.00")

    async def test_unrelated_refunds_do_not_offset_consumption(self, sm, monkeypatch):
        instance_id = await self._billed_instance(sm)
        async with sm() as session:
            await self._fault(
                session, instance_id, monkeypatch, cutoff=H_END, at=H + timedelta(hours=3)
            )
            await wallet.debit(
                session,
                1,
                Decimal("5.00"),
                type_="refund",
                ref_type="refund_request",
                ref_id="123",
                allow_negative=False,
            )
            await wallet.credit(
                session, 1, Decimal("2.00"), type_="refund", ref_type="order", ref_id="456"
            )
            await session.commit()
            assert await wallet.consumed_by_user(session, [1]) == {1: Decimal("1.68")}
            assert await reconcile.bills_vs_consume(session, H, H + timedelta(hours=3)) == (
                Decimal("1.68"),
                Decimal("1.68"),
            )
            assert await reconcile.dangling_consume_refs(session) == 0
            await wallet.credit(
                session,
                1,
                Decimal("0.01"),
                type_="refund",
                ref_type="bill_hourly",
                ref_id="999999999",
            )
            await session.flush()
            assert await reconcile.dangling_consume_refs(session) == 1

    async def test_second_fault_keeps_prior_run_and_each_event_is_idempotent(self, sm, monkeypatch):
        instance_id, _ = await seed_instance(
            sm, status="running", events=[ev(0, "creating", "running")]
        )
        async with sm() as session:
            faults: list[InstanceEvent] = []
            for minute, cutoff_minute in [(20, 10), (50, 40)]:
                await settle_instance_window(
                    session,
                    instance_id=instance_id,
                    user_id=1,
                    unit_price=Decimal("1.6800"),
                    gpu_count=1,
                    window_start=H,
                    window_end=H + timedelta(minutes=minute),
                    source="hourly",
                )
                event = await self._fault(
                    session,
                    instance_id,
                    monkeypatch,
                    cutoff=H + timedelta(minutes=cutoff_minute),
                    at=H + timedelta(minutes=minute + 5),
                )
                faults.append(event)
                if len(faults) == 1:
                    instance = await session.get(Instance, instance_id)
                    assert instance is not None
                    monkeypatch.setattr(transitions, "now_utc", lambda: H + timedelta(minutes=30))
                    for status in ("stopped", "starting", "running"):
                        await transitions.transition(
                            session, instance, status, reason="test_restart", actor="system"
                        )
            await session.commit()
            instance = await session.get(Instance, instance_id)
            assert instance is not None
            for event in faults:
                await edge_listener.on_instance_transition(session, instance, event)
            await session.commit()
            bill = (await session.execute(select(BillHourly))).scalar_one()
            assert bill.seconds_used == 1200 and bill.amount == Decimal("0.56")
            assert bill.detail is not None
            assert len(bill.detail["fault_corrections"]) == 2
            refunds = (
                (await session.execute(select(BalanceLedger).where(BalanceLedger.type == "refund")))
                .scalars()
                .all()
            )
            assert [entry.amount for entry in refunds] == [Decimal("0.28"), Decimal("0.28")]
            assert await wallet.get_balance(session, 1) == Decimal("99.44")
            assert await reconcile.bills_vs_consume(session, H, H_END) == (
                Decimal("0.56"),
                Decimal("0.56"),
            )


class TestLateFaultCorrectionUnit:
    async def test_real_correction_and_wallet_credit_rebuild_every_window(self, monkeypatch):
        """No Docker: mock only I/O; execute reconstruction, correction and wallet posting."""
        cutoff = H + timedelta(minutes=30)
        bills = [
            BillHourly(
                id=index + 1,
                instance_id=7,
                user_id=1,
                hour_start=H + timedelta(hours=index),
                seconds_used=3600,
                unit_price=Decimal("1.6800"),
                gpu_count=1,
                amount=Decimal("1.68"),
                detail={"source": "hourly"},
            )
            for index in range(3)
        ]
        result = MagicMock()
        result.scalars.return_value.all.return_value = bills
        session = MagicMock(execute=AsyncMock(return_value=result))
        lock = AsyncMock()
        monkeypatch.setattr(settlement.orchestrator_queries, "lock_instance_for_billing", lock)
        monkeypatch.setattr(
            settlement.orchestrator_queries,
            "billing_events",
            AsyncMock(
                return_value=[
                    (H, "creating", "running", None),
                    (
                        H + timedelta(hours=3),
                        "running",
                        "failed",
                        {"unready_since": cutoff.isoformat()},
                    ),
                ]
            ),
        )
        balance = Wallet(user_id=1, balance=Decimal("94.96"), frozen=Decimal("100.00"))
        monkeypatch.setattr(wallet, "lock_wallet", AsyncMock(return_value=balance))
        for expected in (Decimal("4.20"), Decimal("0.00")):
            assert (
                await settlement.correct_fault_bills(
                    session,
                    instance_id=7,
                    event_id=42,
                    cutoff=cutoff,
                    edge_at=H + timedelta(hours=3),
                    reason="node_lost",
                )
                == expected
            )
        lock.assert_awaited_with(session, 7)
        assert [b.seconds_used for b in bills] == [1800, 0, 0]
        assert [b.amount for b in bills] == [Decimal("0.84"), Decimal("0.00"), Decimal("0.00")]
        assert balance.balance == Decimal("99.16") and balance.frozen == Decimal("100.00")
        entries = [call.args[0] for call in session.add.call_args_list]
        assert [e.amount for e in entries] == [Decimal("0.84"), Decimal("1.68"), Decimal("1.68")]
        assert all(e.type == "refund" and e.ref_type == "bill_hourly" for e in entries)
        assert [e.balance_after for e in entries] == [
            Decimal("95.80"),
            Decimal("97.48"),
            Decimal("99.16"),
        ]
        query = session.execute.call_args.args[0]
        assert "FOR UPDATE" in str(query)
        assert query.get_execution_options()["populate_existing"] is True

    @pytest.mark.parametrize(("seconds", "price", "gpu_count"), [(2, "1.68", 0), (3, "0.01", 4)])
    async def test_zero_money_correction_keeps_seconds_and_event_marker(
        self, monkeypatch, seconds, price, gpu_count
    ):
        row = BillHourly(
            id=1,
            user_id=1,
            seconds_used=seconds,
            unit_price=Decimal(price),
            gpu_count=gpu_count,
            amount=Decimal("0.00"),
            detail={"source": "tail"},
        )
        credit = AsyncMock()
        monkeypatch.setattr(wallet, "credit", credit)
        session = MagicMock()
        assert (
            await settlement._correct_fault_bill(
                session, row, seconds=0, event_id=42, cutoff=H, reason="pod_lost"
            )
            == 0
        )
        assert row.seconds_used == 0 and row.amount == 0
        assert row.detail is not None
        assert row.detail["fault_corrections"]["42"]["before_seconds"] == seconds
        credit.assert_not_awaited()

    @pytest.mark.parametrize(
        ("before_seconds", "after_seconds", "before_amount", "after_amount", "refund"),
        [(900, 899, "0.12", "0.12", "0.00"), (1800, 900, "0.25", "0.12", "0.13")],
    )
    async def test_refund_is_difference_of_rounded_totals(
        self, monkeypatch, before_seconds, after_seconds, before_amount, after_amount, refund
    ):
        row = BillHourly(
            id=1,
            user_id=1,
            seconds_used=before_seconds,
            unit_price=Decimal("0.5000"),
            gpu_count=1,
            amount=Decimal(before_amount),
            detail={"source": "tail"},
        )
        credit = AsyncMock()
        monkeypatch.setattr(wallet, "credit", credit)
        session = MagicMock()
        assert await settlement._correct_fault_bill(
            session, row, seconds=after_seconds, event_id=42, cutoff=H, reason="pod_lost"
        ) == Decimal(refund)
        assert row.seconds_used == after_seconds and row.amount == Decimal(after_amount)
        if Decimal(refund):
            assert credit.await_args is not None
            assert credit.await_args.args[2] == Decimal(refund)
        else:
            credit.assert_not_awaited()
        assert row.detail is not None and "42" in row.detail["fault_corrections"]

    @pytest.mark.parametrize(
        ("actor", "reason", "to_status", "market", "has_cutoff", "expected"),
        [
            ("system", "node_lost", "failed", "on_demand", True, True),
            ("system", "pod_lost", "failed", "spot", True, True),
            ("user", "node_lost", "failed", "on_demand", True, False),
            ("admin", "pod_lost", "failed", "on_demand", True, False),
            ("system", "pod_unready", "failed", "on_demand", True, False),
            ("system", "node_lost", "stopping", "on_demand", True, False),
            ("system", "node_lost", "failed", "subscription", True, True),
            ("system", "pod_lost", "failed", "on_demand", False, False),
        ],
    )
    async def test_only_trusted_fault_listener_authorizes_refund(
        self, monkeypatch, actor, reason, to_status, market, has_cutoff, expected
    ):
        instance = Instance(
            id=7, user_id=1, market=market, price_hourly=Decimal("1.68"), gpu_count=1
        )
        event = InstanceEvent(
            id=42,
            instance_id=7,
            from_status="running",
            to_status=to_status,
            actor=actor,
            reason=reason,
            created_at=H_END,
            event_metadata={"unready_since": H.isoformat()} if has_cutoff else None,
        )
        correction = AsyncMock(return_value=Decimal("0.00"))
        monkeypatch.setattr(edge_listener, "correct_fault_bills", correction)
        monkeypatch.setattr(
            edge_listener, "settle_instance_window", AsyncMock(return_value=Decimal("0.00"))
        )
        await edge_listener.on_instance_transition(AsyncMock(), instance, event)
        assert correction.await_count == int(expected)

    async def test_ordinary_upsert_never_refunds_a_smaller_rebuild(self, monkeypatch):
        row = BillHourly(seconds_used=3600, amount=Decimal("1.68"))
        insert_result = MagicMock()
        insert_result.scalar_one_or_none.return_value = None
        locked_result = MagicMock()
        locked_result.scalar_one.return_value = row
        session = MagicMock(execute=AsyncMock(side_effect=[insert_result, locked_result]))
        credit = AsyncMock()
        monkeypatch.setattr(wallet, "credit", credit)
        assert (
            await upsert_hour_bill(
                session,
                instance_id=7,
                user_id=1,
                unit_price=Decimal("1.68"),
                gpu_count=1,
                hour_start=H,
                seconds=1800,
                source="hourly",
            )
            == 0
        )
        assert row.seconds_used == 3600 and row.amount == Decimal("1.68")
        credit.assert_not_awaited()


class TestGapClosure:
    """Settlement gap loop: recorded → visible to admins → replay settles / manual write-off →
    resolved_at written."""

    async def _make_gap(
        self, sm, *, kind="hourly", window_start=H, object_id=0, reason="dead_letter"
    ) -> int:
        from app.modules.billing.models import SettlementGap

        async with sm() as session:
            gap = SettlementGap(
                kind=kind, window_start=window_start, object_id=object_id, reason=reason
            )
            session.add(gap)
            await session.commit()
            return int(gap.id)

    async def test_replay_single_hourly_gap_settles_and_resolves(self, sm):
        from app.modules.billing.settlement import replay_gap

        inst_id, _ = await seed_instance(
            sm, events=[ev(10, "creating", "running"), ev(40, "running", "stopping")]
        )
        gap_id = await self._make_gap(sm, object_id=inst_id)
        out = await replay_gap(sm, gap_id, operator_id=1)
        assert out.resolved_at is not None
        async with sm() as session:
            bill = (await session.execute(select(BillHourly))).scalar_one()
            w = (await session.execute(select(Wallet))).scalar_one()
        assert bill.hour_start == H and bill.seconds_used == 1800
        assert bill.detail.get("gap_id") == gap_id
        assert w.balance == Decimal("100.00") - Decimal("0.84")

    async def test_replay_is_idempotent_no_double_charge(self, sm):
        from app.modules.billing.settlement import replay_gap

        inst_id, _ = await seed_instance(
            sm, events=[ev(10, "creating", "running"), ev(40, "running", "stopping")]
        )
        gap_id = await self._make_gap(sm, object_id=inst_id)
        await replay_gap(sm, gap_id, operator_id=1)
        again = await replay_gap(sm, gap_id, operator_id=1)
        assert again.resolved_at is not None
        async with sm() as session:
            w = (await session.execute(select(Wallet))).scalar_one()
        assert w.balance == Decimal("100.00") - Decimal("0.84")

    async def test_replay_whole_window_gap_covers_all_candidates(self, sm):
        """A whole-window gap (catchup_truncated, object_id=0): every candidate of the window is
        replayed."""
        from app.modules.billing.settlement import replay_gap

        await seed_instance(
            sm, events=[ev(10, "creating", "running"), ev(40, "running", "stopping")]
        )
        await seed_instance(
            sm, user_id=2, events=[ev(-30, "creating", "running")], status="running"
        )
        gap_id = await self._make_gap(sm, object_id=0, reason="catchup_truncated")
        out = await replay_gap(sm, gap_id, operator_id=1)
        assert out.resolved_at is not None
        async with sm() as session:
            bills = (await session.execute(select(BillHourly))).scalars().all()
        assert len(bills) == 2

    async def test_replay_object_gone_conflict(self, sm):
        from app.core.errors import AppError
        from app.modules.billing.settlement import replay_gap

        gap_id = await self._make_gap(sm, object_id=999999)
        with pytest.raises(AppError) as exc_info:
            await replay_gap(sm, gap_id, operator_id=1)
        assert exc_info.value.message_key == "billing.settlementGapObjectGone"

    async def test_unresolved_gauge_reflects_db(self, sm):
        """Unresolved gap gauge > 0, back to zero after the write-off."""
        from app.core.metrics import SETTLEMENT_GAP_UNRESOLVED
        from app.modules.billing.settlement import resolve_gap

        gap_id = await self._make_gap(sm)
        async with sm() as session:
            from app.modules.billing.settlement import _refresh_gap_gauge

            await _refresh_gap_gauge(session)
        assert SETTLEMENT_GAP_UNRESOLVED.labels(kind="hourly")._value.get() == 1
        async with sm() as session:
            gap = await resolve_gap(session, gap_id, note="written off", operator_id=1)
            assert gap.resolved_at is not None
        assert SETTLEMENT_GAP_UNRESOLVED.labels(kind="hourly")._value.get() == 0


class TestWholeWindowReplayFailures:
    @pytest.mark.parametrize("kind", ["hourly", "daily_disk"])
    @pytest.mark.parametrize("resolved_child", [False, True])
    async def test_failure_retains_parent_until_success(
        self, sm, monkeypatch, kind, resolved_child
    ):
        """First failures survive retries/restarts; successful objects are not charged again."""
        from app.core.errors import AppError
        from app.core.timeutil import billing_day_floor
        from app.modules.billing import settlement
        from app.modules.billing.models import BillDailyDisk, SettlementGap
        from tests.helpers import fund_wallet, seed_disk

        window = H if kind == "hourly" else billing_day_floor(H)
        if kind == "hourly":
            await seed_instance(sm, user_id=1, status="running", events=[ev(0, None, "running")])
            bad_id, _ = await seed_instance(
                sm, user_id=2, status="running", events=[ev(0, None, "running")]
            )
            bill_model = BillHourly
        else:
            await seed_disk(sm, 1, created_at=window)
            bad_id, _ = await seed_disk(sm, 2, created_at=window)
            await fund_wallet(sm, 1)
            await fund_wallet(sm, 2)
            bill_model = BillDailyDisk
        async with sm() as session:
            gap = SettlementGap(
                kind=kind, window_start=window, object_id=0, reason="catchup_truncated"
            )
            session.add(gap)
            if resolved_child:
                session.add(
                    SettlementGap(
                        kind=kind,
                        window_start=window,
                        object_id=bad_id,
                        reason="dead_letter",
                        resolved_at=H_END,
                    )
                )
            await session.commit()
            gap_id = gap.id

        original = settlement._charge_bill
        failing = True

        async def fail_one(session, user_id, amount, **kwargs):
            if failing and user_id == 2:
                raise RuntimeError("injected posting failure")
            await original(session, user_id, amount, **kwargs)

        monkeypatch.setattr(settlement, "_charge_bill", fail_one)
        monkeypatch.setattr(settlement, "_failure_streaks", {})
        for _ in range(settlement.DEAD_LETTER_AFTER + 1):
            with pytest.raises(AppError) as exc:
                await settlement.replay_gap(sm, gap_id, operator_id=1)
            assert exc.value.message_key == "common.retryableConflict"
            async with sm() as session:
                parent = await session.get(SettlementGap, gap_id)
                assert parent is not None and parent.resolved_at is None
                bills = (await session.execute(select(bill_model))).scalars().all()
                assert len(bills) == 1 and bills[0].user_id == 1
                assert await wallet.get_balance(session, 2) == Decimal("100.00")
            settlement._failure_streaks.clear()

        failing = False
        results = await asyncio.wait_for(
            asyncio.gather(*(settlement.replay_gap(sm, gap_id, operator_id=1) for _ in range(3))),
            timeout=20,
        )
        assert all(result.resolved_at is not None for result in results)
        async with sm() as session:
            bills = (await session.execute(select(bill_model))).scalars().all()
            entries = (
                (
                    await session.execute(
                        select(BalanceLedger).where(BalanceLedger.type == "consume")
                    )
                )
                .scalars()
                .all()
            )
            assert len(bills) == len(entries) == 2
            for user_id in (1, 2):
                amount = next(b.amount for b in bills if b.user_id == user_id)
                assert await wallet.get_balance(session, user_id) == Decimal("100.00") - amount


class TestReplayFailureUnit:
    @pytest.mark.parametrize("kind", ["hourly", "daily_disk"])
    async def test_replay_returns_every_failure_without_dead_lettering(self, monkeypatch, kind):
        """No Docker: execute the real replay/attempt loop, including a pre-existing streak."""
        from unittest.mock import AsyncMock, MagicMock

        from app.core.errors import AppError
        from app.modules.billing import settlement

        session = AsyncMock()
        sm = MagicMock()
        sm.return_value.__aenter__.return_value = session
        good = AsyncMock(return_value=Decimal("1.00"))
        bad = AsyncMock(side_effect=RuntimeError("injected posting failure"))
        attempts = [(1, good), (2, bad)]
        monkeypatch.setattr(settlement, "_hourly_window_attempts", AsyncMock(return_value=attempts))
        monkeypatch.setattr(settlement, "_billable_disk_rows", AsyncMock(return_value=[]))
        monkeypatch.setattr(
            settlement, "_daily_disk_window_attempts", AsyncMock(return_value=attempts)
        )
        record = AsyncMock()
        monkeypatch.setattr(settlement, "_record_gaps", record)
        monkeypatch.setattr(
            settlement, "_failure_streaks", {(kind, H, 2): settlement.DEAD_LETTER_AFTER}
        )
        with pytest.raises(AppError) as exc:
            if kind == "hourly":
                await settlement._replay_hourly_gap(sm, H, 0, detail_extra={})
            else:
                await settlement._replay_daily_disk_gap(sm, H, 0)
        assert exc.value.message_key == "common.retryableConflict"
        good.assert_awaited_once_with(session)
        bad.assert_awaited_once_with(session)
        session.commit.assert_awaited_once()
        record.assert_not_awaited()


class TestGapEndpoints:
    """Admin gap endpoints: list filters + role gate + replay / write-off write the audit."""

    async def test_list_replay_resolve_flow(self, client, sm):
        from app.modules.billing.models import SettlementGap

        inst_id, _ = await seed_instance(
            sm, events=[ev(10, "creating", "running"), ev(40, "running", "stopping")]
        )
        async with sm() as session:
            session.add(
                SettlementGap(
                    kind="hourly", window_start=H, object_id=inst_id, reason="dead_letter"
                )
            )
            session.add(
                SettlementGap(
                    kind="daily_disk",
                    window_start=H,
                    object_id=1,
                    reason="grace_overlap",
                )
            )
            await session.commit()
        finance = await admin_headers(sm, client, role="finance")
        rows = (await client.get("/api/admin/v1/finance/settlement-gaps", headers=finance)).json()
        assert len(rows["items"]) == 2
        rows = (
            await client.get(
                "/api/admin/v1/finance/settlement-gaps",
                params={"kind": "hourly"},
                headers=finance,
            )
        ).json()
        assert len(rows["items"]) == 1
        gid = rows["items"][0]["id"]
        resp = await client.post(
            f"/api/admin/v1/finance/settlement-gaps/{gid}/replay", headers=finance
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["resolved_at"] is not None
        gid2 = (
            await client.get(
                "/api/admin/v1/finance/settlement-gaps",
                params={"kind": "daily_disk"},
                headers=finance,
            )
        ).json()["items"][0]["id"]
        resp = await client.post(
            f"/api/admin/v1/finance/settlement-gaps/{gid2}/replay", headers=finance
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.settlementGapNotReplayable"
        resp = await client.post(
            f"/api/admin/v1/finance/settlement-gaps/{gid2}/resolve",
            json={"note": "deliberately unbilled during grace, confirmed no charge"},
            headers=finance,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["resolved_at"] is not None
        rows = (await client.get("/api/admin/v1/finance/settlement-gaps", headers=finance)).json()
        assert rows["items"] == []


class TestLagWindows:
    """The lag gauge counts windows: a DST day is one window, not 25/24 or 23/24."""

    def test_fixed_step_divides_elapsed_time(self):
        t0 = datetime(2026, 8, 19, 0, 0, tzinfo=UTC)
        assert _lag_windows(None, t0, timedelta(hours=1), None) == 0.0
        assert _lag_windows(t0, t0 + timedelta(hours=3), timedelta(hours=1), None) == 3.0
        assert _lag_windows(t0 + timedelta(hours=3), t0, timedelta(hours=1), None) == 0.0

    def test_calendar_shift_counts_steps(self, monkeypatch):
        from app.core.config import get_settings
        from app.core.timeutil import billing_day_floor, billing_day_shift

        monkeypatch.setattr(get_settings(), "billing_timezone", "America/New_York")
        fall = billing_day_floor(datetime(2026, 11, 1, 12, 0, tzinfo=UTC))
        nxt = billing_day_shift(fall, 1)
        assert (nxt - fall).total_seconds() == 25 * 3600
        assert _lag_windows(fall, nxt, timedelta(days=1), billing_day_shift) == 1.0
        assert (
            _lag_windows(fall, billing_day_shift(fall, 3), timedelta(days=1), billing_day_shift)
            == 3.0
        )
