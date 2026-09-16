"""billing_identity lock: first boot locks env, matching boots pass, a mismatch is re-keyed only
while no money rows exist (or with the explicit rekey flag) and refused otherwise."""

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.timeutil import now_utc
from app.modules.billing.identity import assert_billing_identity
from app.modules.billing.models import BillingIdentity, Order


async def _identity(sm: async_sessionmaker[AsyncSession]) -> tuple[str, str]:
    async with sm() as session:
        row = await session.get(BillingIdentity, 1)
        assert row is not None
        return row.currency, row.timezone


async def test_first_boot_locks_env_and_repeat_is_noop(sm):
    settings = get_settings()
    async with sm() as session:
        await assert_billing_identity(session)
        await assert_billing_identity(session)
    assert await _identity(sm) == (settings.platform_currency, settings.billing_timezone)


async def test_mismatch_with_empty_ledger_rekeys(sm, monkeypatch):
    async with sm() as session:
        await assert_billing_identity(session)
    monkeypatch.setattr(get_settings(), "platform_currency", "CNY")
    monkeypatch.setattr(get_settings(), "billing_timezone", "Asia/Shanghai")
    async with sm() as session:
        await assert_billing_identity(session)
    assert await _identity(sm) == ("CNY", "Asia/Shanghai")


async def test_mismatch_with_money_rows_refuses_boot_unless_rekey_flag(sm, monkeypatch):
    async with sm() as session:
        await assert_billing_identity(session)
        session.add(
            Order(
                order_no="R-lock-1",
                user_id=1,
                amount=Decimal("1.00"),
                channel="mock",
                expires_at=now_utc(),
            )
        )
        await session.commit()
    monkeypatch.setattr(get_settings(), "platform_currency", "EUR")
    async with sm() as session:
        with pytest.raises(RuntimeError, match="billing identity mismatch"):
            await assert_billing_identity(session)
    monkeypatch.setattr(get_settings(), "billing_identity_rekey", True)
    async with sm() as session:
        await assert_billing_identity(session)
    assert (await _identity(sm))[0] == "EUR"


async def test_new_orders_carry_platform_currency(sm):
    async with sm() as session:
        order = Order(
            order_no="R-cur-1",
            user_id=1,
            amount=Decimal("5.00"),
            channel="mock",
            expires_at=now_utc(),
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)
        assert order.currency == get_settings().platform_currency
