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
    get_watermark,
    running_seconds_in_window,
    settle_due_hours,
    settle_instance_window,
    upsert_hour_bill,
)
from app.modules.orchestrator.models import Instance, InstanceEvent

H = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)  # 结算窗口 [10:00, 11:00)
H_END = datetime(2026, 8, 19, 11, 0, tzinfo=UTC)


def ev(minute: float, from_s: str | None, to_s: str) -> tuple[datetime, str | None, str]:
    return (H + timedelta(minutes=minute), from_s, to_s)


def evm(
    minute: float, from_s: str | None, to_s: str, meta: dict
) -> tuple[datetime, str | None, str, dict]:
    """带 metadata 的事件(失联截断用例用)。"""
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
        # 恰在窗口末进入 → 0;恰在窗口始离开 → 0
        assert running_seconds_in_window([ev(60, "starting", "running")], H, H_END) == 0
        events = [ev(-30, "creating", "running"), ev(0, "running", "stopping")]
        assert running_seconds_in_window(events, H, H_END) == 0

    def test_events_after_window_ignored(self):
        events = [ev(10, "creating", "running"), ev(70, "running", "stopping")]
        assert running_seconds_in_window(events, H, H_END) == 3000


class TestBillAmount:
    def test_exact(self):
        assert bill_amount(Decimal("1.6800"), 1, 3600) == Decimal("1.68")
        assert bill_amount(Decimal("1.6800"), 2, 1800) == Decimal("1.68")

    def test_half_even(self):
        """真正的分位 tie(第三位小数恰好是 5),才分得开三种 half 模式。

        入参必须落在 tie 上,否则三种 half 模式结果相同,用例形同虚设。
        """
        # 0.5000 × 1 × 900 / 3600 = 0.1250 → 向偶 → 0.12(HALF_UP 会给 0.13)
        assert bill_amount(Decimal("0.5000"), 1, 900) == Decimal("0.12")
        # 0.5400 × 1 × 900 / 3600 = 0.1350 → 向偶 → 0.14(HALF_DOWN 会给 0.13)
        assert bill_amount(Decimal("0.5400"), 1, 900) == Decimal("0.14")
        # 非 tie 的两侧仍要覆盖,排除 ROUND_UP / ROUND_DOWN
        assert bill_amount(Decimal("3.0000"), 1, 3) == Decimal("0.00")  # 0.0025
        assert bill_amount(Decimal("9.0000"), 1, 3) == Decimal("0.01")  # 0.0075

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
    events: list[tuple] | None = None,
    status: str = "stopped",
) -> int:
    """直接落库实例 + 事件(合成时间戳),返回 instance_id。

    events 元素:(ts, from, to) 或 (ts, from, to, metadata)。
    """
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
        for e in events or []:
            ts, from_s, to_s = e[0], e[1], e[2]
            session.add(
                InstanceEvent(
                    instance_id=inst.id,
                    from_status=from_s,
                    to_status=to_s,
                    reason="seed",
                    actor="system",
                    event_metadata=e[3] if len(e) > 3 else None,
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

    async def test_settlement_waits_for_inflight_transition(self, sm):
        """在飞的尾账事务对无锁读不可见 → 结算会把「已经停了的那半小时」算成满小时。

        upsert_hour_bill 单调只增,高估值回不去,所以结算读事件前必须先拿实例行锁。
        """
        inst_id = await seed_instance(sm, events=[ev(-30, "creating", "running")])
        started = asyncio.Event()
        release = asyncio.Event()

        async def inflight_stop() -> None:
            """模拟用户 10:59:30 关机的事务:先拿实例行锁,再写 stopping 事件,迟迟不提交。"""
            async with sm() as session:
                await session.execute(
                    select(Instance.id)
                    .where(Instance.id == inst_id)
                    .with_for_update(read=False, key_share=True)
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
            # 让结算真的先撞上锁,再放行关机事务
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
        # 3570 秒 × 3.60/时 = 3.57,而不是按满 3600 秒的 3.60
        assert bill.seconds_used == 3570
        assert charged == Decimal("3.57")

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
        assert await settle_due_hours(sm, at=at) == 1
        assert await settle_due_hours(sm, at=at) == 0  # 幂等:零重复扣款
        async with sm() as session:
            bill = (await session.execute(select(BillHourly))).scalar_one()
            w = (await session.execute(select(Wallet))).scalar_one()
        assert bill.seconds_used == 1800
        assert w.balance == Decimal("99.16")

    async def test_long_running_instance_without_window_events(self, sm):
        """跨小时持续 running(窗口内无事件)也必须被结算。"""
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
        assert bill.amount == Decimal("12.00")  # 3.00 × 4 卡

    async def test_ledger_balance_chain_consistent(self, sm):
        await seed_instance(
            sm, events=[(H - timedelta(hours=1), "creating", "running")], status="running"
        )
        await settle_due_hours(sm, at=H_END + timedelta(minutes=2))
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


class TestCatchUpSettlement:
    """停机跨整点后的追平:漏掉的小时必须补上,金额与连续运行一致。"""

    async def test_missed_hours_are_caught_up(self, sm):
        # 持续 running 3 小时;worker 只在最后一个整点后跑了一轮
        await seed_instance(
            sm, events=[(H - timedelta(hours=1), "creating", "running")], status="running"
        )
        at = H + timedelta(hours=3, minutes=2)
        assert await settle_due_hours(sm, at=at) == 1  # 首轮只结上一小时,水位线落 [12:00)
        at2 = H + timedelta(hours=6, minutes=2)  # 停机 3 小时后恢复
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
        # 4 个整点 × 1.68,与「连续运行、每小时准时结算」完全一致
        assert w.balance == Decimal("100.00") - Decimal("1.68") * 4

    async def test_watermark_advances_and_blocks_replay(self, sm):
        await seed_instance(
            sm, events=[(H - timedelta(hours=1), "creating", "running")], status="running"
        )
        at = H_END + timedelta(minutes=2)
        await settle_due_hours(sm, at=at)
        async with sm() as session:
            assert await get_watermark(session, "hourly") == H
        assert await settle_due_hours(sm, at=at) == 0  # 水位线已过,重跑不重复扣款

    async def test_catchup_truncated_at_limit(self, sm):
        """停机超出追平上限:只结最近 MAX_CATCHUP_HOURS 小时,不把 worker 拖死。"""
        from app.modules.billing.settlement import MAX_CATCHUP_HOURS

        await seed_instance(
            sm, events=[(H - timedelta(hours=1), "creating", "running")], status="running"
        )
        await settle_due_hours(sm, at=H_END + timedelta(minutes=2))
        at = H_END + timedelta(hours=MAX_CATCHUP_HOURS + 10)
        assert await settle_due_hours(sm, at=at) == MAX_CATCHUP_HOURS


@pytest.mark.parametrize(
    "anchor",
    [
        pytest.param(datetime(2026, 8, 19, 23, 0, tzinfo=UTC), id="cross-day"),
        pytest.param(datetime(2026, 8, 31, 23, 0, tzinfo=UTC), id="cross-month"),
        pytest.param(datetime(2026, 12, 31, 23, 0, tzinfo=UTC), id="cross-year"),
        pytest.param(datetime(2028, 2, 29, 23, 0, tzinfo=UTC), id="leap-day"),
    ],
)
class TestWindowBoundaries:
    """结算窗口跨日/跨月/跨年/闰日。

    锁住「按 aware-UTC 做 timedelta 递推」这一实现:换成 replace(day=...) 之类会算错账期。
    """

    async def test_tail_then_hourly_across_boundary(self, sm, anchor):
        start, end = anchor, anchor + timedelta(hours=1)
        inst_id = await seed_instance(
            sm,
            events=[
                (start - timedelta(minutes=30), "creating", "running"),
                (start + timedelta(minutes=30), "running", "stopping"),
            ],
        )
        async with sm() as session:
            charged = await settle_instance_window(
                session,
                instance_id=inst_id,
                user_id=1,
                unit_price=Decimal("3.6000"),
                gpu_count=1,
                window_start=start,
                window_end=end,
                source="hourly",
            )
            await session.commit()
        assert charged == Decimal("1.80")  # 半小时 × 3.60
        async with sm() as session:
            bill = (await session.execute(select(BillHourly))).scalar_one()
        assert bill.hour_start.replace(tzinfo=UTC) == start
        assert bill.seconds_used == 1800

    async def test_catchup_walks_over_boundary(self, sm, anchor):
        """水位线追平必须能连续跨过午夜/月末,而不是停在边界上。"""
        inst_id = await seed_instance(
            sm, events=[(anchor - timedelta(hours=2), "creating", "running")], status="running"
        )
        assert inst_id
        # 水位线停在 anchor 前两小时 → 追平应结出 anchor-1h、anchor 两个窗口
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
    """allow_negative=False 的拒绝路径。

    结算扣款必须允许透支(服务已消费完),但拒绝路径本身要能工作:
    任何「先付后用」的同步扣款都要靠它。
    """

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
        assert len(entries) == 1  # 只有那笔充值,没有半截扣款


class TestNodeLostBillingTruncation:
    """节点失联/Pod 丢失(node_lost/pod_lost):宽限观察期不计费。

    计费截断到 Pod 首次 not-ready 的时刻(metadata.unready_since),而非 reconciler
    判定时刻。挂了 = 节点断电后宽限期(默认 10 分钟)照收 GPU 时费;或尾账截断了、
    整点结算又把宽限期秒数补扣回来(口径不一致)。
    """

    async def test_reconstruction_truncates_at_unready_since(self, sm):
        unready = H + timedelta(minutes=5)
        inst_id = await seed_instance(
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
        # 重复结算(尾账/整点/追平同口径):零新增,不重复扣
        async with sm() as session:
            again = await settle_instance_window(
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
        assert again == Decimal("0.00")
        async with sm() as session:
            bill = (await session.execute(select(BillHourly))).scalar_one()
        assert bill.seconds_used == 300  # 10:00~10:05,不是到判定时刻 10:15 的 900s
        assert bill.amount == Decimal("0.14")

    async def test_tail_listener_truncates_and_marks_detail(self, sm):
        """尾账(与迁移同事务):截断窗口 + bills_hourly.detail 留截断依据。"""
        from app.modules.billing.edge_listener import on_instance_transition

        unready = H + timedelta(minutes=5)
        inst_id = await seed_instance(sm, status="failed", events=[ev(-30, "creating", "running")])
        async with sm() as session:
            inst = await session.get(Instance, inst_id)
            assert inst is not None
            inst.unready_since = unready  # reconciler 判定时刻实例行上的现场
            event = InstanceEvent(
                instance_id=inst_id,
                from_status="running",
                to_status="failed",
                reason="node_lost",
                actor="system",
                event_metadata={"unready_since": unready.isoformat()},
                created_at=H + timedelta(minutes=15),  # 判定时刻(宽限 10 分钟后)
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

        # 整点结算同小时:口径一致,宽限期秒数不会被补扣回来
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

    async def test_tail_truncation_across_hour_boundary(self, sm):
        """unready 在上一小时:尾账落在 unready 所在小时(而非判定时刻的小时)。"""
        from app.modules.billing.edge_listener import on_instance_transition

        unready = H - timedelta(minutes=5)  # 09:55
        inst_id = await seed_instance(sm, status="failed", events=[ev(-30, "creating", "running")])
        async with sm() as session:
            inst = await session.get(Instance, inst_id)
            assert inst is not None
            inst.unready_since = unready
            event = InstanceEvent(
                instance_id=inst_id,
                from_status="running",
                to_status="failed",
                reason="node_lost",
                actor="system",
                event_metadata={"unready_since": unready.isoformat()},
                created_at=H + timedelta(minutes=15),  # 10:15 判定
            )
            session.add(event)
            await on_instance_transition(session, inst, event)
            await session.commit()
        async with sm() as session:
            bills = (await session.execute(select(BillHourly))).scalars().all()
        # 只有 09 点这一小时的账(09:30~09:55 = 1500s);10 点小时不产生账单
        assert len(bills) == 1
        assert bills[0].hour_start.replace(tzinfo=UTC) == H - timedelta(hours=1)
        assert bills[0].seconds_used == 1500

    async def test_pod_lost_without_unready_bills_to_event(self, sm):
        """pod_lost 但从未观测到 not-ready(unready_since 为空):无法知道何时不可用,
        维持按事件时刻结算 —— 截断只发生在有依据时。"""
        from app.modules.billing.edge_listener import on_instance_transition

        inst_id = await seed_instance(sm, status="failed", events=[ev(-30, "creating", "running")])
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
        assert bill.seconds_used == 900  # 不截断
        assert bill.detail is not None and "truncated_at" not in bill.detail
