"""账单 CSV 导出端点:内容头、行数、月份窗口、时区后缀、截断标记、转义规则。"""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import csvexport
from tests.helpers import create_user_with_key, funded_user, seed_bill_hourly


def _hour(y: int, m: int, d: int, h: int) -> datetime:
    return datetime(y, m, d, h, tzinfo=UTC)


class TestEscaping:
    """CSV 转义/时区后缀原语(app.core.csvexport)纯单测。"""

    def test_formula_lead_prefixed(self):
        assert csvexport.csv_line(["=1+1"]) == "'=1+1\r\n"
        assert csvexport.csv_line(["@who"]) == "'@who\r\n"
        # 纯数字负数金额不受影响;非纯数字的 - 前导按文本化
        assert csvexport.csv_line(["-12.30"]) == "-12.30\r\n"
        assert csvexport.csv_line(["-2+3"]) == "'-2+3\r\n"

    def test_comma_quote_newline_quoted(self):
        assert csvexport.csv_line(['a,"b"\nc']) == '"a,""b""\nc"\r\n'

    def test_utc_suffix(self):
        assert csvexport.utc_suffix(480) == "(UTC+8)"
        assert csvexport.utc_suffix(-300) == "(UTC-5)"
        assert csvexport.utc_suffix(345) == "(UTC+5:45)"
        assert csvexport.utc_suffix(0) == "(UTC+0)"


class TestHourlyExport:
    async def test_headers_rows_and_month_window(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        headers, user_id, _ = await create_user_with_key(client, "13900000301")
        await seed_bill_hourly(
            sm,
            user_id,
            rows=[
                (1, _hour(2026, 8, 1, 0), "1.68"),
                (1, _hour(2026, 8, 1, 1), "1.68"),
                (1, _hour(2026, 7, 31, 20), "1.68"),
            ],
            unit_price="1.6800",
        )
        resp = await client.get(
            "/api/v1/billing/export",
            params={"dataset": "hourly", "month": "2026-08", "tz_offset_minutes": 480},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert "attachment" in resp.headers["content-disposition"]
        assert "superdl-hourly-2026-08.csv" in resp.headers["content-disposition"]

        text = resp.text
        assert text.startswith("\ufeff小时,实例ID,运行秒数,单价(元/时),卡数,金额(元)\r\n")
        lines = [ln for ln in text.removeprefix("\ufeff").split("\r\n") if ln]
        # 8 月窗口(UTC+8):7/31 20:00 UTC = 8/1 04:00 本地也在月内
        assert len(lines) == 1 + 3
        # 行按 id 降序;时间按 UTC+8 折算并带后缀
        hours = {ln.split(",", 1)[0] for ln in lines[1:]}
        assert hours == {
            "2026-08-01 08:00 (UTC+8)",
            "2026-08-01 09:00 (UTC+8)",
            "2026-08-01 04:00 (UTC+8)",
        }
        assert lines[1].endswith(",1,3600,1.6800,1,1.68")
        assert csvexport.TRUNCATED_MARKER not in text

    async def test_month_excludes_outside_rows(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        headers, user_id, _ = await create_user_with_key(client, "13900000302")
        # 6/30 17:00 UTC = 7/1 01:00 (UTC+8),不属于 6 月窗口
        await seed_bill_hourly(
            sm,
            user_id,
            rows=[(1, _hour(2026, 6, 30, 17), "1.68"), (1, _hour(2026, 6, 15, 0), "1.68")],
            unit_price="1.6800",
        )
        resp = await client.get(
            "/api/v1/billing/export",
            params={"dataset": "hourly", "month": "2026-06", "tz_offset_minutes": 480},
            headers=headers,
        )
        lines = [ln for ln in resp.text.split("\r\n") if ln]
        assert len(lines) == 1 + 1  # 只剩 6/15 那行

    async def test_truncation_marker(
        self,
        client: AsyncClient,
        sm: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ):
        headers, user_id, _ = await create_user_with_key(client, "13900000304")
        await seed_bill_hourly(
            sm,
            user_id,
            rows=[(1, _hour(2026, 8, 1, h), "1.68") for h in range(4)],
            unit_price="1.6800",
        )
        monkeypatch.setattr(csvexport, "EXPORT_MAX_ROWS", 2)
        resp = await client.get(
            "/api/v1/billing/export",
            params={"dataset": "hourly", "month": "2026-08"},
            headers=headers,
        )
        lines = [ln for ln in resp.text.split("\r\n") if ln]
        assert len(lines) == 1 + 2 + 1  # 表头 + 上限行数 + 截断标记行
        assert lines[-1].startswith(csvexport.TRUNCATED_MARKER)


class TestLedgerExport:
    async def test_ledger_rows(self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]):
        headers, _user_id, _ = await funded_user(client, sm, "13900000306", "100.00")
        resp = await client.get(
            "/api/v1/billing/export",
            params={"dataset": "ledger", "tz_offset_minutes": 480},
            headers=headers,
        )
        assert resp.status_code == 200
        text = resp.text
        assert "时间,类型,金额(元),余额快照(元),关联,备注" in text
        assert "充值,100.00,100.00" in text
        assert "(UTC+8)" in text

    async def test_isolation(self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]):
        headers, _user_id, _ = await create_user_with_key(client, "13900000307")
        _headers2, user_id2, _ = await funded_user(client, sm, "13900000308", "888.00")
        await seed_bill_hourly(sm, user_id2, rows=[(1, _hour(2026, 8, 1, 0), "1.68")])
        resp = await client.get(
            "/api/v1/billing/export",
            params={"dataset": "ledger"},
            headers=headers,
        )
        assert "888.00" not in resp.text
        resp2 = await client.get(
            "/api/v1/billing/export",
            params={"dataset": "hourly", "month": "2026-08"},
            headers=headers,
        )
        assert "1.6800" not in resp2.text
