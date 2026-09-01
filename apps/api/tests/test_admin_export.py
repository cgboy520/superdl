"""管理端 CSV 导出:订单/租户流水/审计/日对账 —— 口径(截断标记统一由 test_billing_export 覆盖)。"""

from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import csvexport
from app.core.timeutil import now_utc
from tests.helpers import admin_headers, fund_wallet, register

pytestmark = pytest.mark.usefixtures("fake")


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
        fin = await admin_headers(sm, client, role="finance", username="fin-exp")
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


async def _make_refunds(sm: async_sessionmaker[AsyncSession], count: int = 2) -> None:
    from app.modules.billing.models import RefundRequest

    async with sm() as session:
        for i in range(count):
            session.add(
                RefundRequest(
                    refund_no=f"R20260101-E{i}",
                    user_id=1,
                    order_no=f"SDL-EXP-RF{i}",
                    amount=Decimal("10.00"),
                    reason="重复扣款",
                    status="pending" if i == 0 else "paid",
                    payout_channel=None if i == 0 else "offline",
                    payout_ref=None if i == 0 else "PAY-REF-1",
                )
            )
        await session.commit()


class TestRefundsExport:
    async def test_csv_rows_and_filters(self, client: AsyncClient, sm):
        await _make_refunds(sm)
        fin = await admin_headers(sm, client, role="finance", username="fin-exp-rf")
        resp = await client.get("/api/admin/v1/refunds/export", headers=fin)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        text = resp.text
        assert text.startswith("﻿")
        lines = text.splitlines()
        assert lines[0].lstrip("﻿").startswith("退款单号")
        assert "(UTC+8)" in text
        assert any("R20260101-E0" in line and "待审批" in line for line in lines)
        # 已打款行带渠道文案与凭证号
        assert any(
            "R20260101-E1" in line and "线下转账" in line and "PAY-REF-1" in line for line in lines
        )
        # 状态过滤与列表端点同口径
        resp = await client.get(
            "/api/admin/v1/refunds/export", params={"status": "paid"}, headers=fin
        )
        only_paid = resp.text
        assert "R20260101-E1" in only_paid and "R20260101-E0" not in only_paid
        # day 过滤:申请日(UTC 今日)命中,昨日为空(仅表头)
        today = now_utc().date().isoformat()
        by_day = (
            await client.get("/api/admin/v1/refunds/export", params={"day": today}, headers=fin)
        ).text
        assert "R20260101-E0" in by_day
        yesterday = (now_utc() - timedelta(days=1)).date().isoformat()
        by_yday = (
            await client.get("/api/admin/v1/refunds/export", params={"day": yesterday}, headers=fin)
        ).text
        assert "R20260101-E0" not in by_yday


async def _make_invoices(sm: async_sessionmaker[AsyncSession], count: int = 2) -> None:
    from app.modules.billing.models import InvoiceRequest

    async with sm() as session:
        for i in range(count):
            session.add(
                InvoiceRequest(
                    user_id=1,
                    # 同 (user_id, period) 非 rejected 唯一:逐行错开账期
                    period=f"2026-{7 + i:02d}",
                    title_type="company",
                    title=f"示例科技(深圳)有限公司{i}号",
                    tax_id="91440300MA5F000000",
                    email=f"ap{i}@example.com",
                    amount=Decimal("100.00"),
                    status="submitted" if i == 0 else "issued",
                    invoice_no=None if i == 0 else f"INV-2026-000{i}",
                )
            )
        await session.commit()


class TestInvoicesExport:
    async def test_csv_rows_and_filters(self, client: AsyncClient, sm):
        await _make_invoices(sm)
        fin = await admin_headers(sm, client, role="finance", username="fin-exp-iv")
        resp = await client.get("/api/admin/v1/invoices/export", headers=fin)
        assert resp.status_code == 200
        text = resp.text
        assert text.startswith("﻿")
        lines = text.splitlines()
        assert lines[0].lstrip("﻿").startswith("发票号")
        assert any("2026-07" in line and "审核中" in line for line in lines)
        assert any("INV-2026-0001" in line and "已开票" in line for line in lines)
        # status/period 过滤与列表端点同口径
        by_status = (
            await client.get(
                "/api/admin/v1/invoices/export", params={"status": "issued"}, headers=fin
            )
        ).text
        assert "INV-2026-0001" in by_status and "2026-07" not in by_status
        by_period = (
            await client.get(
                "/api/admin/v1/invoices/export", params={"period": "2026-07"}, headers=fin
            )
        ).text
        assert "2026-07" in by_period and "INV-2026-0001" not in by_period


class TestInvoicePiiGate:
    """发票抬头/税号/邮箱是全站最集中的 PII 出口(单次导出封顶 5 万行):
    只给财务、默认脱敏、明文要事由、每次导出都留痕。"""

    async def test_readonly_is_refused(self, client: AsyncClient, sm):
        """readonly 一律 403 —— /tenants 已明确不给它明文实名,这里放它整表拉走
        抬头+邮箱是同一份 PII 换个出口。挂了说明角色门又被放宽回 _ANY_READ。"""
        await _make_invoices(sm)
        ro = await admin_headers(sm, client, role="readonly", username="ro-exp-iv")
        for path in ("/api/admin/v1/invoices", "/api/admin/v1/invoices/export"):
            resp = await client.get(path, headers=ro)
            assert resp.status_code == 403, (path, resp.text)

    async def test_masked_by_default_and_reason_required(self, client: AsyncClient, sm):
        """不给 reveal:抬头与邮箱脱敏;给了 reveal 却不给事由:422,不吐明文。"""
        await _make_invoices(sm)
        fin = await admin_headers(sm, client, role="finance", username="fin-mask-iv")

        csv_text = (await client.get("/api/admin/v1/invoices/export", headers=fin)).text
        assert "示例科技(深圳)有限公司0号" not in csv_text
        assert "ap0@example.com" not in csv_text
        assert "示***********" in csv_text  # 留首字符掩其余(与租户实名同一档)
        # 税号与金额不脱敏:财务核对要用,且不是自然人标识
        assert "91440300MA5F000000" in csv_text

        rows = (await client.get("/api/admin/v1/invoices", headers=fin)).json()
        assert all(r["title"].startswith("示*") and "@" not in r["email"] for r in rows), rows

        no_reason = await client.get(
            "/api/admin/v1/invoices/export", params={"reveal": True}, headers=fin
        )
        assert no_reason.status_code == 400
        assert no_reason.json()["detail"] == {
            "field": "reason",
            "constraint": "required_when_reveal",
        }

    async def test_reveal_with_reason_is_audited_with_row_count(self, client: AsyncClient, sm):
        """明文导出 = 一次显式动作:CSV 里是明文,同时落一条带筛选条件与**实际行数**的审计。

        挂了说明 5 万行 PII 可以被拉走而没有任何一行记录说明谁、为什么、拉了多少。
        """
        from app.core.audit import AuditLog

        await _make_invoices(sm)
        fin = await admin_headers(sm, client, role="finance", username="fin-reveal-iv")
        resp = await client.get(
            "/api/admin/v1/invoices/export",
            params={"reveal": True, "reason": "月末开票核对", "status": "submitted"},
            headers=fin,
        )
        assert resp.status_code == 200
        assert "示例科技(深圳)有限公司0号" in resp.text and "ap0@example.com" in resp.text

        async with sm() as session:
            row = (
                await session.execute(
                    select(AuditLog).where(AuditLog.target == "invoice-identity:export")
                )
            ).scalar_one()
        assert row.actor_type == "admin"
        assert row.detail == {
            "rows": 1,  # status=submitted 只命中一条
            "reveal": True,
            "reason": "月末开票核对",
            "status": "submitted",
            "period": None,
            "format": "csv",
        }
        # 审计行与该次请求的日志之间有连接键
        assert row.request_id == resp.headers["x-request-id"]

    async def test_json_reveal_is_audited(self, client: AsyncClient, sm):
        """JSON 列表与 CSV 同一档:明文同样要事由 + 审计(否则绕开导出就白设闸)。"""
        from app.core.audit import AuditLog

        await _make_invoices(sm)
        fin = await admin_headers(sm, client, role="finance", username="fin-reveal-json")
        resp = await client.get(
            "/api/admin/v1/invoices",
            params={"reveal": True, "reason": "客服核对抬头"},
            headers=fin,
        )
        assert resp.status_code == 200
        assert any(r["title"] == "示例科技(深圳)有限公司0号" for r in resp.json())
        async with sm() as session:
            row = (
                await session.execute(
                    select(AuditLog).where(AuditLog.target == "invoice-identity:reveal")
                )
            ).scalar_one()
        assert row.detail is not None and row.detail["rows"] == 2
        assert row.detail["reason"] == "客服核对抬头"


async def _make_adjustments(sm: async_sessionmaker[AsyncSession], count: int = 2) -> None:
    from app.modules.adminapi.models import AdminAdjustment

    async with sm() as session:
        for i in range(count):
            session.add(
                AdminAdjustment(
                    user_id=i + 1,
                    amount=Decimal("5.00") if i == 0 else Decimal("-3.00"),
                    reason=f"赔付工单 T2026{i}",
                    status="pending" if i == 0 else "approved",
                    created_by=1,
                    reviewed_by=None if i == 0 else 2,
                )
            )
        await session.commit()


class TestAdjustmentsExport:
    async def test_csv_rows_and_filters(self, client: AsyncClient, sm):
        await _make_adjustments(sm)
        fin = await admin_headers(sm, client, role="finance", username="fin-exp-adj")
        resp = await client.get("/api/admin/v1/adjustments/export", headers=fin)
        assert resp.status_code == 200
        text = resp.text
        assert text.startswith("﻿")
        lines = text.splitlines()
        assert lines[0].lstrip("﻿").startswith("ID,用户ID")
        assert any("赔付工单 T20260" in line and "待复核" in line for line in lines)
        assert any("赔付工单 T20261" in line and "已生效" in line for line in lines)
        # status/user_id 过滤与列表端点同口径
        by_status = (
            await client.get(
                "/api/admin/v1/adjustments/export", params={"status": "approved"}, headers=fin
            )
        ).text
        assert "赔付工单 T20261" in by_status and "赔付工单 T20260" not in by_status
        by_uid = (
            await client.get("/api/admin/v1/adjustments/export", params={"user_id": 2}, headers=fin)
        ).text
        assert "赔付工单 T20261" in by_uid and "赔付工单 T20260" not in by_uid


class TestTenantLedgerExport:
    async def test_rows_match_ledger(self, client: AsyncClient, sm):
        data = await register(client, "13688880001")
        uid = data["user"]["id"]
        await fund_wallet(sm, uid, "66.00")
        ops = await admin_headers(sm, client, role="ops", username="ops-exp-ledger")
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


class TestReconciliationExport:
    async def test_totals_row_and_bad_day(self, client: AsyncClient, sm):
        fin = await admin_headers(sm, client, role="finance", username="fin-exp-recon")
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
