"""发票闭环(F2):eligible 口径/服务端算额/幂等/开票与驳回/IDOR/F1 退款联动。"""

from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.timeutil import now_utc
from app.modules.billing.models import InvoiceRequest, Order
from tests.test_admin_ops import second_admin_headers
from tests.test_catalog import admin_headers
from tests.test_payment import create_order, pay_mock, user_headers


def past_period(months_ago: int = 1) -> tuple[str, datetime]:
    """一个已结束的北京账期(YYYY-MM)与该账期内的 UTC 支付时刻(当月 15 日中午)。"""
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
    """mock 渠道充值并支付,再把 paid_at 钉到指定时刻(构造历史账期的 paid 订单)。"""
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
        "title": "示例科技有限公司",
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
        """被渠道冲正的 paid 订单不计入可开票额(钱已被渠道划回,对其开票=为未收到的款纳税)。"""
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
        """Σpaid 按北京账期分组:两账期各聚合,倒序返回,金额为字符串。"""
        headers = await user_headers(client, "13700000201")
        p1, at1 = past_period(1)
        p2, at2 = past_period(2)
        await paid_order_at(client, sm, headers, "50.00", at1)
        await paid_order_at(client, sm, headers, "30.00", at1)  # 同账期合并
        await paid_order_at(client, sm, headers, "20.00", at2)
        rows = await eligible(client, headers)
        assert [(r["period"], r["amount"]) for r in rows] == [(p1, "80.00"), (p2, "20.00")]

    async def test_submitted_and_issued_subtract(self, client: AsyncClient, sm):
        """已占用额(submitted/issued)从可开票额中扣除;全额申请后该账期不再出现。"""
        headers = await user_headers(client, "13700000202")
        p1, at1 = past_period(1)
        await paid_order_at(client, sm, headers, "50.00", at1)
        resp = await apply_invoice(client, headers, p1)
        assert resp.status_code == 201, resp.text
        assert resp.json()["amount"] == "50.00"
        assert await eligible(client, headers) == []  # submitted 已占位
        # 开票后同样不占 eligible(issued 也计入已占用)
        finance = await admin_headers(sm, client, role="finance")
        resp = await client.post(
            f"/api/admin/v1/invoices/{resp.json()['id']}/issue",
            json={"invoice_no": "NO-2026-001"},
            headers=finance,
        )
        assert resp.status_code == 200, resp.text
        assert await eligible(client, headers) == []

    async def test_current_period_not_eligible(self, client: AsyncClient, sm):
        """当前北京月账期不可开(paid 订单还可能变):本月支付不入 eligible。"""
        headers = await user_headers(client, "13700000203")
        order = await create_order(client, headers, "50.00")
        assert (await pay_mock(client, order["order_no"], "50.00")).status_code == 200
        assert await eligible(client, headers) == []


class TestRefundDeduction:
    """F1↔F2 口径:当期已打款退款从当期可开票额扣除(净实收),
    否则用户在渠道侧拿回钱后平台仍按全额开票纳税,形成资损。"""

    async def _refund_paid(
        self, client, sm, headers, order_no: str, amount: str, payout_at
    ) -> None:
        """走完整退款流(申请→审批→打款),并把 payout_at 钉到指定时刻(构造账期归属)。"""
        from app.modules.billing.models import RefundRequest
        from tests.test_refunds import apply_refund, finance_pair

        rid = (await apply_refund(client, headers, order_no, amount)).json()["id"]
        reviewer, payer = await finance_pair(sm, client)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "同意"},
            headers=reviewer,
        )
        assert resp.status_code == 200, resp.text
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/payout",
            json={"channel": "offline", "ref": "OFF-T"},
            headers=payer,
        )
        assert resp.status_code == 200, resp.text
        async with sm() as session:
            await session.execute(
                update(RefundRequest).where(RefundRequest.id == rid).values(payout_at=payout_at)
            )
            await session.commit()

    async def test_refund_reduces_eligible_amount(self, client: AsyncClient, sm):
        headers = await user_headers(client, "13700000204")
        p1, at1 = past_period(1)
        order = await paid_order_at(client, sm, headers, "50.00", at1)
        await self._refund_paid(client, sm, headers, order["order_no"], "20.00", at1)
        rows = await eligible(client, headers)
        assert [(r["period"], r["amount"]) for r in rows] == [(p1, "30.00")]

    async def test_full_refund_leaves_nothing_to_bill(self, client: AsyncClient, sm):
        """全额退款后:账期不再出现在 eligible,create 也被服务端算额拦下。"""
        headers = await user_headers(client, "13700000205")
        p1, at1 = past_period(1)
        order = await paid_order_at(client, sm, headers, "50.00", at1)
        await self._refund_paid(client, sm, headers, order["order_no"], "50.00", at1)
        assert await eligible(client, headers) == []
        resp = await apply_invoice(client, headers, p1)
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.invoiceNothingToBill"

    async def test_refund_in_other_period_not_deducted(self, client: AsyncClient, sm):
        """退款打款落在别的账期:只扣打款所在账期,不误伤订单账期。"""
        headers = await user_headers(client, "13700000206")
        p1, at1 = past_period(1)
        _p2, at2 = past_period(2)
        order = await paid_order_at(client, sm, headers, "50.00", at1)
        await self._refund_paid(client, sm, headers, order["order_no"], "20.00", at2)
        rows = await eligible(client, headers)
        assert [(r["period"], r["amount"]) for r in rows] == [(p1, "50.00")]


class TestCreate:
    async def test_amount_computed_server_side(self, client: AsyncClient, sm):
        """金额服务端计算:客户端夹带 amount 字段无效,回包为账期全额。"""
        headers = await user_headers(client, "13700000211")
        p1, at1 = past_period(1)
        await paid_order_at(client, sm, headers, "66.00", at1)
        resp = await apply_invoice(client, headers, p1, amount="0.01")  # 篡改尝试
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["amount"] == "66.00"
        assert body["status"] == "submitted"
        assert body["period"] == p1

    async def test_duplicate_period_rejected(self, client: AsyncClient, sm):
        """同账期已有活跃申请:再次申请 409(幂等键不同也不放行,部分唯一索引兜底)。"""
        headers = await user_headers(client, "13700000212")
        p1, at1 = past_period(1)
        await paid_order_at(client, sm, headers, "50.00", at1)
        assert (await apply_invoice(client, headers, p1)).status_code == 201
        resp = await apply_invoice(client, headers, p1, idem="inv-dup")
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.invoicePeriodAlreadyApplied"

    async def test_company_title_requires_tax_id(self, client: AsyncClient, sm):
        headers = await user_headers(client, "13700000213")
        p1, at1 = past_period(1)
        await paid_order_at(client, sm, headers, "50.00", at1)
        resp = await apply_invoice(client, headers, p1, tax_id=None)
        assert resp.status_code == 422
        resp = await apply_invoice(client, headers, p1, tax_id="   ")
        assert resp.status_code == 422

    async def test_email_format_validated(self, client: AsyncClient, sm):
        headers = await user_headers(client, "13700000214")
        p1, at1 = past_period(1)
        await paid_order_at(client, sm, headers, "50.00", at1)
        resp = await apply_invoice(client, headers, p1, email="not-an-email")
        assert resp.status_code == 422

    async def test_personal_title_needs_no_tax_id(self, client: AsyncClient, sm):
        """个人抬头:税号不需要,即使夹带也不落库。"""
        headers = await user_headers(client, "13700000215")
        p1, at1 = past_period(1)
        await paid_order_at(client, sm, headers, "50.00", at1)
        resp = await apply_invoice(
            client, headers, p1, title_type="personal", title="张三", tax_id="SHOULD-BE-DROPPED"
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["tax_id"] is None

    async def test_current_period_rejected(self, client: AsyncClient, sm):
        """当月账期不可开(即使本月已有 paid 订单)。"""
        headers = await user_headers(client, "13700000216")
        order = await create_order(client, headers, "50.00")
        assert (await pay_mock(client, order["order_no"], "50.00")).status_code == 200
        resp = await apply_invoice(client, headers, current_period())
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "billing.invoicePeriodNotOpen"

    async def test_nothing_to_bill_rejected(self, client: AsyncClient, sm):
        """账期无 paid 充值:应开票额为 0,不受理。"""
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


class TestAdminFlow:
    async def _submitted(self, client: AsyncClient, sm, phone: str) -> tuple[dict, int, str]:
        headers = await user_headers(client, phone)
        p1, at1 = past_period(1)
        await paid_order_at(client, sm, headers, "50.00", at1)
        resp = await apply_invoice(client, headers, p1)
        assert resp.status_code == 201, resp.text
        return headers, resp.json()["id"], p1

    async def test_issue_fills_no_and_notifies(self, client: AsyncClient, sm):
        """开票:status=issued + issued_by/at + 站内信(含发票号)。"""
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

    async def test_reject_requires_reason_and_notifies(self, client: AsyncClient, sm):
        """驳回:理由必填(契约层 422);成功后站内信含理由。"""
        headers, iid, _p1 = await self._submitted(client, sm, "13700000223")
        finance = await admin_headers(sm, client, role="finance")
        resp = await client.post(
            f"/api/admin/v1/invoices/{iid}/reject", json={"reason": ""}, headers=finance
        )
        assert resp.status_code == 422
        resp = await client.post(
            f"/api/admin/v1/invoices/{iid}/reject",
            json={"reason": "抬头与实名信息不一致"},
            headers=finance,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "rejected"
        assert resp.json()["reject_reason"] == "抬头与实名信息不一致"
        notes = (await client.get("/api/v1/notifications", headers=headers)).json()["items"]
        rejected = next(n for n in notes if n["type"] == "invoice")
        assert "抬头与实名信息不一致" in rejected["content"]

    async def test_rejected_period_can_reapply(self, client: AsyncClient, sm):
        """rejected 不占部分唯一索引:同账期可重新申请,金额仍按全额算。"""
        headers = await user_headers(client, "13700000224")
        p1, at1 = past_period(1)
        await paid_order_at(client, sm, headers, "50.00", at1)
        iid = (await apply_invoice(client, headers, p1)).json()["id"]
        finance = await admin_headers(sm, client, role="finance")
        resp = await client.post(
            f"/api/admin/v1/invoices/{iid}/reject",
            json={"reason": "税号有误,请修正"},
            headers=finance,
        )
        assert resp.status_code == 200
        resp = await apply_invoice(client, headers, p1, idem="inv-re")
        assert resp.status_code == 201, resp.text
        assert resp.json()["amount"] == "50.00"
        # 用户端列表能看到驳回理由
        mine = (await client.get("/api/v1/billing/invoices", headers=headers)).json()
        rejected = next(r for r in mine["items"] if r["status"] == "rejected")
        assert rejected["reject_reason"] == "税号有误,请修正"

    async def test_list_filters_and_role_gate(self, client: AsyncClient, sm):
        """status/period 过滤;读:ops/finance/readonly 可,写:仅 finance/admin。"""
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
        # 读角色:readonly / ops 均可
        ro = await admin_headers(sm, client, role="readonly")
        assert (await client.get("/api/admin/v1/invoices", headers=ro)).status_code == 200
        ops = await second_admin_headers(sm, client, "ops-invoice", role="ops")
        assert (await client.get("/api/admin/v1/invoices", headers=ops)).status_code == 200
        # 写角色:ops/readonly 403
        iid = (await client.get("/api/admin/v1/invoices", headers=finance)).json()[0]["id"]
        for h in (ops, ro):
            resp = await client.post(
                f"/api/admin/v1/invoices/{iid}/issue", json={"invoice_no": "NO-X"}, headers=h
            )
            assert resp.status_code == 403


class TestIdor:
    @pytest.mark.parametrize("probe", ["list", "eligible"])
    async def test_other_users_invoice_invisible(self, client: AsyncClient, sm, probe: str):
        """用户 B 的列表与可开票额度都看不到用户 A 的账期数据。"""
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
    """F1 联动:已开票(issued)账期的 paid 订单不可退,须先红冲;submitted 不拦。"""

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
        # 该账期 paid 订单申请退款被拒
        resp = await client.post(
            "/api/v1/wallet/refunds",
            json={"order_no": order["order_no"], "amount": "50.00", "reason": "用不完,申请退款"},
            headers=headers,
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.refundInvoiceIssued"
        # 退款表单候选集同步置灰(invoiced)
        rows = (await client.get("/api/v1/wallet/refunds/eligible-orders", headers=headers)).json()
        row = next(r for r in rows if r["order_no"] == order["order_no"])
        assert row["refundable"] is False
        assert row["reason_code"] == "invoiced"

    async def test_submitted_period_does_not_block_refund(self, client: AsyncClient, sm):
        headers = await user_headers(client, "13700000242")
        p1, at1 = past_period(1)
        order = await paid_order_at(client, sm, headers, "50.00", at1)
        assert (await apply_invoice(client, headers, p1)).status_code == 201  # submitted
        resp = await client.post(
            "/api/v1/wallet/refunds",
            json={"order_no": order["order_no"], "amount": "20.00", "reason": "用不完,申请退款"},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        # 其他账期已开票不影响本账期退款
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
            json={"order_no": order["order_no"], "amount": "10.00", "reason": "再退一笔"},
            headers=headers,
        )
        assert resp.status_code == 409  # 已有活跃退款申请,而非发票拦截
        assert resp.json()["message_key"] == "billing.refundAlreadyApplied"
