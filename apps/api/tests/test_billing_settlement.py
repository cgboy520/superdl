# pyright: reportPrivateUsage=false
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.modules.billing import wallet
from app.modules.billing.models import BalanceLedger, BillHourly, Wallet
from app.modules.billing.settlement import (
    bill_amount,
    get_watermark,
    running_seconds_in_window,
    settle_due_hours,
    settle_instance_window,
    upsert_hour_bill,
)
from app.modules.orchestrator.models import Instance, InstanceEvent
from tests.helpers import H_END, H, admin_headers, seed_instance


def ev(minute: float, from_s: str | None, to_s: str) -> tuple[datetime, str | None, str]:
    return (H + timedelta(minutes=minute), from_s, to_s)


def evm(
    minute: float, from_s: str | None, to_s: str, meta: dict
) -> tuple[datetime, str | None, str, dict]:
    """带 metadata 的事件。"""
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
        """微秒级事件:整数微秒累加 + HALF_EVEN 舍入。"""
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
        """金额恰好位于半分时按 HALF_EVEN 舍入。"""
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
        """同小时先尾账(30min)后整点结算(50min)→ 只补差价。"""
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
        """已 charged 的账单行并发增量补足:差价只加一次。"""
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
        """结算读事件前先拿实例行锁,在飞的关机事务提交后才算秒数。"""
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
        """跨小时持续 running(窗口内无事件)也被结算。"""
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
        """金额舍入 0.00 → 留账单行不扣款,后续补差从 0 起算。"""
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
    """停机跨整点后的追平:漏掉的小时补上,金额与连续运行一致。"""

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
        """无水位线时登记 settlement_gaps(watermark_missing)。"""
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
        """worker 时钟前跳超阈值:本轮结算拒绝执行。"""
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
    """结算追平跨月末/闰日(aware-UTC timedelta 递推)。"""

    async def test_catchup_walks_over_boundary(self, sm, anchor):
        """水位线追平连续跨过午夜/月末。"""
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
    """allow_negative=False 的拒绝路径。"""

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
    """node_lost/pod_lost:计费截断到 metadata.unready_since,宽限观察期不计费。"""

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
        """尾账(与迁移同事务):截断窗口 + bills_hourly.detail 留截断依据。"""
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
        """pod_lost 但 unready_since 为空:按事件时刻结算,不截断。"""
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


class TestGapClosure:
    """结算缺口闭环:登记 → 管理端可见 → 重放补结/人工核销 → resolved_at 回写。"""

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
        """object_id=0 的整窗缺口(catchup_truncated):对该窗全量候选重放。"""
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
        """缺口未核销 gauge>0,核销后归零。"""
        from app.core.metrics import SETTLEMENT_GAP_UNRESOLVED
        from app.modules.billing.settlement import resolve_gap

        gap_id = await self._make_gap(sm)
        async with sm() as session:
            from app.modules.billing.settlement import _refresh_gap_gauge

            await _refresh_gap_gauge(session)
        assert SETTLEMENT_GAP_UNRESOLVED.labels(kind="hourly")._value.get() == 1
        async with sm() as session:
            gap = await resolve_gap(session, gap_id, note="核销", operator_id=1)
            assert gap.resolved_at is not None
        assert SETTLEMENT_GAP_UNRESOLVED.labels(kind="hourly")._value.get() == 0


class TestGapEndpoints:
    """管理端缺口端点:列表过滤 + 角色门槛 + 重放/核销写审计。"""

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
            json={"note": "grace 期间有意不计费,确认无账"},
            headers=finance,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["resolved_at"] is not None
        rows = (await client.get("/api/admin/v1/finance/settlement-gaps", headers=finance)).json()
        assert rows["items"] == []
