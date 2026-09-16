"""Filter, masking and audit contracts of the admin CSV exports."""

from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import csvexport
from app.core.timeutil import billing_offset_minutes, now_utc
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
        assert text.startswith("﻿")
        lines = text.splitlines()
        assert lines[0].lstrip("﻿").startswith("Order no")
        assert csvexport.utc_suffix(billing_offset_minutes()) in text
        assert any("SDL-EXP-0" in line and "Paid" in line for line in lines)
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
                    reason="duplicate charge",
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
        assert lines[0].lstrip("﻿").startswith("Refund no")
        assert csvexport.utc_suffix(billing_offset_minutes()) in text
        assert any("R20260101-E0" in line and "Pending review" in line for line in lines)
        assert any(
            "R20260101-E1" in line and "Offline transfer" in line and "PAY-REF-1" in line
            for line in lines
        )
        resp = await client.get(
            "/api/admin/v1/refunds/export", params={"status": "paid"}, headers=fin
        )
        only_paid = resp.text
        assert "R20260101-E1" in only_paid and "R20260101-E0" not in only_paid
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
                    period=f"2026-{7 + i:02d}",
                    title_type="company",
                    title=f"示例科技(深圳)有限公司{i}号",  # CJK title: exercises masking  # cjk-ok
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
        assert lines[0].lstrip("﻿").startswith("Invoice no")
        assert any("2026-07" in line and "In review" in line for line in lines)
        assert any("INV-2026-0001" in line and "Issued" in line for line in lines)
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
    """Invoice export: finance only, masked by default, plaintext needs a reason, every export
    leaves
    a trace."""

    async def test_masked_by_default_and_reason_required(self, client: AsyncClient, sm):
        """Title and email masked by default; reveal without a reason returns 400."""
        await _make_invoices(sm)
        fin = await admin_headers(sm, client, role="finance", username="fin-mask-iv")

        csv_text = (await client.get("/api/admin/v1/invoices/export", headers=fin)).text
        assert "示例科技(深圳)有限公司0号" not in csv_text  # cjk-ok
        assert "ap0@example.com" not in csv_text
        assert "示***********" in csv_text  # cjk-ok
        assert "91440300MA5F000000" in csv_text

        rows = (await client.get("/api/admin/v1/invoices", headers=fin)).json()
        assert all(r["title"].startswith("示*") and "***@" in r["email"] for r in rows), (  # cjk-ok
            rows
        )  # cjk-ok

        no_reason = await client.get(
            "/api/admin/v1/invoices/export", params={"reveal": True}, headers=fin
        )
        assert no_reason.status_code == 400
        assert no_reason.json()["detail"] == {
            "field": "reason",
            "constraint": "required_when_reveal",
        }

    async def test_reveal_with_reason_is_audited_with_row_count(self, client: AsyncClient, sm):
        """A plaintext export writes one audit row with the filters and the actual row count."""
        from app.core.audit import AuditLog

        await _make_invoices(sm)
        fin = await admin_headers(sm, client, role="finance", username="fin-reveal-iv")
        resp = await client.get(
            "/api/admin/v1/invoices/export",
            params={"reveal": True, "reason": "month-end invoice check", "status": "submitted"},
            headers=fin,
        )
        assert resp.status_code == 200
        assert "示例科技(深圳)有限公司0号" in resp.text and "ap0@example.com" in resp.text  # cjk-ok

        async with sm() as session:
            row = (
                await session.execute(
                    select(AuditLog).where(AuditLog.target == "invoice-identity:export")
                )
            ).scalar_one()
        assert row.actor_type == "admin"
        assert row.detail == {
            "rows": 1,
            "reveal": True,
            "reason": "month-end invoice check",
            "status": "submitted",
            "period": None,
            "format": "csv",
        }
        assert row.request_id == resp.headers["x-request-id"]

    async def test_json_reveal_is_audited(self, client: AsyncClient, sm):
        """The JSON list and the CSV share the level: plaintext needs a reason + audit alike."""
        from app.core.audit import AuditLog

        await _make_invoices(sm)
        fin = await admin_headers(sm, client, role="finance", username="fin-reveal-json")
        resp = await client.get(
            "/api/admin/v1/invoices",
            params={"reveal": True, "reason": "support verifying the title"},
            headers=fin,
        )
        assert resp.status_code == 200
        assert any(r["title"] == "示例科技(深圳)有限公司0号" for r in resp.json())  # cjk-ok
        async with sm() as session:
            row = (
                await session.execute(
                    select(AuditLog).where(AuditLog.target == "invoice-identity:reveal")
                )
            ).scalar_one()
        assert row.detail is not None and row.detail["rows"] == 2
        assert row.detail["reason"] == "support verifying the title"


async def _make_adjustments(sm: async_sessionmaker[AsyncSession], count: int = 2) -> None:
    from app.modules.adminapi.models import AdminAdjustment

    async with sm() as session:
        for i in range(count):
            session.add(
                AdminAdjustment(
                    user_id=i + 1,
                    amount=Decimal("5.00") if i == 0 else Decimal("-3.00"),
                    reason=f"compensation ticket T2026{i}",
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
        assert lines[0].lstrip("﻿").startswith("ID,User ID")
        assert any(
            "compensation ticket T20260" in line and "Pending review" in line for line in lines
        )
        assert any("compensation ticket T20261" in line and "Effective" in line for line in lines)
        by_status = (
            await client.get(
                "/api/admin/v1/adjustments/export", params={"status": "approved"}, headers=fin
            )
        ).text
        assert (
            "compensation ticket T20261" in by_status
            and "compensation ticket T20260" not in by_status
        )
        by_uid = (
            await client.get("/api/admin/v1/adjustments/export", params={"user_id": 2}, headers=fin)
        ).text
        assert "compensation ticket T20261" in by_uid and "compensation ticket T20260" not in by_uid


class TestTenantLedgerExport:
    async def test_rows_match_ledger(self, client: AsyncClient, sm):
        data = await register(client, "13688880001")
        uid = data["user"]["id"]
        await fund_wallet(sm, uid, "66.00")
        ops = await admin_headers(sm, client, role="ops", username="ops-exp-ledger")
        resp = await client.get(f"/api/admin/v1/tenants/{uid}/ledger/export", headers=ops)
        assert resp.status_code == 200
        text = resp.text
        assert "Recharge" in text and "66.00" in text and "test-fund" in text
        assert csvexport.TRUNCATED_MARKER not in text
        user_headers = {"Authorization": f"Bearer {data['access_token']}"}
        mine_resp = await client.get("/api/v1/billing/export?dataset=ledger", headers=user_headers)
        assert len(mine_resp.text.splitlines()) == len(text.splitlines())


class TestAuditExport:
    async def test_filtered_rows_and_audited(self, client: AsyncClient, sm):
        from app.core.audit import AuditLog

        h = await admin_headers(sm, client)
        await register(client, "13688880002")
        await client.get("/api/admin/v1/tenants", params={"q": "13688880002"}, headers=h)

        resp = await client.get(
            "/api/admin/v1/audit/export", params={"actor_type": "admin"}, headers=h
        )
        assert resp.status_code == 200
        text = resp.text
        assert "tenant-search:" in text
        assert csvexport.TRUNCATED_MARKER not in text
        by_user = (
            await client.get("/api/admin/v1/audit/export", params={"actor_type": "user"}, headers=h)
        ).text
        assert "tenant-search:" not in by_user
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
        assert resp.text.splitlines()[1].startswith("TOTAL,")
        bad = await client.get(
            "/api/admin/v1/reconciliation/export", params={"day": "bad"}, headers=fin
        )
        assert bad.status_code == 400
