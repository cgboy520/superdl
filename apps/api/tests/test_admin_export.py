"""管理端 CSV 导出:订单/租户流水/审计/日对账 —— 口径与截断标记(角色门由 route×role 矩阵覆盖)。"""

from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import csvexport
from app.core.timeutil import now_utc
from tests.helpers import fund_wallet
from tests.test_account_auth import register
from tests.test_admin_ops import second_admin_headers
from tests.test_catalog import admin_headers

pytestmark = pytest.mark.usefixtures("fake")


@pytest.fixture
def fake():
    from app.core.k8s import set_orchestrator
    from app.core.k8s.fake import FakeOrchestrator

    orch = FakeOrchestrator(auto_ready=False)
    set_orchestrator(orch)
    yield orch
    set_orchestrator(None)


async def _make_orders(sm: async_sessionmaker[AsyncSession], count: int = 2) -> None:
    from app.modules.billing.models import Order

    async with sm() as session:
        for i in range(count):
            session.add(
                Order(
                    order_no=f"SDL-EXP-{i}",
                    user_id=1,
                    amount=Decimal("10.00"),
                    channel="mock",
                    status="paid" if i == 0 else "pending",
                    expires_at=now_utc() + timedelta(minutes=30),
                )
            )
        await session.commit()


class TestOrdersExport:
    async def test_csv_rows_and_filters(self, client: AsyncClient, sm):
        await _make_orders(sm)
        fin = await second_admin_headers(sm, client, "fin-exp")
        resp = await client.get("/api/admin/v1/orders/export", headers=fin)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        text = resp.text
        assert text.startswith("﻿")  # BOM 防 Excel 中文乱码
        lines = text.splitlines()
        assert lines[0].lstrip("﻿").startswith("订单号")
        assert "(UTC+8)" in text
        assert any("SDL-EXP-0" in line and "已支付" in line for line in lines)
        # 状态过滤与列表端点同口径
        resp = await client.get(
            "/api/admin/v1/orders/export", params={"status": "pending"}, headers=fin
        )
        only_pending = resp.text
        assert "SDL-EXP-1" in only_pending and "SDL-EXP-0" not in only_pending

    async def test_truncation_marker(self, client: AsyncClient, sm, monkeypatch):
        await _make_orders(sm, count=3)
        monkeypatch.setattr(csvexport, "EXPORT_MAX_ROWS", 2)
        fin = await second_admin_headers(sm, client, "fin-exp-cap")
        text = (await client.get("/api/admin/v1/orders/export", headers=fin)).text
        lines = [line for line in text.splitlines() if line.startswith("SDL-")]
        assert len(lines) == 2  # 触顶只出前 N 行
        assert text.splitlines()[-1].startswith(csvexport.TRUNCATED_MARKER)


class TestTenantLedgerExport:
    async def test_rows_match_ledger(self, client: AsyncClient, sm):
        data = await register(client, "13688880001")
        uid = data["user"]["id"]
        await fund_wallet(sm, uid, "66.00")
        ops = await second_admin_headers(sm, client, "ops-exp-ledger", role="ops")
        resp = await client.get(f"/api/admin/v1/tenants/{uid}/ledger/export", headers=ops)
        assert resp.status_code == 200
        text = resp.text
        assert "充值" in text and "66.00" in text and "test-fund" in text
        assert csvexport.TRUNCATED_MARKER not in text
        # 管理端导出与用户端导出同一数据源:行数一致
        user_headers = {"Authorization": f"Bearer {data['access_token']}"}
        mine_resp = await client.get("/api/v1/billing/export?dataset=ledger", headers=user_headers)
        assert len(mine_resp.text.splitlines()) == len(text.splitlines())


class TestAuditExport:
    async def test_filtered_rows_and_audited(self, client: AsyncClient, sm):
        from app.core.audit import AuditLog

        h = await admin_headers(sm, client)
        await register(client, "13688880002")
        # 造一条已知审计:按手机号检索租户(敏感读显式留痕)
        await client.get("/api/admin/v1/tenants", params={"q": "13688880002"}, headers=h)

        resp = await client.get(
            "/api/admin/v1/audit/export", params={"actor_type": "admin"}, headers=h
        )
        assert resp.status_code == 200
        text = resp.text
        assert "tenant-search:" in text
        assert csvexport.TRUNCATED_MARKER not in text
        # 过滤口径与 GET /audit 一致:actor_type=user 时刚才那条 admin 检索不得出现
        by_user = (
            await client.get("/api/admin/v1/audit/export", params={"actor_type": "user"}, headers=h)
        ).text
        assert "tenant-search:" not in by_user
        # 导出本身是敏感读,落一条 audit:export(只记筛选参数)
        async with sm() as session:
            hits = (
                (await session.execute(select(AuditLog).where(AuditLog.target == "audit:export")))
                .scalars()
                .all()
            )
        assert len(hits) >= 1

    async def test_truncation_marker(self, client: AsyncClient, sm, monkeypatch):
        h = await admin_headers(sm, client)
        await register(client, "13688880003")
        monkeypatch.setattr(csvexport, "EXPORT_MAX_ROWS", 1)
        text = (await client.get("/api/admin/v1/audit/export", headers=h)).text
        assert text.splitlines()[-1].startswith(csvexport.TRUNCATED_MARKER)


class TestReconciliationExport:
    async def test_totals_row_and_bad_day(self, client: AsyncClient, sm):
        fin = await second_admin_headers(sm, client, "fin-exp-recon")
        day = now_utc().date().isoformat()
        resp = await client.get(
            "/api/admin/v1/reconciliation/export", params={"day": day}, headers=fin
        )
        assert resp.status_code == 200
        # 表头之后第一行 = 合计行(空库:两侧均为 0.00,diff 0)
        assert resp.text.splitlines()[1].startswith("合计,")
        # 非法日期 → 400(与 GET /reconciliation 同一 parse_day)
        bad = await client.get(
            "/api/admin/v1/reconciliation/export", params={"day": "bad"}, headers=fin
        )
        assert bad.status_code == 400
