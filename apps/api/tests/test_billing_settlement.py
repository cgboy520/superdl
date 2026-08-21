import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.billing import wallet
from app.modules.billing.models import BalanceLedger, BillHourly, Wallet
from app.modules.billing.settlement import (
    bill_amount,
    running_seconds_in_window,
    settle_instance_window,
    settle_previous_hour,
    upsert_hour_bill,
)
from app.modules.orchestrator.models import Instance, InstanceEvent

H = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)  # 结算窗口 [10:00, 11:00)
H_END = datetime(2026, 8, 19, 11, 0, tzinfo=UTC)


def ev(minute: float, from_s: str | None, to_s: str) -> tuple[datetime, str | None, str]:
    return (H + timedelta(minutes=minute), from_s, to_s)


class TestRunningSeconds:
    def test_full_hour(self):
        events = [ev(-120, None, "creating"), ev(-119, "creating", "running")]
        assert running_seconds_in_window(events, H, H_END) == 3600

    def test_enter_mid_window(self):
        events = [ev(-5, None, "creating"), ev(10, "creating", "running")]
        assert running_seconds_in_window(events, H, H_END) == 3000

    def test_enter_and_leave_inside(self):
        events = [ev(10, "creating", "running"), ev(40, "running", "stopping")]
        assert running_seconds_in_window(events, H, H_END) == 1800

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
        # 恰在窗口末进入 → 0;恰在窗口始离开 → 0
        assert running_seconds_in_window([ev(60, "starting", "running")], H, H_END) == 0
        events = [ev(-30, "creating", "running"), ev(0, "running", "stopping")]
        assert running_seconds_in_window(events, H, H_END) == 0

    def test_never_running(self):
        events = [ev(5, None, "creating"), ev(50, "creating", "failed")]
        assert running_seconds_in_window(events, H, H_END) == 0

    def test_events_after_window_ignored(self):
        events = [ev(10, "creating", "running"), ev(70, "running", "stopping")]
        assert running_seconds_in_window(events, H, H_END) == 3000


class TestBillAmount:
    def test_exact(self):
        assert bill_amount(Decimal("1.6800"), 1, 3600) == Decimal("1.68")
        assert bill_amount(Decimal("1.6800"), 2, 1800) == Decimal("1.68")

    def test_half_even(self):
        # 3.0000 × 1 × 3 / 3600 = 0.0025 → HALF_EVEN → 0.00
        assert bill_amount(Decimal("3.0000"), 1, 3) == Decimal("0.00")
        # 0.0075 → 0.01(7.5 → 8,向偶)
        assert bill_amount(Decimal("9.0000"), 1, 3) == Decimal("0.01")

    def test_zero(self):
        assert bill_amount(Decimal("9.9900"), 1, 0) == Decimal("0.00")

    def test_out_of_range_raises(self):
        # 窗口计算 bug 必须炸出来,不允许静默截断少扣/多扣
        with pytest.raises(ValueError):
            bill_amount(Decimal("1.0000"), 1, 3601)
        with pytest.raises(ValueError):
            bill_amount(Decimal("1.0000"), 1, -1)


async def seed_instance(
    sm: async_sessionmaker[AsyncSession],
    user_id: int = 1,
    price: str = "1.6800",
    gpu_count: int = 1,
    events: list[tuple[datetime, str | None, str]] | None = None,
    status: str = "stopped",
) -> int:
    """直接落库实例 + 事件(合成时间戳),返回 instance_id。"""
    async with sm() as session:
        inst = Instance(
            uuid=f"u{user_id}i{datetime.now(UTC).timestamp()}".replace(".", ""),
            user_id=user_id,
            name="t",
            sku_id=1,
            spec={
                "tier": "shared_std",
                "vram_gb": 8,
                "vcpu": 8,
                "mem_gb": 32,
                "disk_gb": 100,
                "pool_label": "hami",
                "gpu_cores_pct": 50,
            },
            price_hourly=Decimal(price),
            gpu_count=gpu_count,
            image_ref="img",
            status=status,
            k8s_namespace=f"tenant-{user_id}",
            jupyter_token="tok",
            authorized_keys=[],
        )
        session.add(inst)
        await session.flush()
        for ts, from_s, to_s in events or []:
            session.add(
                InstanceEvent(
                    instance_id=inst.id,
                    from_status=from_s,
                    to_status=to_s,
                    reason="seed",
                    actor="system",
                    created_at=ts,
                )
            )
        await wallet.credit(session, user_id, Decimal("100.00"), type_="recharge", remark="seed")
        await session.commit()
        return inst.id


class TestUpsertIdempotency:
    async def test_repeat_execution_no_double_charge(self, sm):
        inst_id = await seed_instance(
            sm, events=[ev(0, "creating", "running"), ev(30, "running", "stopping")]
        )
        for _ in range(3):  # 重复执行 3 次
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
        assert w.balance == Decimal("99.16")  # 只扣一次
        assert len([e for e in ledger if e.type == "consume"]) == 1

    async def test_growth_tops_up_delta(self, sm):
        """同小时先尾账(30min)后整点结算(50min)→ 只补差价。"""
        inst_id = await seed_instance(
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

        # 后续又跑了 20 分钟(30~50 重新 running)
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
        # 总 50min = 3000s → 1.40;已收 0.84 → 补 0.56
        assert second == Decimal("0.56")
        async with sm() as session:
            bill = (await session.execute(select(BillHourly))).scalar_one()
            w = (await session.execute(select(Wallet))).scalar_one()
        assert bill.seconds_used == 3000
        assert bill.amount == Decimal("1.40")
        assert w.balance == Decimal("98.60")

    async def test_concurrent_settlement_single_charge(self, sm):
        inst_id = await seed_instance(sm, events=[ev(0, "creating", "running")])

        async def run():
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

        await asyncio.gather(run(), run(), run())
        async with sm() as session:
            bills = (await session.execute(select(BillHourly))).scalars().all()
            w = (await session.execute(select(Wallet))).scalar_one()
        assert len(bills) == 1
        assert w.balance == Decimal("98.00")  # 100 - 2.00,并发只扣一次

    async def test_zero_seconds_no_bill(self, sm):
        inst_id = await seed_instance(sm, events=[ev(5, None, "creating")])
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
        # 上一小时相对 at=H_END+2min 即 [10:00,11:00)
        await seed_instance(
            sm, events=[ev(10, "creating", "running"), ev(40, "running", "stopping")]
        )
        at = H_END + timedelta(minutes=2)
        assert await settle_previous_hour(sm, at=at) == 1
        assert await settle_previous_hour(sm, at=at) == 0  # 幂等:零重复扣款
        async with sm() as session:
            bill = (await session.execute(select(BillHourly))).scalar_one()
            w = (await session.execute(select(Wallet))).scalar_one()
        assert bill.seconds_used == 1800
        assert w.balance == Decimal("99.16")

    async def test_long_running_instance_without_window_events(self, sm):
        """跨小时持续 running(窗口内无事件)也必须被结算。"""
        await seed_instance(sm, events=[(H - timedelta(hours=5), "creating", "running")])
        assert await settle_previous_hour(sm, at=H_END + timedelta(minutes=2)) == 1
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
        )
        await settle_previous_hour(sm, at=H_END + timedelta(minutes=2))
        async with sm() as session:
            bill = (await session.execute(select(BillHourly))).scalar_one()
        assert bill.amount == Decimal("12.00")  # 3.00 × 4 卡

    async def test_ledger_balance_chain_consistent(self, sm):
        await seed_instance(sm, events=[(H - timedelta(hours=1), "creating", "running")])
        await settle_previous_hour(sm, at=H_END + timedelta(minutes=2))
        async with sm() as session:
            entries = (
                (await session.execute(select(BalanceLedger).order_by(BalanceLedger.id)))
                .scalars()
                .all()
            )
            w = (await session.execute(select(Wallet))).scalar_one()
        running = Decimal("0.00")
        for e in entries:
            running += e.amount
            assert e.balance_after == running  # 每条 balance_after 快照自洽
        assert w.balance == running


class TestTinyDurationTail:
    async def test_seconds_rounding_to_zero_amount_no_crash(self, sm):
        """运行数秒即关机:金额舍入 0.00 → 留账单行不扣款,后续补差从 0 起算。"""
        inst_id = await seed_instance(
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

        # 同小时再跑 30 分钟 → 补差从 0 起,全额入账
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
