"""Organization finance view: available balance, the month-to-date window starting at joining,
the hourly burn basis, hours left, money formatting and the audit row of every read."""

from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditLog
from app.core.config import get_settings
from app.core.timeutil import billing_period_range, current_billing_period
from app.modules.billing import service as billing_service
from app.modules.orgs.models import OrgMember
from tests.helpers import fund_wallet, seed_bill_hourly, seed_instance
from tests.test_orgs import create_org, join_org, org_user

pytestmark = pytest.mark.usefixtures("fake")


async def _finance(client: AsyncClient, headers: dict[str, str]) -> dict:
    resp = await client.get("/api/v1/org/finance", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _row(view: dict, member_id: int) -> dict:
    return next(r for r in view["members"] if r["member_id"] == member_id)


class TestFinance:
    async def test_available_is_balance_minus_frozen(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """Fails when admins see the raw balance instead of what the member can spend."""
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        _, uid, mid = await join_org(client, owner, 2)
        await fund_wallet(sm, uid, "100.00")
        async with sm() as session:
            await billing_service.freeze(
                session, uid, Decimal("30.00"), ref_id="ord-1", remark="reversal"
            )
            await session.commit()
        row = _row(await _finance(client, owner), mid)
        assert row["available_balance"] == "70.00"

    async def test_month_to_date_starts_when_the_member_joined(
        self,
        client: AsyncClient,
        sm: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Fails when the view reaches back before the member consented, or across the billing
        month in the deployment's time zone."""
        monkeypatch.setattr(get_settings(), "billing_timezone", "Asia/Shanghai")
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        _, uid, mid = await join_org(client, owner, 2)
        start, _ = billing_period_range(current_billing_period())
        joined = start + timedelta(hours=5)
        async with sm() as session:
            await session.execute(
                update(OrgMember).where(OrgMember.user_id == uid).values(joined_at=joined)
            )
            await session.commit()
        instance_id, _ = await seed_instance(sm, uid, wallet_credit=False)
        await seed_bill_hourly(
            sm,
            uid,
            rows=[
                (instance_id, start - timedelta(hours=1), "9.00"),
                (instance_id, start + timedelta(hours=1), "4.00"),
                (instance_id, joined, "1.25"),
                (instance_id, joined + timedelta(hours=1), "1.25"),
            ],
        )
        view = await _finance(client, owner)
        row = _row(view, mid)
        assert row["month_to_date"] == "2.50"
        assert row["window_start"].startswith(joined.isoformat()[:19])
        assert view["billing_timezone"] == "Asia/Shanghai"
        assert view["period_start"].startswith(start.isoformat()[:19])

    async def test_burn_counts_running_on_demand_only(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """Fails when prepaid subscriptions or stopped instances inflate the hourly burn."""
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        _, uid, mid = await join_org(client, owner, 2)
        await fund_wallet(sm, uid, "100.00")
        await seed_instance(
            sm, uid, price="1.5000", gpu_count=2, status="running", wallet_credit=False
        )
        await seed_instance(sm, uid, status="running", market="subscription", wallet_credit=False)
        await seed_instance(sm, uid, status="stopped", wallet_credit=False)
        row = _row(await _finance(client, owner), mid)
        assert row["burn_per_hour"] == "3.0000"
        assert row["running_instances"] == 2
        assert row["est_hours_left"] == pytest.approx(33.3)

    async def test_no_burn_means_no_estimate(self, client: AsyncClient):
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        view = await _finance(client, owner)
        (row,) = view["members"]
        assert row["est_hours_left"] is None
        assert row["available_balance"] == "0.00"
        assert view["totals"]["running_instances"] == 0

    async def test_every_read_is_audited(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """Fails when admins can read members' balances without leaving an audit row."""
        owner, owner_uid = await org_user(client, 1)
        await create_org(client, owner)
        await _finance(client, owner)
        await _finance(client, owner)
        async with sm() as session:
            rows = (
                await session.scalars(
                    select(AuditLog).where(AuditLog.target == f"org-finance:{owner_uid}")
                )
            ).all()
        assert len(rows) == 2
