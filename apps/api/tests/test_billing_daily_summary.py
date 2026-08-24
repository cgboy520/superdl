"""GET /api/v1/bills/daily-summary:本地日界折 UTC 窗口的当日消费聚合。

覆盖:跨日界归属 / Decimal 精度 / 空数据 / 盘费计入 / 多实例分组 / 租户隔离 / 日月口径一致。
"""

from datetime import UTC, datetime
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.billing.models import BillDailyDisk, BillHourly


async def seed_hourly(
    sm: async_sessionmaker[AsyncSession],
    user_id: int,
    instance_id: int,
    hour_start: datetime,
    amount: str,
    seconds: int = 3600,
) -> None:
    async with sm() as session:
        session.add(
            BillHourly(
                user_id=user_id,
                instance_id=instance_id,
                hour_start=hour_start,
                seconds_used=seconds,
                unit_price=Decimal("1.0000"),
                gpu_count=1,
                amount=Decimal(amount),
            )
        )
        await session.commit()


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


async def register_user(client: AsyncClient, phone: str) -> tuple[dict[str, str], int]:
    from tests.test_account_auth import register

    data = await register(client, phone)
    return {"Authorization": f"Bearer {data['access_token']}"}, data["user"]["id"]


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
    async def test_tz_offset_bounds(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """TzOffset 统一 ±720 上下界:越界一律 422(此前 le=840 与默认 0 的漂移口径已收敛)。"""
        headers, _ = await register_user(client, "13900010099")
        for bad in (721, -721, 840, -840):
            resp = await client.get(
                "/api/v1/bills/daily-summary",
                params={"date": "2026-08-19", "tz_offset_minutes": bad},
                headers=headers,
            )
            assert resp.status_code == 422, (bad, resp.text)
        for good in (720, -720, 480):
            resp = await client.get(
                "/api/v1/bills/daily-summary",
                params={"date": "2026-08-19", "tz_offset_minutes": good},
                headers=headers,
            )
            assert resp.status_code == 200, (good, resp.text)

    async def test_day_boundary_attribution_utc8(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """东八区本地日 D = UTC [D-1 16:00, D 16:00):边界内计入,边界外排除。"""
        headers, uid = await register_user(client, "13900010001")
        inside_first = datetime(2026, 8, 18, 16, 0, tzinfo=UTC)  # 本地 8-19 00:00
        inside_last = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)  # 本地 8-19 23:00
        before = datetime(2026, 8, 18, 15, 0, tzinfo=UTC)  # 本地 8-18 23:00
        after = datetime(2026, 8, 19, 16, 0, tzinfo=UTC)  # 本地 8-20 00:00
        await seed_hourly(sm, uid, 101, inside_first, "1.00")
        await seed_hourly(sm, uid, 101, inside_last, "2.00")
        await seed_hourly(sm, uid, 101, before, "40.00")
        await seed_hourly(sm, uid, 101, after, "80.00")

        body = await get_summary(client, headers, "2026-08-19")
        assert body["gpu_total"] == "3.00"
        assert body["date"] == "2026-08-19"

    async def test_decimal_sum_precision(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """0.01 级金额累加 10 次无浮点误差(0.10 而非 0.09999…)。"""
        headers, uid = await register_user(client, "13900010002")
        for h in range(10):
            await seed_hourly(sm, uid, 102, datetime(2026, 8, 19, h, 0, tzinfo=UTC), "0.01")
        body = await get_summary(client, headers, "2026-08-19")
        assert body["gpu_total"] == "0.10"
        assert body["items"][0]["total_amount"] == "0.10"
        assert body["items"][0]["total_seconds"] == 36000

    async def test_empty_returns_zero(self, client: AsyncClient):
        headers, _ = await register_user(client, "13900010003")
        body = await get_summary(client, headers, "2026-08-19")
        assert body["gpu_total"] == "0.00"
        assert body["disk_total"] == "0.00"
        assert body["items"] == []

    async def test_disk_daily_counted(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """当日窗口内的数据盘日结计入 disk_total(UTC 零点落在东八区当日窗口内)。"""
        headers, uid = await register_user(client, "13900010005")
        await seed_disk_daily(sm, uid, datetime(2026, 8, 19, 0, 0, tzinfo=UTC), "3.50")
        await seed_disk_daily(sm, uid, datetime(2026, 8, 20, 0, 0, tzinfo=UTC), "3.50")
        body = await get_summary(client, headers, "2026-08-19")
        assert body["disk_total"] == "3.50"

    async def test_multi_instance_grouping(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        headers, uid = await register_user(client, "13900010006")
        t = datetime(2026, 8, 19, 2, 0, tzinfo=UTC)
        await seed_hourly(sm, uid, 201, t, "1.50")
        await seed_hourly(sm, uid, 202, t, "2.50")
        body = await get_summary(client, headers, "2026-08-19")
        assert body["gpu_total"] == "4.00"
        by_iid = {i["instance_id"]: i["total_amount"] for i in body["items"]}
        assert by_iid == {201: "1.50", 202: "2.50"}

    async def test_tenant_isolation(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        headers_a, uid_a = await register_user(client, "13900010007")
        headers_b, uid_b = await register_user(client, "13900010008")
        t = datetime(2026, 8, 19, 3, 0, tzinfo=UTC)
        await seed_hourly(sm, uid_a, 301, t, "9.00")
        await seed_hourly(sm, uid_b, 302, t, "1.00")
        body_a = await get_summary(client, headers_a, "2026-08-19")
        body_b = await get_summary(client, headers_b, "2026-08-19")
        assert body_a["gpu_total"] == "9.00"
        assert body_b["gpu_total"] == "1.00"
        assert {i["instance_id"] for i in body_a["items"]} == {301}


class TestMonthMatchesDays:
    async def test_daily_summaries_sum_to_month_summary(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """把整月的日账单加起来必须等于月账单:两个接口的日界口径必须一致。"""
        headers, uid = await register_user(client, "13900010009")
        # 边界四点(东八区):本地 7-31 23:00 在 8 月之外;8-01 00:00 与 8-31 23:00 在内
        await seed_hourly(sm, uid, 401, datetime(2026, 7, 31, 14, 0, tzinfo=UTC), "100.00")
        await seed_hourly(sm, uid, 401, datetime(2026, 7, 31, 16, 0, tzinfo=UTC), "1.00")
        await seed_hourly(sm, uid, 401, datetime(2026, 8, 31, 15, 0, tzinfo=UTC), "2.00")
        await seed_hourly(sm, uid, 401, datetime(2026, 8, 31, 16, 0, tzinfo=UTC), "200.00")

        resp = await client.get(
            "/api/v1/bills/summary",
            params={"month": "2026-08", "tz_offset_minutes": 480},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["gpu_total"] == "3.00"

        # 逐日相加 == 月合计
        total = Decimal("0.00")
        for day in range(1, 32):
            body = await get_summary(client, headers, f"2026-08-{day:02d}")
            total += Decimal(body["gpu_total"])
        assert total == Decimal("3.00")
