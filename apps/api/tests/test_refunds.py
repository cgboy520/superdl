"""Refund loop: request rules / idempotency / review → payout / two-person rule / balance re-check /
IDOR / channel reversal block."""

import re
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.timeutil import now_utc
from app.modules.billing import service as billing_service
from app.modules.billing.models import BalanceLedger, Order, RefundRequest
from tests.helpers import (
    admin_headers,
    apply_refund,
    create_order,
    finance_pair,
    paid_order,
    user_headers,
)

REFUND_NO_RE = re.compile(r"^R\d{8}-\d{2}$")


class TestSyncAudit:
    """Synchronous payout audit: a failed audit write rolls the payout back; on success audit and
    business share the transaction."""

    async def _approved_refund(self, client, sm, phone: str, payer_name: str) -> tuple[dict, int]:
        headers = await user_headers(client, phone)
        order = await paid_order(client, headers)
        rid = (await apply_refund(client, headers, order["order_no"], "30.00")).json()["id"]
        reviewer = await admin_headers(sm, client, role="finance")
        payer = await admin_headers(sm, client, role="finance", username=payer_name)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "approved"},
            headers=reviewer,
        )
        assert resp.status_code == 200, resp.text
        return payer, rid

    async def test_audit_writer_failure_rolls_back_payout(self, client, sm, monkeypatch):
        """Audit write failure → payout rolled back: 500, wallet untouched, refund still approved;
        the retry succeeds once the fault clears."""
        from app.modules.adminapi import router_finance

        payer, rid = await self._approved_refund(
            client, sm, "u13700000160@test.local", "finance-payer-a"
        )

        async def boom(request, session, *, result=200):
            raise RuntimeError("audit write failed (injected)")

        monkeypatch.setattr(router_finance, "write_audit_sync", boom)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/payout",
            json={"channel": "offline", "ref": "OFF-AUDIT"},
            headers=payer,
        )
        assert resp.status_code == 500
        async with sm() as session:
            req = await session.get(RefundRequest, rid)
            entries = (
                (await session.execute(select(BalanceLedger).where(BalanceLedger.type == "refund")))
                .scalars()
                .all()
            )
        assert req is not None and req.status == "approved"
        assert entries == []
        monkeypatch.undo()
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/payout",
            json={"channel": "offline", "ref": "OFF-AUDIT"},
            headers=payer,
        )
        assert resp.status_code == 200, resp.text

    async def test_payout_audit_single_row_same_transaction(self, client, sm):
        """Successful payout: exactly one audit row, detail carries channel / reference."""
        from app.core.audit import AuditLog

        payer, rid = await self._approved_refund(
            client, sm, "u13700000161@test.local", "finance-payer-b"
        )
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/payout",
            json={"channel": "offline", "ref": "OFF-SYNC"},
            headers=payer,
        )
        assert resp.status_code == 200, resp.text
        async with sm() as session:
            rows = (
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.action == f"admin.POST /api/admin/v1/refunds/{rid}/payout"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1
        assert rows[0].detail == {"channel": "offline", "ref": "OFF-SYNC"}
        assert rows[0].result == 200


class TestApply:
    async def test_create_success_and_no_format(self, client: AsyncClient, sm):
        headers = await user_headers(client, "u13700000101@test.local")
        order = await paid_order(client, headers)
        resp = await apply_refund(client, headers, order["order_no"], "30.00")
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert REFUND_NO_RE.match(body["refund_no"]), body["refund_no"]
        assert body["status"] == "pending"
        assert body["amount"] == "30.00"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "50.00"

    async def test_idempotent_replay_returns_same(self, client: AsyncClient, sm):
        headers = await user_headers(client, "u13700000102@test.local")
        order = await paid_order(client, headers)
        r1 = await apply_refund(client, headers, order["order_no"], "20.00", idem="rf-1")
        r2 = await apply_refund(client, headers, order["order_no"], "20.00", idem="rf-1")
        assert r1.status_code == 201 and r2.status_code == 200
        assert r2.headers["x-idempotent-replay"] == "true"
        assert r2.json()["refund_no"] == r1.json()["refund_no"]
        async with sm() as session:
            rows = (await session.execute(select(RefundRequest))).scalars().all()
        assert len(rows) == 1

    async def test_non_paid_order_rejected(self, client: AsyncClient, sm):
        headers = await user_headers(client, "u13700000103@test.local")
        order = await create_order(client, headers, "50.00")
        resp = await apply_refund(client, headers, order["order_no"])
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.refundOrderNotPaid"

    async def test_channel_reversed_order_rejected(self, client: AsyncClient, sm):
        """Orders reversed by the channel cannot be paid out."""
        headers = await user_headers(client, "u13700000105@test.local")
        order = await paid_order(client, headers, "50.00")
        async with sm() as session:
            await session.execute(
                update(Order)
                .where(Order.order_no == order["order_no"])
                .values(channel_reversed_at=now_utc())
            )
            await session.commit()
        resp = await apply_refund(client, headers, order["order_no"], "50.00")
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.refundChannelReversed"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "50.00"

    async def test_amount_capped_by_min_of_order_and_balance(self, client: AsyncClient, sm):
        """Cap = min(order amount, current balance): above the order and above the balance each
        rejected once."""
        headers = await user_headers(client, "u13700000104@test.local")
        order = await paid_order(client, headers, "50.00")
        resp = await apply_refund(client, headers, order["order_no"], "50.01")
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "billing.refundAmountExceeded"
        async with sm() as session:
            from app.modules.billing.models import Order

            uid = (
                await session.execute(
                    select(Order.user_id).where(Order.order_no == order["order_no"])
                )
            ).scalar_one()
            await billing_service.debit(
                session, uid, Decimal("40.00"), type_="consume", allow_negative=True
            )
            await session.commit()
        resp = await apply_refund(client, headers, order["order_no"], "20.00")
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "billing.refundAmountExceeded"
        resp = await apply_refund(client, headers, order["order_no"], "10.00")
        assert resp.status_code == 201, resp.text

    async def test_duplicate_application_rejected(self, client: AsyncClient, sm):
        """A second request while the order has an active one → 409 (a different idempotency key
        does not help)."""
        headers = await user_headers(client, "u13700000105@test.local")
        order = await paid_order(client, headers)
        assert (await apply_refund(client, headers, order["order_no"], "10.00")).status_code == 201
        resp = await apply_refund(client, headers, order["order_no"], "10.00", idem="rf-dup")
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.refundAlreadyApplied"

    async def test_idem_key_param_mismatch_409(self, client: AsyncClient, sm):
        """Same key, different params (amount changed): 409."""
        headers = await user_headers(client, "u13700000106@test.local")
        order = await paid_order(client, headers)
        r1 = await apply_refund(client, headers, order["order_no"], "20.00", idem="rf-mix")
        assert r1.status_code == 201
        r2 = await apply_refund(client, headers, order["order_no"], "21.00", idem="rf-mix")
        assert r2.status_code == 409
        assert r2.json()["message_key"] == "common.idempotencyKeyMismatch"


class TestAdminFlow:
    async def test_review_then_payout_full_flow(self, client: AsyncClient, sm):
        """Review → payout registration: negative wallet adjustment + ledger type=refund +
        balance_after snapshot + back-link."""
        headers = await user_headers(client, "u13700000111@test.local")
        order = await paid_order(client, headers, "50.00")
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        reviewer, payer = await finance_pair(sm, client)

        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "verified, approved"},
            headers=reviewer,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "approved"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "50.00"

        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/payout",
            json={"channel": "alipay_transfer", "ref": "ALI-PAY-20260823-001"},
            headers=payer,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "paid"
        assert body["payout_channel"] == "alipay_transfer"
        assert body["wallet_entry_id"] is not None

        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "30.00"
        async with sm() as session:
            entry = (
                await session.execute(select(BalanceLedger).where(BalanceLedger.type == "refund"))
            ).scalar_one()
            assert entry.amount == Decimal("-20.00")
            assert entry.balance_after == Decimal("30.00")
            assert entry.ref_type == "refund_request"
            req = await session.get(RefundRequest, rid)
            assert req is not None and req.wallet_entry_id == entry.id
            assert req.review_by is not None and req.payout_by is not None
            assert req.review_by != req.payout_by

    async def test_payout_rejected_when_reversed_after_approval(self, client: AsyncClient, sm):
        """Reversed by the channel after approval: the payout re-checks and refuses."""
        headers = await user_headers(client, "u13700000114@test.local")
        order = await paid_order(client, headers, "50.00")
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        reviewer, payer = await finance_pair(sm, client)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "approved"},
            headers=reviewer,
        )
        assert resp.status_code == 200
        async with sm() as session:
            await session.execute(
                update(Order)
                .where(Order.order_no == order["order_no"])
                .values(channel_reversed_at=now_utc())
            )
            await session.commit()
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/payout",
            json={"channel": "offline", "ref": "OFF-002"},
            headers=payer,
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.refundChannelReversed"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "50.00"

    async def test_payout_same_person_rejected(self, client: AsyncClient, sm):
        """Two-person rule: payer == reviewer → 409."""
        headers = await user_headers(client, "u13700000112@test.local")
        order = await paid_order(client, headers)
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        reviewer, _payer = await finance_pair(sm, client)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "approved"},
            headers=reviewer,
        )
        assert resp.status_code == 200
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/payout",
            json={"channel": "offline", "ref": "OFF-001"},
            headers=reviewer,
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.refundPayoutSamePerson"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "50.00"

    async def test_payout_idempotent_replay(self, client: AsyncClient, sm):
        """Payout Idempotency-Key: same key and params replays 200 + X-Idempotent-Replay without a
        second payout."""
        headers = await user_headers(client, "u13700000141@test.local")
        order = await paid_order(client, headers, "50.00")
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        reviewer, payer = await finance_pair(sm, client)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "approved"},
            headers=reviewer,
        )
        assert resp.status_code == 200

        idem = {"Idempotency-Key": "payout-key-001"}
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/payout",
            json={"channel": "offline", "ref": "OFF-100"},
            headers={**payer, **idem},
        )
        assert resp.status_code == 200, resp.text
        assert "x-idempotent-replay" not in {k.lower() for k in resp.headers}
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "30.00"

        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/payout",
            json={"channel": "offline", "ref": "OFF-100"},
            headers={**payer, **idem},
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers.get("x-idempotent-replay") == "true"
        assert resp.json()["status"] == "paid"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "30.00"

    async def test_payout_idem_key_param_mismatch_409(self, client: AsyncClient, sm):
        """Same key, different params (reference changed): 409."""
        headers = await user_headers(client, "u13700000142@test.local")
        order = await paid_order(client, headers, "50.00")
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        reviewer, payer = await finance_pair(sm, client)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "approved"},
            headers=reviewer,
        )
        assert resp.status_code == 200
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/payout",
            json={"channel": "offline", "ref": "OFF-200"},
            headers={**payer, "Idempotency-Key": "payout-key-002"},
        )
        assert resp.status_code == 200, resp.text
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/payout",
            json={"channel": "offline", "ref": "OFF-201"},
            headers={**payer, "Idempotency-Key": "payout-key-002"},
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "common.idempotencyKeyMismatch"

    async def test_payout_repeat_without_key_still_409(self, client: AsyncClient, sm):
        """Repeated payout without a key: state machine 409."""
        headers = await user_headers(client, "u13700000143@test.local")
        order = await paid_order(client, headers, "50.00")
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        reviewer, payer = await finance_pair(sm, client)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "approved"},
            headers=reviewer,
        )
        assert resp.status_code == 200
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/payout",
            json={"channel": "offline", "ref": "OFF-300"},
            headers=payer,
        )
        assert resp.status_code == 200, resp.text
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/payout",
            json={"channel": "offline", "ref": "OFF-300"},
            headers=payer,
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.refundStateNotPayable"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "30.00"

    async def test_payout_balance_consumed_then_cancel(self, client: AsyncClient, sm):
        """Balance consumed after approval → payout 409 (balance consumed); cancel the request, the
        balance is untouched."""
        headers = await user_headers(client, "u13700000113@test.local")
        order = await paid_order(client, headers, "50.00")
        rid = (await apply_refund(client, headers, order["order_no"], "40.00")).json()["id"]
        reviewer, payer = await finance_pair(sm, client)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "approved"},
            headers=reviewer,
        )
        assert resp.status_code == 200
        async with sm() as session:
            from app.modules.billing.models import Order

            uid = (
                await session.execute(
                    select(Order.user_id).where(Order.order_no == order["order_no"])
                )
            ).scalar_one()
            await billing_service.debit(
                session, uid, Decimal("40.00"), type_="consume", allow_negative=True
            )
            await session.commit()
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/payout",
            json={"channel": "wechat_transfer", "ref": "WX-001"},
            headers=payer,
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.refundBalanceConsumed"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "10.00"
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/cancel",
            json={"reason": "balance consumed, cancelled in agreement with the user"},
            headers=payer,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "cancelled"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "10.00"

    async def test_cancel_terminal_state_rejected(self, client: AsyncClient, sm):
        """Cancel only pending/approved: paid cannot be cancelled."""
        headers = await user_headers(client, "u13700000114@test.local")
        order = await paid_order(client, headers)
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        reviewer, payer = await finance_pair(sm, client)
        await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "approved"},
            headers=reviewer,
        )
        await client.post(
            f"/api/admin/v1/refunds/{rid}/payout",
            json={"channel": "offline", "ref": "OFF-9"},
            headers=payer,
        )
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/cancel",
            json={"reason": "cancelling a terminal state"},
            headers=payer,
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.refundStateNotCancellable"

    async def test_rejected_order_can_reapply(self, client: AsyncClient, sm):
        """A rejection does not hold the partial unique index: the order can be requested again."""
        headers = await user_headers(client, "u13700000115@test.local")
        order = await paid_order(client, headers)
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        reviewer, _payer = await finance_pair(sm, client)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": False, "comment": "insufficient grounds, rejected"},
            headers=reviewer,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"
        resp = await apply_refund(client, headers, order["order_no"], "15.00", idem="rf-re")
        assert resp.status_code == 201, resp.text
        assert resp.json()["amount"] == "15.00"
        mine = (await client.get("/api/v1/wallet/refunds", headers=headers)).json()
        rejected = next(r for r in mine["items"] if r["status"] == "rejected")
        assert rejected["review_comment"] == "insufficient grounds, rejected"

    async def test_review_non_pending_rejected(self, client: AsyncClient, sm):
        headers = await user_headers(client, "u13700000116@test.local")
        order = await paid_order(client, headers)
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        reviewer, payer = await finance_pair(sm, client)
        await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "approved"},
            headers=reviewer,
        )
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": False, "comment": "duplicate review"},
            headers=payer,
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.refundStateNotReviewable"

    async def test_admin_list_filters(self, client: AsyncClient, sm):
        headers = await user_headers(client, "u13700000117@test.local")
        order = await paid_order(client, headers)
        await apply_refund(client, headers, order["order_no"], "20.00")
        reviewer, _payer = await finance_pair(sm, client)
        rows = (await client.get("/api/admin/v1/refunds", headers=reviewer)).json()["items"]
        assert len(rows) == 1 and rows[0]["status"] == "pending"
        assert (
            await client.get("/api/admin/v1/refunds", params={"status": "paid"}, headers=reviewer)
        ).json()["items"] == []
        assert rows[0]["user_id"] is not None


class TestPartialRefunds:
    """Several partial refunds: paid ones hold no slot, the order can be refunded up to its
    amount."""

    async def _review_and_payout(self, client, rid: int, reviewer: dict, payer: dict) -> None:
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "approved"},
            headers=reviewer,
        )
        assert resp.status_code == 200, resp.text
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/payout",
            json={"channel": "offline", "ref": f"OFF-{rid}"},
            headers=payer,
        )
        assert resp.status_code == 200, resp.text

    async def test_second_partial_after_payout_up_to_order_amount(self, client: AsyncClient, sm):
        """A second request after the first payout: the two add up to the order amount; any further
        amount is rejected (cap 0)."""
        headers = await user_headers(client, "u13700000130@test.local")
        order = await paid_order(client, headers, "50.00")
        reviewer, payer = await finance_pair(sm, client)

        rid1 = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        await self._review_and_payout(client, rid1, reviewer, payer)
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "30.00"

        r2 = await apply_refund(client, headers, order["order_no"], "30.00", idem="rf-p2")
        assert r2.status_code == 201, r2.text
        await self._review_and_payout(client, r2.json()["id"], reviewer, payer)
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "0.00"

        r3 = await apply_refund(client, headers, order["order_no"], "0.01", idem="rf-p3")
        assert r3.status_code == 400
        assert r3.json()["message_key"] == "billing.refundAmountExceeded"

    async def test_payout_cumulative_guard_on_data_anomaly(self, client: AsyncClient, sm):
        """Order amount lowered after approval so the total would exceed it → payout 409."""
        headers = await user_headers(client, "u13700000131@test.local")
        order = await paid_order(client, headers, "50.00")
        reviewer, payer = await finance_pair(sm, client)
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "approved"},
            headers=reviewer,
        )
        assert resp.status_code == 200
        async with sm() as session:
            await session.execute(
                update(Order)
                .where(Order.order_no == order["order_no"])
                .values(amount=Decimal("10.00"))
            )
            await session.commit()
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/payout",
            json={"channel": "offline", "ref": "OFF-GUARD"},
            headers=payer,
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.refundCumulativeExceeded"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "50.00"

    async def test_eligible_orders_fully_refunded(self, client: AsyncClient, sm):
        """Candidate view: fully refunded orders are greyed fully_refunded; after a partial refund
        max_amount = remainder."""
        headers = await user_headers(client, "u13700000132@test.local")
        order = await paid_order(client, headers, "50.00")
        reviewer, payer = await finance_pair(sm, client)
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        await self._review_and_payout(client, rid, reviewer, payer)

        rows = (await client.get("/api/v1/wallet/refunds/eligible-orders", headers=headers)).json()
        row = next(r for r in rows if r["order_no"] == order["order_no"])
        assert row["refundable"] is True
        assert row["max_amount"] == "30.00"

        rid2 = (await apply_refund(client, headers, order["order_no"], "30.00")).json()["id"]
        await self._review_and_payout(client, rid2, reviewer, payer)
        rows = (await client.get("/api/v1/wallet/refunds/eligible-orders", headers=headers)).json()
        row = next(r for r in rows if r["order_no"] == order["order_no"])
        assert row["refundable"] is False
        assert row["reason_code"] == "fully_refunded"
        assert row["max_amount"] == "0.00"


class TestRefundStrategy:
    """Refund policy: refundable = Σ top-ups − Σ consumption (compensation credits cannot be
    withdrawn) + original channel."""

    async def test_compensation_credit_not_refundable(self, client: AsyncClient, sm):
        """Top up 50, spend it all + 50 compensation (balance 50): refundable 0, every request
        rejected."""
        headers = await user_headers(client, "u13700000201@test.local")
        order = await paid_order(client, headers, "50.00")
        async with sm() as session:
            uid = (
                await session.execute(
                    select(Order.user_id).where(Order.order_no == order["order_no"])
                )
            ).scalar_one()
            await billing_service.debit(
                session, uid, Decimal("50.00"), type_="consume", allow_negative=True
            )
            await billing_service.credit(session, uid, Decimal("50.00"), type_="adjust")
            await session.commit()
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "50.00"
        resp = await apply_refund(client, headers, order["order_no"], "1.00")
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "billing.refundAmountExceeded"
        eligible = (
            await client.get("/api/v1/wallet/refunds/eligible-orders", headers=headers)
        ).json()
        row = next(r for r in eligible if r["order_no"] == order["order_no"])
        assert row["max_amount"] == "0.00"
        assert row["refundable"] is False
        assert row["reason_code"] == "no_balance"

    async def test_payout_channel_must_match_order_channel(self, client: AsyncClient, sm):
        """Original channel: a WeChat order only allows wechat_transfer (or the offline exception),
        alipay_transfer is refused."""
        headers = await user_headers(client, "u13700000202@test.local")
        order = await paid_order(client, headers, "50.00")
        async with sm() as session:
            await session.execute(
                update(Order).where(Order.order_no == order["order_no"]).values(channel="wechat")
            )
            await session.commit()
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        reviewer, payer = await finance_pair(sm, client)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "approved"},
            headers=reviewer,
        )
        assert resp.status_code == 200, resp.text
        bad = await client.post(
            f"/api/admin/v1/refunds/{rid}/payout",
            json={"channel": "alipay_transfer", "ref": "ALI-1"},
            headers=payer,
        )
        assert bad.status_code == 409
        assert bad.json()["message_key"] == "billing.refundPayoutChannelMismatch"
        ok = await client.post(
            f"/api/admin/v1/refunds/{rid}/payout",
            json={"channel": "wechat_transfer", "ref": "WX-1"},
            headers=payer,
        )
        assert ok.status_code == 200, ok.text

    async def test_payout_refundable_gate_after_approval(self, client: AsyncClient, sm):
        """The user keeps spending between review and payout: refundable short, the payout is
        blocked
        (wallet untouched)."""
        headers = await user_headers(client, "u13700000203@test.local")
        order = await paid_order(client, headers, "50.00")
        async with sm() as session:
            uid = (
                await session.execute(
                    select(Order.user_id).where(Order.order_no == order["order_no"])
                )
            ).scalar_one()
            await billing_service.credit(session, uid, Decimal("50.00"), type_="adjust")
            await session.commit()
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        reviewer, payer = await finance_pair(sm, client)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "approved"},
            headers=reviewer,
        )
        assert resp.status_code == 200, resp.text
        async with sm() as session:
            await billing_service.debit(
                session, uid, Decimal("40.00"), type_="consume", allow_negative=True
            )
            await session.commit()
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/payout",
            json={"channel": "offline", "ref": "OFF-X"},
            headers=payer,
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.refundNotRefundable"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "60.00"


class TestIdor:
    @pytest.mark.parametrize("probe", ["list", "apply"])
    async def test_other_users_refund_invisible(self, client: AsyncClient, sm, probe: str):
        """User B can neither read nor touch user A's refund request or order."""
        ha = await user_headers(client, "u13700000121@test.local")
        order = await paid_order(client, ha)
        created = await apply_refund(client, ha, order["order_no"], "20.00")
        assert created.status_code == 201
        hb = await user_headers(client, "u13700000122@test.local")
        await paid_order(client, hb, "10.00")

        if probe == "list":
            mine = (await client.get("/api/v1/wallet/refunds", headers=hb)).json()
            assert mine["items"] == []
        else:
            resp = await apply_refund(client, hb, order["order_no"], "5.00")
            assert resp.status_code == 404
            assert resp.json()["message_key"] == "billing.orderNotFound"

    async def test_eligible_orders_marks_ineligible(self, client: AsyncClient, sm):
        """Refund form candidates: requested orders greyed (already_applied), unpaid greyed
        (not_paid)."""
        headers = await user_headers(client, "u13700000123@test.local")
        paid = await paid_order(client, headers, "50.00")
        pending = await create_order(client, headers, "30.00")
        await apply_refund(client, headers, paid["order_no"], "20.00")
        rows = (await client.get("/api/v1/wallet/refunds/eligible-orders", headers=headers)).json()
        by_no = {r["order_no"]: r for r in rows}
        assert by_no[paid["order_no"]]["refundable"] is False
        assert by_no[paid["order_no"]]["reason_code"] == "already_applied"
        assert by_no[pending["order_no"]]["refundable"] is False
        assert by_no[pending["order_no"]]["reason_code"] == "not_paid"
        assert by_no[paid["order_no"]]["max_amount"] == "50.00"
