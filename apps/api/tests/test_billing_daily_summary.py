"""Local-date consumption summary: day boundary, amount precision, tenant isolation and daily /
monthly consistency."""

from datetime import UTC, datetime
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.billing.models import BillDailyDisk
from tests.helpers import seed_bill_hourly, user_headers_with_id


async def seed_disk_daily(
    sm: async_sessionmaker[AsyncSession], user_id: int, day: datetime, amount: str
) -> None:
    async with sm() as session:
        session.add(
            BillDailyDisk(
                disk_id=990_000 + int(day.timestamp()) % 100_000,
                user_id=user_id,
                day=day,
                size_gb=100,
                unit_price=Decimal("0.0350"),
                amount=Decimal(amount),
            )
        )
        await session.commit()


async def get_summary(
    client: AsyncClient, headers: dict[str, str], date: str, offset: int = 480
) -> dict:
    resp = await client.get(
        "/api/v1/bills/daily-summary",
        params={"date": date, "tz_offset_minutes": offset},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestDailySummary:
    async def test_day_boundary_attribution_utc8(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        headers, uid = await user_headers_with_id(client, "13900010001")
        inside_first = datetime(2026, 8, 18, 16, 0, tzinfo=UTC)
        inside_last = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)
        before = datetime(2026, 8, 18, 15, 0, tzinfo=UTC)
        after = datetime(2026, 8, 19, 16, 0, tzinfo=UTC)
        await seed_bill_hourly(
            sm,
            uid,
            rows=[
                (101, inside_first, "1.00"),
                (101, inside_last, "2.00"),
                (101, before, "40.00"),
                (101, after, "80.00"),
            ],
        )

        body = await get_summary(client, headers, "2026-08-19")
        assert body["gpu_total"] == "3.00"
        assert body["date"] == "2026-08-19"

    async def test_decimal_sum_precision(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        headers, uid = await user_headers_with_id(client, "13900010002")
        await seed_bill_hourly(
            sm,
            uid,
            rows=[(102, datetime(2026, 8, 19, h, 0, tzinfo=UTC), "0.01") for h in range(10)],
        )
        body = await get_summary(client, headers, "2026-08-19")
        assert body["gpu_total"] == "0.10"
        assert body["items"][0]["total_amount"] == "0.10"
        assert body["items"][0]["total_seconds"] == 36000

    async def test_empty_returns_zero(self, client: AsyncClient):
        headers, _ = await user_headers_with_id(client, "13900010003")
        body = await get_summary(client, headers, "2026-08-19")
        assert body["gpu_total"] == "0.00"
        assert body["disk_total"] == "0.00"
        assert body["items"] == []

    async def test_disk_daily_counted(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        headers, uid = await user_headers_with_id(client, "13900010005")
        await seed_disk_daily(sm, uid, datetime(2026, 8, 19, 0, 0, tzinfo=UTC), "3.50")
        await seed_disk_daily(sm, uid, datetime(2026, 8, 20, 0, 0, tzinfo=UTC), "3.50")
        body = await get_summary(client, headers, "2026-08-19")
        assert body["disk_total"] == "3.50"

    async def test_multi_instance_grouping(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        headers, uid = await user_headers_with_id(client, "13900010006")
        t = datetime(2026, 8, 19, 2, 0, tzinfo=UTC)
        await seed_bill_hourly(sm, uid, rows=[(201, t, "1.50"), (202, t, "2.50")])
        body = await get_summary(client, headers, "2026-08-19")
        assert body["gpu_total"] == "4.00"
        by_iid = {i["instance_id"]: i["total_amount"] for i in body["items"]}
        assert by_iid == {201: "1.50", 202: "2.50"}

    async def test_tenant_isolation(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        headers_a, uid_a = await user_headers_with_id(client, "13900010007")
        headers_b, uid_b = await user_headers_with_id(client, "13900010008")
        t = datetime(2026, 8, 19, 3, 0, tzinfo=UTC)
        await seed_bill_hourly(sm, uid_a, rows=[(301, t, "9.00")])
        await seed_bill_hourly(sm, uid_b, rows=[(302, t, "1.00")])
        body_a = await get_summary(client, headers_a, "2026-08-19")
        body_b = await get_summary(client, headers_b, "2026-08-19")
        assert body_a["gpu_total"] == "9.00"
        assert body_b["gpu_total"] == "1.00"
        assert {i["instance_id"] for i in body_a["items"]} == {301}


class TestMonthMatchesDays:
    async def test_daily_summaries_sum_to_month_summary(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        headers, uid = await user_headers_with_id(client, "13900010009")
        await seed_bill_hourly(
            sm,
            uid,
            rows=[
                (401, datetime(2026, 7, 31, 14, 0, tzinfo=UTC), "100.00"),
                (401, datetime(2026, 7, 31, 16, 0, tzinfo=UTC), "1.00"),
                (401, datetime(2026, 8, 31, 15, 0, tzinfo=UTC), "2.00"),
                (401, datetime(2026, 8, 31, 16, 0, tzinfo=UTC), "200.00"),
            ],
        )

        resp = await client.get(
            "/api/v1/bills/summary",
            params={"month": "2026-08", "tz_offset_minutes": 480},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["gpu_total"] == "3.00"

        total = Decimal("0.00")
        for day in range(1, 32):
            body = await get_summary(client, headers, f"2026-08-{day:02d}")
            total += Decimal(body["gpu_total"])
        assert total == Decimal("3.00")
