"""资金账实核对(只报不改)+ money 表 DB 兜底约束。"""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, text, update

from app.core.timeutil import billing_day_floor, now_utc
from app.modules.billing import wallet
from app.modules.billing.models import BalanceLedger, BillHourly, Wallet
from app.modules.billing.reconcile import reconcile_funds
from app.modules.notify.models import Notification
from tests.helpers import fund_wallet


class TestWalletLedgerInvariant:
    async def test_balance_drift_detected_and_not_written_back(self, sm):
        """余额与流水不符时告警,不修改余额或流水。"""
        await fund_wallet(sm, 1)
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
            w = (await session.execute(select(Wallet))).scalar_one()
            entries = (await session.execute(select(BalanceLedger))).scalars().all()
        assert any("账实核对" in a.title for a in alerts)
        assert w.balance == Decimal("999.00")
        assert len(entries) == 1

    async def test_missing_ledger_row_is_detected(self, sm):
        """流水行被删掉(或压根没写)同样被发现。"""
        await fund_wallet(sm, 1)
        async with sm() as session:
            await session.execute(text("DELETE FROM balance_ledger"))
            await session.commit()
        assert (await reconcile_funds(sm))["wallet_mismatch"] == 1


class TestBillsVsConsume:
    async def test_bill_without_debit_is_detected(self, sm):
        """出账写了但扣款没写:出账合计 ≠ 消费流水合计。"""
        await fund_wallet(sm, 1)
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
    async def test_overdraft_is_still_allowed(self, sm):
        """wallets 表无 balance >= 0 约束,透支必须放行。"""
        async with sm() as session:
            await wallet.debit(
                session,
                1,
                Decimal("5.00"),
                type_="consume",
                ref_type="bill_hourly",
                allow_negative=True,
                allow_frozen=True,
            )
            await session.commit()
        async with sm() as session:
            w = (await session.execute(select(Wallet))).scalar_one()
        assert w.balance == Decimal("-5.00")
