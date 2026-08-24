"""资金账实核对(只报不改)+ money 表 DB 兜底约束。"""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text, update

from app.core.timeutil import billing_day_floor, now_utc
from app.modules.billing import wallet
from app.modules.billing.models import BalanceLedger, BillHourly, Wallet
from app.modules.billing.reconcile import reconcile_funds
from app.modules.notify.models import Notification


async def _fund(sm, user_id: int, amount: str = "100.00") -> None:
    async with sm() as session:
        await wallet.credit(session, user_id, Decimal(amount), type_="recharge")
        await session.commit()


class TestWalletLedgerInvariant:
    async def test_clean_books_report_no_mismatch(self, sm):
        await _fund(sm, 1)
        counts = await reconcile_funds(sm)
        assert counts == {"wallet_mismatch": 0, "bill_mismatch": 0}

    async def test_balance_drift_is_detected(self, sm):
        """手工改一笔余额(模拟坏写/半提交事务)必须被发现。"""
        await _fund(sm, 1)
        async with sm() as session:
            await session.execute(update(Wallet).values(balance=Decimal("999.00")))
            await session.commit()
        counts = await reconcile_funds(sm)
        assert counts["wallet_mismatch"] == 1
        async with sm() as session:
            alerts = (
                (
                    await session.execute(
                        select(Notification).where(Notification.type == "admin_alert")
                    )
                )
                .scalars()
                .all()
            )
        assert any("账实核对" in a.title for a in alerts)

    async def test_missing_ledger_row_is_detected(self, sm):
        """流水行被删掉(或压根没写)同样被发现。"""
        await _fund(sm, 1)
        async with sm() as session:
            await session.execute(text("DELETE FROM balance_ledger"))
            await session.commit()
        assert (await reconcile_funds(sm))["wallet_mismatch"] == 1

    async def test_reconcile_never_writes_back(self, sm):
        """只报不改:自动纠正会把一个可查的差异变成一个不可查的差异。"""
        await _fund(sm, 1)
        async with sm() as session:
            await session.execute(update(Wallet).values(balance=Decimal("999.00")))
            await session.commit()
        await reconcile_funds(sm)
        async with sm() as session:
            w = (await session.execute(select(Wallet))).scalar_one()
            entries = (await session.execute(select(BalanceLedger))).scalars().all()
        assert w.balance == Decimal("999.00")  # 没被改回去
        assert len(entries) == 1  # 也没补一条「纠正」流水


class TestBillsVsConsume:
    async def test_bill_without_debit_is_detected(self, sm):
        """出账写了但扣款没写:出账合计 ≠ 消费流水合计。"""
        await _fund(sm, 1)
        async with sm() as session:
            session.add(
                BillHourly(
                    instance_id=1,
                    user_id=1,
                    hour_start=billing_day_floor(now_utc()) - timedelta(hours=5),
                    seconds_used=3600,
                    unit_price=Decimal("1.0000"),
                    gpu_count=1,
                    amount=Decimal("1.00"),
                    created_at=billing_day_floor(now_utc()) - timedelta(hours=5),
                )
            )
            await session.commit()
        counts = await reconcile_funds(sm)
        assert counts["bill_mismatch"] == 1


class TestMoneyTableConstraints:
    async def test_zero_amount_ledger_row_rejected(self, sm):
        """金额为 0 的流水没有业务含义,写入那一刻就要失败。"""
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            async with sm() as session:
                session.add(
                    BalanceLedger(
                        user_id=1,
                        type="consume",
                        amount=Decimal("0.00"),
                        balance_after=Decimal("0.00"),
                    )
                )
                await session.commit()

    async def test_hour_bill_seconds_out_of_range_rejected(self, sm):
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            async with sm() as session:
                session.add(
                    BillHourly(
                        instance_id=1,
                        user_id=1,
                        hour_start=now_utc(),
                        seconds_used=7200,  # 单个自然小时窗口不可能超过 3600
                        unit_price=Decimal("1.0000"),
                        gpu_count=1,
                        amount=Decimal("2.00"),
                    )
                )
                await session.commit()

    async def test_overdraft_is_still_allowed(self, sm):
        """刻意不加 balance >= 0:透支是设计内的(服务已消费完才结算)。"""
        async with sm() as session:
            await wallet.debit(
                session,
                1,
                Decimal("5.00"),
                type_="consume",
                ref_type="bill_hourly",
                allow_negative=True,
            )
            await session.commit()
        async with sm() as session:
            w = (await session.execute(select(Wallet))).scalar_one()
        assert w.balance == Decimal("-5.00")
