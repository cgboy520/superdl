"""Invoice loop: eligible definition / server-side amount / idempotency / issue and reject / IDOR /
refund interplay."""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.timeutil import now_utc
from app.modules.billing.models import InvoiceRequest, Order
from tests.helpers import (
    admin_headers,
    apply_refund,
    create_order,
    finance_pair,
    pay_mock,
    user_headers,
)


def past_period(months_ago: int = 1) -> tuple[str, datetime]:
    """The billing-zone period and the UTC instant of noon on the 15th of that month."""
    bj_first = (now_utc() + timedelta(hours=8)).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    for _ in range(months_ago):
        bj_first = (bj_first - timedelta(days=1)).replace(day=1)
    mid_bj = bj_first.replace(day=15, hour=12)
    return f"{mid_bj:%Y-%m}", mid_bj - timedelta(hours=8)


def current_period() -> str:
    return f"{now_utc() + timedelta(hours=8):%Y-%m}"


async def paid_order_at(
    client: AsyncClient, sm, headers: dict, amount: str, paid_at: datetime
) -> dict:
    """Complete a mock top-up and set paid_at, returning the order data."""
    order = await create_order(client, headers, amount)
    resp = await pay_mock(client, order["order_no"], amount)
    assert resp.status_code == 200, resp.text
    async with sm() as session:
        await session.execute(
            update(Order).where(Order.order_no == order["order_no"]).values(paid_at=paid_at)
        )
        await session.commit()
    return order


async def apply_invoice(
    client: AsyncClient,
    headers: dict,
    period: str,
    idem: str | None = None,
    **overrides: object,
):
    body: dict[str, object] = {
        "period": period,
        "title_type": "company",
        "title": "Example Tech Co., Ltd.",
        "tax_id": "91310000MA1K0000X0",
        "email": "ap@example.com",
    }
    body.update(overrides)
    h = {**headers, **({"Idempotency-Key": idem} if idem else {})}
    return await client.post("/api/v1/billing/invoices", json=body, headers=h)


async def eligible(client: AsyncClient, headers: dict) -> list[dict]:
    resp = await client.get("/api/v1/billing/invoices/eligible", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestEligible:
    async def test_channel_reversed_excluded(self, client: AsyncClient, sm):
        """Paid orders reversed by the channel do not count towards the invoiceable amount."""
        headers = await user_headers(client, "13700000203")
        p1, at1 = past_period(1)
        order = await paid_order_at(client, sm, headers, "50.00", at1)
        await paid_order_at(client, sm, headers, "30.00", at1)
        async with sm() as session:
            await session.execute(
                update(Order)
                .where(Order.order_no == order["order_no"])
                .values(channel_reversed_at=now_utc())
            )
            await session.commit()
        rows = await eligible(client, headers)
        assert [(r["period"], r["amount"]) for r in rows] == [(p1, "30.00")]

    async def test_grouped_by_period_and_summed(self, client: AsyncClient, sm):
        """Σpaid grouped by billing-zone period: two periods aggregated separately, descending,
        amounts as strings."""
        headers = await user_headers(client, "13700000201")
        p1, at1 = past_period(1)
        p2, at2 = past_period(2)
        await paid_order_at(client, sm, headers, "50.00", at1)
        await paid_order_at(client, sm, headers, "30.00", at1)
        await paid_order_at(client, sm, headers, "20.00", at2)
        rows = await eligible(client, headers)
        assert [(r["period"], r["amount"]) for r in rows] == [(p1, "80.00"), (p2, "20.00")]

    async def test_submitted_and_issued_subtract(self, client: AsyncClient, sm):
        """Used amounts (submitted/issued) are subtracted; after a full request the period
        disappears."""
        headers = await user_headers(client, "13700000202")
        p1, at1 = past_period(1)
        await paid_order_at(client, sm, headers, "50.00", at1)
        resp = await apply_invoice(client, headers, p1)
        assert resp.status_code == 201, resp.text
        assert resp.json()["amount"] == "50.00"
        assert await eligible(client, headers) == []
        finance = await admin_headers(sm, client, role="finance")
        resp = await client.post(
            f"/api/admin/v1/invoices/{resp.json()['id']}/issue",
            json={"invoice_no": "NO-2026-001"},
            headers=finance,
        )
        assert resp.status_code == 200, resp.text
        assert await eligible(client, headers) == []

    async def test_current_period_not_eligible(self, client: AsyncClient, sm):
        """The current billing month is not eligible."""
        headers = await user_headers(client, "13700000203")
        order = await create_order(client, headers, "50.00")
        assert (await pay_mock(client, order["order_no"], "50.00")).status_code == 200
        assert await eligible(client, headers) == []


class TestRefundDeduction:
    """Refunds are subtracted from the invoiceable amount of the order's payment period: paid and
    in flight alike, attribution by the order's paid_at only."""

    async def _approve_and_payout(self, client, sm, rid: int) -> tuple[dict, dict]:
        """Review + register the payout (payout lands in the current period). Returns (reviewer,
        payer) headers."""
        reviewer, payer = await finance_pair(sm, client)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "approved"},
            headers=reviewer,
        )
        assert resp.status_code == 200, resp.text
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/payout",
            json={"channel": "offline", "ref": "OFF-T"},
            headers=payer,
        )
        assert resp.status_code == 200, resp.text
        return reviewer, payer

    async def _refund_paid(self, client, sm, headers, order_no: str, amount: str) -> None:
        rid = (await apply_refund(client, headers, order_no, amount)).json()["id"]
        await self._approve_and_payout(client, sm, rid)

    async def test_refund_reduces_eligible_amount(self, client: AsyncClient, sm):
        headers = await user_headers(client, "13700000204")
        p1, at1 = past_period(1)
        order = await paid_order_at(client, sm, headers, "50.00", at1)
        await self._refund_paid(client, sm, headers, order["order_no"], "20.00")
        rows = await eligible(client, headers)
        assert [(r["period"], r["amount"]) for r in rows] == [(p1, "30.00")]

    async def test_full_refund_leaves_nothing_to_bill(self, client: AsyncClient, sm):
        """After a full refund: the period leaves eligible and create is stopped by the server-side
        amount."""
        headers = await user_headers(client, "13700000205")
        p1, at1 = past_period(1)
        order = await paid_order_at(client, sm, headers, "50.00", at1)
        await self._refund_paid(client, sm, headers, order["order_no"], "50.00")
        assert await eligible(client, headers) == []
        resp = await apply_invoice(client, headers, p1)
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.invoiceNothingToBill"

    async def test_pending_to_paid_across_months_keeps_order_period(self, client, sm):
        """Refunds of P1 orders, pending and paid, are subtracted from P1; an invoice requested at
        the pre-deducted amount recomputes consistently at issue."""
        headers = await user_headers(client, "13700000206")
        p1, at1 = past_period(1)
        order = await paid_order_at(client, sm, headers, "50.00", at1)
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        assert [(r["period"], r["amount"]) for r in await eligible(client, headers)] == [
            (p1, "30.00")
        ]
        resp = await apply_invoice(client, headers, p1)
        assert resp.status_code == 201, resp.text
        assert resp.json()["amount"] == "30.00"
        reviewer, _payer = await self._approve_and_payout(client, sm, rid)
        assert await eligible(client, headers) == []
        resp = await client.post(
            f"/api/admin/v1/invoices/{resp.json()['id']}/issue",
            json={"invoice_no": "NO-CROSS-MONTH"},
            headers=reviewer,
        )
        assert resp.status_code == 200, resp.text


class TestCreate:
    async def test_amount_computed_server_side(self, client: AsyncClient, sm):
        """The amount is computed server-side: a smuggled amount field is ignored, the response
        carries the full period amount."""
        headers = await user_headers(client, "13700000211")
        p1, at1 = past_period(1)
        await paid_order_at(client, sm, headers, "66.00", at1)
        resp = await apply_invoice(client, headers, p1, amount="0.01")
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["amount"] == "66.00"
        assert body["status"] == "submitted"
        assert body["period"] == p1

    async def test_duplicate_period_rejected(self, client: AsyncClient, sm):
        """An active request for the same period: a second request → 409 (a different idempotency
        key does not help)."""
        headers = await user_headers(client, "13700000212")
        p1, at1 = past_period(1)
        await paid_order_at(client, sm, headers, "50.00", at1)
        assert (await apply_invoice(client, headers, p1)).status_code == 201
        resp = await apply_invoice(client, headers, p1, idem="inv-dup")
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.invoicePeriodAlreadyApplied"

    async def test_company_title_requires_tax_id(self, client: AsyncClient):
        headers = await user_headers(client, "13700000213")
        p1, _ = past_period(1)
        resp = await apply_invoice(client, headers, p1, tax_id=None)
        assert resp.status_code == 422
        resp = await apply_invoice(client, headers, p1, tax_id="   ")
        assert resp.status_code == 422

    async def test_company_tax_id_format_follows_compliance_profile(
        self, client: AsyncClient, sm, monkeypatch
    ):
        """cn profile: the 18-character unified social credit code (upper-cased on input, GB 32100
        alphabet) is the only accepted form; the generic profile stores any 2–32 character ID."""
        headers = await user_headers(client, "13700000217")
        other = await user_headers(client, "13700000218")
        p1, at1 = past_period(1)
        await paid_order_at(client, sm, headers, "50.00", at1)
        await paid_order_at(client, sm, other, "50.00", at1)
        monkeypatch.setattr(get_settings(), "compliance_profile", "cn")
        for bad in ("TAX-123", "91310000MA1K0000XI"):
            resp = await apply_invoice(client, headers, p1, tax_id=bad)
            assert resp.status_code == 400, bad
            assert resp.json()["message_key"] == "billing.invoiceTaxIdInvalidCn"
        resp = await apply_invoice(client, headers, p1, tax_id="91310000ma1k0000x0")
        assert resp.status_code == 201, resp.text
        assert resp.json()["tax_id"] == "91310000MA1K0000X0"

        monkeypatch.setattr(get_settings(), "compliance_profile", None)
        resp = await apply_invoice(client, other, p1, tax_id="X")
        assert resp.status_code == 422
        resp = await apply_invoice(client, other, p1, tax_id="DE123456789")
        assert resp.status_code == 201, resp.text
        assert resp.json()["tax_id"] == "DE123456789"

    async def test_personal_title_needs_no_tax_id(self, client: AsyncClient, sm):
        """Personal title: no tax id needed, a smuggled one is not stored."""
        headers = await user_headers(client, "13700000215")
        p1, at1 = past_period(1)
        await paid_order_at(client, sm, headers, "50.00", at1)
        resp = await apply_invoice(
            client,
            headers,
            p1,
            title_type="personal",
            title="Alice Example",
            tax_id="SHOULD-BE-DROPPED",
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["tax_id"] is None

    async def test_current_period_rejected(self, client: AsyncClient, sm):
        """The current month cannot be invoiced (even with paid orders this month)."""
        headers = await user_headers(client, "13700000216")
        order = await create_order(client, headers, "50.00")
        assert (await pay_mock(client, order["order_no"], "50.00")).status_code == 200
        resp = await apply_invoice(client, headers, current_period())
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "billing.invoicePeriodNotOpen"

    async def test_nothing_to_bill_rejected(self, client: AsyncClient, sm):
        """Period without paid top-ups: invoiceable amount 0, not accepted."""
        headers = await user_headers(client, "13700000217")
        p1, _ = past_period(1)
        resp = await apply_invoice(client, headers, p1)
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.invoiceNothingToBill"

    async def test_idempotent_replay_returns_same(self, client: AsyncClient, sm):
        headers = await user_headers(client, "13700000218")
        p1, at1 = past_period(1)
        await paid_order_at(client, sm, headers, "50.00", at1)
        r1 = await apply_invoice(client, headers, p1, idem="inv-1")
        r2 = await apply_invoice(client, headers, p1, idem="inv-1")
        assert r1.status_code == 201 and r2.status_code == 200
        assert r2.headers["x-idempotent-replay"] == "true"
        assert r2.json()["id"] == r1.json()["id"]
        async with sm() as session:
            rows = (await session.execute(select(InvoiceRequest))).scalars().all()
        assert len(rows) == 1

    async def test_idem_key_param_mismatch_409(self, client: AsyncClient, sm):
        """Same key, different params (title changed): 409."""
        headers = await user_headers(client, "13700000219")
        p1, at1 = past_period(1)
        await paid_order_at(client, sm, headers, "50.00", at1)
        r1 = await apply_invoice(client, headers, p1, idem="inv-mix")
        assert r1.status_code == 201
        r2 = await apply_invoice(
            client, headers, p1, idem="inv-mix", title="Renamed Tech Co., Ltd."
        )
        assert r2.status_code == 409
        assert r2.json()["message_key"] == "common.idempotencyKeyMismatch"


class TestAdminFlow:
    async def _submitted(self, client: AsyncClient, sm, phone: str) -> tuple[dict, int, str]:
        headers = await user_headers(client, phone)
        p1, at1 = past_period(1)
        await paid_order_at(client, sm, headers, "50.00", at1)
        resp = await apply_invoice(client, headers, p1)
        assert resp.status_code == 201, resp.text
        return headers, resp.json()["id"], p1

    async def test_issue_fills_no_and_notifies(self, client: AsyncClient, sm):
        """Issue: status=issued + issued_by/at + in-app notification (with the invoice number)."""
        headers, iid, p1 = await self._submitted(client, sm, "13700000221")
        finance = await admin_headers(sm, client, role="finance")
        resp = await client.post(
            f"/api/admin/v1/invoices/{iid}/issue",
            json={"invoice_no": "24XXXXXX01"},
            headers=finance,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "issued"
        assert body["invoice_no"] == "24XXXXXX01"
        assert body["issued_by"] is not None and body["issued_at"] is not None
        async with sm() as session:
            req = await session.get(InvoiceRequest, iid)
            assert req is not None and req.period == p1
        notes = (await client.get("/api/v1/notifications", headers=headers)).json()["items"]
        issued = next(n for n in notes if n["type"] == "invoice")
        assert "24XXXXXX01" in issued["content"]

    async def test_issue_non_submitted_conflict(self, client: AsyncClient, sm):
        _headers, iid, _p1 = await self._submitted(client, sm, "13700000222")
        finance = await admin_headers(sm, client, role="finance")
        resp = await client.post(
            f"/api/admin/v1/invoices/{iid}/issue", json={"invoice_no": "NO-1"}, headers=finance
        )
        assert resp.status_code == 200
        resp = await client.post(
            f"/api/admin/v1/invoices/{iid}/issue", json={"invoice_no": "NO-2"}, headers=finance
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.invoiceStateNotIssuable"

    async def test_reject_notifies_with_reason(self, client: AsyncClient, sm):
        """Reject: the in-app notification carries the reason."""
        headers, iid, _p1 = await self._submitted(client, sm, "13700000223")
        finance = await admin_headers(sm, client, role="finance")
        resp = await client.post(
            f"/api/admin/v1/invoices/{iid}/reject",
            json={"reason": "title does not match the verified identity"},
            headers=finance,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "rejected"
        assert resp.json()["reject_reason"] == "title does not match the verified identity"
        notes = (await client.get("/api/v1/notifications", headers=headers)).json()["items"]
        rejected = next(n for n in notes if n["type"] == "invoice")
        assert "title does not match the verified identity" in rejected["content"]

    async def test_rejected_period_can_reapply(self, client: AsyncClient, sm):
        """rejected does not hold the partial unique index: the period can be requested again, still
        at the full amount."""
        headers = await user_headers(client, "13700000224")
        p1, at1 = past_period(1)
        await paid_order_at(client, sm, headers, "50.00", at1)
        iid = (await apply_invoice(client, headers, p1)).json()["id"]
        finance = await admin_headers(sm, client, role="finance")
        resp = await client.post(
            f"/api/admin/v1/invoices/{iid}/reject",
            json={"reason": "tax id is wrong, please correct it"},
            headers=finance,
        )
        assert resp.status_code == 200
        resp = await apply_invoice(client, headers, p1, idem="inv-re")
        assert resp.status_code == 201, resp.text
        assert resp.json()["amount"] == "50.00"
        mine = (await client.get("/api/v1/billing/invoices", headers=headers)).json()
        rejected = next(r for r in mine["items"] if r["status"] == "rejected")
        assert rejected["reject_reason"] == "tax id is wrong, please correct it"

    async def test_list_filters(self, client: AsyncClient, sm):
        """status/period exact filters; the admin view carries the tenant id."""
        headers = await user_headers(client, "13700000225")
        p1, at1 = past_period(1)
        p2, at2 = past_period(2)
        await paid_order_at(client, sm, headers, "50.00", at1)
        await paid_order_at(client, sm, headers, "20.00", at2)
        await apply_invoice(client, headers, p1)
        await apply_invoice(client, headers, p2, idem="inv-p2")
        finance = await admin_headers(sm, client, role="finance")
        rows = (await client.get("/api/admin/v1/invoices", headers=finance)).json()
        assert len(rows) == 2
        rows = (
            await client.get("/api/admin/v1/invoices", params={"period": p1}, headers=finance)
        ).json()
        assert len(rows) == 1 and rows[0]["period"] == p1
        assert rows[0]["user_id"] is not None
        rows = (
            await client.get("/api/admin/v1/invoices", params={"status": "issued"}, headers=finance)
        ).json()
        assert rows == []


class TestIdor:
    @pytest.mark.parametrize("probe", ["list", "eligible"])
    async def test_other_users_invoice_invisible(self, client: AsyncClient, sm, probe: str):
        """User B sees neither user A's list nor A's invoiceable periods."""
        ha = await user_headers(client, "13700000231")
        p1, at1 = past_period(1)
        await paid_order_at(client, sm, ha, "50.00", at1)
        assert (await apply_invoice(client, ha, p1)).status_code == 201
        hb = await user_headers(client, "13700000232")
        if probe == "list":
            mine = (await client.get("/api/v1/billing/invoices", headers=hb)).json()
            assert mine["items"] == []
        else:
            assert await eligible(client, hb) == []


class TestRefundLinkage:
    """Refund interplay: paid orders of an issued period cannot be refunded before a credit note;
    submitted does not block."""

    async def test_issued_period_blocks_refund(self, client: AsyncClient, sm):
        headers = await user_headers(client, "13700000241")
        p1, at1 = past_period(1)
        order = await paid_order_at(client, sm, headers, "50.00", at1)
        iid = (await apply_invoice(client, headers, p1)).json()["id"]
        finance = await admin_headers(sm, client, role="finance")
        resp = await client.post(
            f"/api/admin/v1/invoices/{iid}/issue",
            json={"invoice_no": "NO-BLOCK-1"},
            headers=finance,
        )
        assert resp.status_code == 200
        resp = await client.post(
            "/api/v1/wallet/refunds",
            json={
                "order_no": order["order_no"],
                "amount": "50.00",
                "reason": "unused, requesting a refund",
            },
            headers=headers,
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.refundInvoiceIssued"
        rows = (await client.get("/api/v1/wallet/refunds/eligible-orders", headers=headers)).json()
        row = next(r for r in rows if r["order_no"] == order["order_no"])
        assert row["refundable"] is False
        assert row["reason_code"] == "invoiced"

    async def test_submitted_period_does_not_block_refund(self, client: AsyncClient, sm):
        headers = await user_headers(client, "13700000242")
        p1, at1 = past_period(1)
        order = await paid_order_at(client, sm, headers, "50.00", at1)
        assert (await apply_invoice(client, headers, p1)).status_code == 201
        resp = await client.post(
            "/api/v1/wallet/refunds",
            json={
                "order_no": order["order_no"],
                "amount": "20.00",
                "reason": "unused, requesting a refund",
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        p2, at2 = past_period(2)
        await paid_order_at(client, sm, headers, "10.00", at2)
        iid2 = (await apply_invoice(client, headers, p2, idem="inv-other")).json()["id"]
        finance = await admin_headers(sm, client, role="finance")
        resp = await client.post(
            f"/api/admin/v1/invoices/{iid2}/issue",
            json={"invoice_no": "NO-OTHER"},
            headers=finance,
        )
        assert resp.status_code == 200
        resp = await client.post(
            "/api/v1/wallet/refunds",
            json={"order_no": order["order_no"], "amount": "10.00", "reason": "one more refund"},
            headers=headers,
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.refundAlreadyApplied"


class TestDoubleSpendGate:
    """Double-spend gate between invoice and refund: in-flight refunds pre-deducted + recomputation
    at issue; refund requests and issuing serialise on the invoice row lock."""

    async def test_pending_refund_withheld_from_eligible_and_create(self, client: AsyncClient, sm):
        """In-flight (pending) refunds are pre-deducted from the order's period: the eligible
        preview
        and the create amount shrink together."""
        headers = await user_headers(client, "13700000243")
        p1, at1 = past_period(1)
        order = await paid_order_at(client, sm, headers, "50.00", at1)
        resp = await client.post(
            "/api/v1/wallet/refunds",
            json={"order_no": order["order_no"], "amount": "20.00", "reason": "partial refund"},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        assert [(r["period"], r["amount"]) for r in await eligible(client, headers)] == [
            (p1, "30.00")
        ]
        resp = await apply_invoice(client, headers, p1)
        assert resp.status_code == 201, resp.text
        assert resp.json()["amount"] == "30.00"

    async def test_issue_recalculates_and_rejects_stale_amount(self, client: AsyncClient, sm):
        """An in-flight refund between request and issue: the recomputation under the issue row lock
        mismatches → 409 invoiceAmountStale."""
        headers = await user_headers(client, "13700000244")
        p1, at1 = past_period(1)
        order = await paid_order_at(client, sm, headers, "50.00", at1)
        iid = (await apply_invoice(client, headers, p1)).json()["id"]
        resp = await client.post(
            "/api/v1/wallet/refunds",
            json={"order_no": order["order_no"], "amount": "20.00", "reason": "partial refund"},
            headers=headers,
        )
        assert resp.status_code == 201
        finance = await admin_headers(sm, client, role="finance")
        resp = await client.post(
            f"/api/admin/v1/invoices/{iid}/issue",
            json={"invoice_no": "NO-STALE-1"},
            headers=finance,
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.invoiceAmountStale"
        resp = await client.post(
            f"/api/admin/v1/invoices/{iid}/reject",
            json={"reason": "a refund occurred within the period, the amount changed"},
            headers=finance,
        )
        assert resp.status_code == 200
        resp = await apply_invoice(client, headers, p1, idem="inv-reapply")
        assert resp.status_code == 201
        assert resp.json()["amount"] == "30.00"

    async def test_payout_succeeds_after_invoice_issued_with_refund_withheld(
        self, client: AsyncClient, sm
    ):
        """Request a refund → issue (invoice amount minus the in-flight refund) → register the
        payout
        successfully."""
        headers = await user_headers(client, "13700000245")
        p1, at1 = past_period(1)
        order = await paid_order_at(client, sm, headers, "50.00", at1)
        rid = (
            await client.post(
                "/api/v1/wallet/refunds",
                json={"order_no": order["order_no"], "amount": "30.00", "reason": "partial refund"},
                headers=headers,
            )
        ).json()["id"]
        reviewer, payer = await finance_pair(sm, client)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "approved"},
            headers=reviewer,
        )
        assert resp.status_code == 200
        resp = await apply_invoice(client, headers, p1)
        assert resp.status_code == 201, resp.text
        assert resp.json()["amount"] == "20.00"
        resp = await client.post(
            f"/api/admin/v1/invoices/{resp.json()['id']}/issue",
            json={"invoice_no": "NO-GATE-1"},
            headers=reviewer,
        )
        assert resp.status_code == 200, resp.text
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/payout",
            json={"channel": "offline", "ref": "OFF-GATE"},
            headers=payer,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "paid"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "20.00"
        assert await eligible(client, headers) == []

    async def test_refund_apply_serializes_with_issue(self, client: AsyncClient, sm):
        """A refund request takes FOR UPDATE on the period's active invoice row: it blocks while the
        issue holds the lock and is rejected once the issue commits."""
        from app.core.errors import AppError
        from app.modules.billing import refunds

        headers = await user_headers(client, "13700000246")
        p1, at1 = past_period(1)
        order = await paid_order_at(client, sm, headers, "50.00", at1)
        iid = (await apply_invoice(client, headers, p1)).json()["id"]
        async with sm() as session:
            uid = (
                await session.execute(
                    select(Order.user_id).where(Order.order_no == order["order_no"])
                )
            ).scalar_one()
        async with sm() as issuing, sm() as applying:
            req = (
                await issuing.execute(
                    select(InvoiceRequest).where(InvoiceRequest.id == iid).with_for_update()
                )
            ).scalar_one()
            task = asyncio.create_task(
                refunds.create_refund(
                    applying,
                    uid,
                    order_no=order["order_no"],
                    amount=Decimal("20.00"),
                    reason="partial refund",
                    idempotency_key=None,
                )
            )
            try:
                await asyncio.sleep(0.3)
                assert not task.done()
                req.status = "issued"
                req.invoice_no = "NO-RACE"
                await issuing.commit()
                with pytest.raises(AppError) as exc:
                    await task
                assert exc.value.message_key == "billing.refundInvoiceIssued"
            finally:
                if not task.done():
                    task.cancel()
