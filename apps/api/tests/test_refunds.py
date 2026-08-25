"""退款闭环:申请口径/幂等/审批 → 打款出金/双人制衡/余额再校验/IDOR/渠道冲正拦截。"""

import re
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.timeutil import now_utc
from app.modules.billing import service as billing_service
from app.modules.billing.models import BalanceLedger, Order, RefundRequest
from tests.test_admin_ops import second_admin_headers
from tests.test_catalog import admin_headers
from tests.test_payment import create_order, pay_mock, user_headers

REFUND_NO_RE = re.compile(r"^R\d{8}-\d{2}$")


async def paid_order(client: AsyncClient, headers: dict, amount: str = "50.00") -> dict:
    """mock 渠道充值并支付,返回已入账订单。"""
    order = await create_order(client, headers, amount)
    resp = await pay_mock(client, order["order_no"], amount)
    assert resp.status_code == 200, resp.text
    return order


async def apply_refund(
    client: AsyncClient,
    headers: dict,
    order_no: str,
    amount: str = "50.00",
    idem: str | None = None,
):
    h = {**headers, **({"Idempotency-Key": idem} if idem else {})}
    return await client.post(
        "/api/v1/wallet/refunds",
        json={"order_no": order_no, "amount": amount, "reason": "用不完,申请退款"},
        headers=h,
    )


async def finance_pair(sm, client: AsyncClient) -> tuple[dict, dict]:
    """两名 finance 管理员(审批人与打款人必须不同)。"""
    reviewer = await admin_headers(sm, client, role="finance")
    payer = await second_admin_headers(sm, client, "finance-payer")
    return reviewer, payer


class TestSyncAudit:
    """出金同步审计(P1-8):审计写失败即出金失败回滚;成功时审计与业务同事务,中间件不双写。"""

    async def _approved_refund(self, client, sm, phone: str, payer_name: str) -> tuple[dict, int]:
        headers = await user_headers(client, phone)
        order = await paid_order(client, headers)
        rid = (await apply_refund(client, headers, order["order_no"], "30.00")).json()["id"]
        reviewer = await admin_headers(sm, client, role="finance")
        payer = await second_admin_headers(sm, client, payer_name)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "同意"},
            headers=reviewer,
        )
        assert resp.status_code == 200, resp.text
        return payer, rid

    async def test_audit_writer_failure_rolls_back_payout(self, client, sm, monkeypatch):
        """审计写失败 → 出金整体回滚:500、钱包未扣、退款单仍 approved
        (宁可不出金,不可无留痕);故障消除后重试成功。"""
        from app.modules.adminapi import router_finance

        payer, rid = await self._approved_refund(client, sm, "13700000160", "finance-payer-a")

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
        assert req is not None and req.status == "approved"  # 未置 paid
        assert entries == []  # 负向调账已随回滚撤销
        monkeypatch.undo()
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/payout",
            json={"channel": "offline", "ref": "OFF-AUDIT"},
            headers=payer,
        )
        assert resp.status_code == 200, resp.text

    async def test_payout_audit_single_row_same_transaction(self, client, sm):
        """成功出金:审计行与业务同事务(恰好一条,中间件不双写);detail 含渠道/凭证。"""
        from app.core.audit import AuditLog

        payer, rid = await self._approved_refund(client, sm, "13700000161", "finance-payer-b")
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
        assert len(rows) == 1  # 同事务一行,audit_synced 标志使中间件未双写
        assert rows[0].detail == {"channel": "offline", "ref": "OFF-SYNC"}
        assert rows[0].result == 200


class TestApply:
    async def test_create_success_and_no_format(self, client: AsyncClient, sm):
        headers = await user_headers(client, "13700000101")
        order = await paid_order(client, headers)
        resp = await apply_refund(client, headers, order["order_no"], "30.00")
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert REFUND_NO_RE.match(body["refund_no"]), body["refund_no"]
        assert body["status"] == "pending"
        assert body["amount"] == "30.00"
        # 钱包不动:审批通过都不算出金,只有登记打款才核销
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "50.00"

    async def test_idempotent_replay_returns_same(self, client: AsyncClient, sm):
        headers = await user_headers(client, "13700000102")
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
        headers = await user_headers(client, "13700000103")
        order = await create_order(client, headers, "50.00")  # pending,未支付
        resp = await apply_refund(client, headers, order["order_no"])
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.refundOrderNotPaid"

    async def test_channel_reversed_order_rejected(self, client: AsyncClient, sm):
        """渠道冲正(用户已在渠道侧拒付拿回钱)的订单禁止平台二次退款出金。"""
        headers = await user_headers(client, "13700000105")
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
        assert w["balance"] == "50.00"  # 未出金

    async def test_amount_capped_by_min_of_order_and_balance(self, client: AsyncClient, sm):
        """金额上限 = min(订单额, 当前余额):超订单额与超余额各拒一次。"""
        headers = await user_headers(client, "13700000104")
        order = await paid_order(client, headers, "50.00")
        resp = await apply_refund(client, headers, order["order_no"], "50.01")
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "billing.refundAmountExceeded"
        # 消费 40 后余额 10:退 20 被拒(上限收缩到余额)
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
        # 余额口径内可申请
        resp = await apply_refund(client, headers, order["order_no"], "10.00")
        assert resp.status_code == 201, resp.text

    async def test_duplicate_application_rejected(self, client: AsyncClient, sm):
        """部分唯一索引:同一订单已有活跃申请时再次申请 409(幂等键不同也不放行)。"""
        headers = await user_headers(client, "13700000105")
        order = await paid_order(client, headers)
        assert (await apply_refund(client, headers, order["order_no"], "10.00")).status_code == 201
        resp = await apply_refund(client, headers, order["order_no"], "10.00", idem="rf-dup")
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.refundAlreadyApplied"


class TestAdminFlow:
    async def test_review_then_payout_full_flow(self, client: AsyncClient, sm):
        """审批 → 登记打款:钱包负向调账 + ledger type=refund + balance_after 快照 + 回写关联。"""
        headers = await user_headers(client, "13700000111")
        order = await paid_order(client, headers, "50.00")
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        reviewer, payer = await finance_pair(sm, client)

        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "属实,同意"},
            headers=reviewer,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "approved"
        # 审批通过 ≠ 出金
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
        """审批通过后才被渠道冲正(webhook 随时可达):打款口必须复核并拒付。"""
        headers = await user_headers(client, "13700000114")
        order = await paid_order(client, headers, "50.00")
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        reviewer, payer = await finance_pair(sm, client)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "同意"},
            headers=reviewer,
        )
        assert resp.status_code == 200
        async with sm() as session:  # 审批后、打款前渠道冲正到达
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
        assert w["balance"] == "50.00"  # 未出金

    async def test_payout_same_person_rejected(self, client: AsyncClient, sm):
        """双人制衡:打款登记人 == 审批人 → 409。"""
        headers = await user_headers(client, "13700000112")
        order = await paid_order(client, headers)
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        reviewer, _payer = await finance_pair(sm, client)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "同意"},
            headers=reviewer,
        )
        assert resp.status_code == 200
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/payout",
            json={"channel": "offline", "ref": "OFF-001"},
            headers=reviewer,  # 同一人
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.refundPayoutSamePerson"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "50.00"  # 未出金

    async def test_payout_balance_consumed_then_cancel(self, client: AsyncClient, sm):
        """审批后余额被消费 → 打款 409(余额已被消费);取消该单,余额不动。"""
        headers = await user_headers(client, "13700000113")
        order = await paid_order(client, headers, "50.00")
        rid = (await apply_refund(client, headers, order["order_no"], "40.00")).json()["id"]
        reviewer, payer = await finance_pair(sm, client)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "同意"},
            headers=reviewer,
        )
        assert resp.status_code == 200
        # 用户在审批后消费,余额剩 10 < 应退 40
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
        # 取消出口:approved 可取消,余额不动
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/cancel",
            json={"reason": "余额已消费,与用户协商取消"},
            headers=payer,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "cancelled"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "10.00"

    async def test_cancel_terminal_state_rejected(self, client: AsyncClient, sm):
        """取消仅限 pending/approved:已打款(paid)不可取消。"""
        headers = await user_headers(client, "13700000114")
        order = await paid_order(client, headers)
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        reviewer, payer = await finance_pair(sm, client)
        await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "同意"},
            headers=reviewer,
        )
        await client.post(
            f"/api/admin/v1/refunds/{rid}/payout",
            json={"channel": "offline", "ref": "OFF-9"},
            headers=payer,
        )
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/cancel", json={"reason": "尝试取消终态"}, headers=payer
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.refundStateNotCancellable"

    async def test_rejected_order_can_reapply(self, client: AsyncClient, sm):
        """驳回不占部分唯一索引:同一订单可重新申请。"""
        headers = await user_headers(client, "13700000115")
        order = await paid_order(client, headers)
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        reviewer, _payer = await finance_pair(sm, client)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": False, "comment": "理由不充分,驳回"},
            headers=reviewer,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"
        resp = await apply_refund(client, headers, order["order_no"], "15.00", idem="rf-re")
        assert resp.status_code == 201, resp.text
        assert resp.json()["amount"] == "15.00"
        # 用户端列表能看到驳回理由
        mine = (await client.get("/api/v1/wallet/refunds", headers=headers)).json()
        rejected = next(r for r in mine["items"] if r["status"] == "rejected")
        assert rejected["review_comment"] == "理由不充分,驳回"

    async def test_review_non_pending_rejected(self, client: AsyncClient, sm):
        headers = await user_headers(client, "13700000116")
        order = await paid_order(client, headers)
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        reviewer, payer = await finance_pair(sm, client)
        await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "同意"},
            headers=reviewer,
        )
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": False, "comment": "重复审批"},
            headers=payer,
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.refundStateNotReviewable"

    async def test_admin_list_filters(self, client: AsyncClient, sm):
        headers = await user_headers(client, "13700000117")
        order = await paid_order(client, headers)
        await apply_refund(client, headers, order["order_no"], "20.00")
        reviewer, _payer = await finance_pair(sm, client)
        rows = (await client.get("/api/admin/v1/refunds", headers=reviewer)).json()["items"]
        assert len(rows) == 1 and rows[0]["status"] == "pending"
        assert (
            await client.get("/api/admin/v1/refunds", params={"status": "paid"}, headers=reviewer)
        ).json()["items"] == []
        # 管理端视图含双人字段与用户 id
        assert rows[0]["user_id"] is not None
        # readonly 可读,ops 不可
        ro = await admin_headers(sm, client, role="readonly")
        assert (await client.get("/api/admin/v1/refunds", headers=ro)).status_code == 200
        ops = await second_admin_headers(sm, client, "ops-nofin", role="ops")
        assert (await client.get("/api/admin/v1/refunds", headers=ops)).status_code == 403


class TestIdor:
    @pytest.mark.parametrize("probe", ["list", "apply"])
    async def test_other_users_refund_invisible(self, client: AsyncClient, sm, probe: str):
        """用户 B 读不到/碰不得用户 A 的退款单与其订单。"""
        ha = await user_headers(client, "13700000121")
        order = await paid_order(client, ha)
        created = await apply_refund(client, ha, order["order_no"], "20.00")
        assert created.status_code == 201
        hb = await user_headers(client, "13700000122")
        await paid_order(client, hb, "10.00")  # B 也有自己的 paid 订单

        if probe == "list":
            mine = (await client.get("/api/v1/wallet/refunds", headers=hb)).json()
            assert mine["items"] == []
        else:
            resp = await apply_refund(client, hb, order["order_no"], "5.00")
            assert resp.status_code == 404  # 他人订单号按不存在处理
            assert resp.json()["message_key"] == "billing.orderNotFound"

    async def test_eligible_orders_marks_ineligible(self, client: AsyncClient, sm):
        """退款表单候选集:已申请的订单置灰(already_applied),未支付置灰(not_paid)。"""
        headers = await user_headers(client, "13700000123")
        paid = await paid_order(client, headers, "50.00")
        pending = await create_order(client, headers, "30.00")
        await apply_refund(client, headers, paid["order_no"], "20.00")
        rows = (await client.get("/api/v1/wallet/refunds/eligible-orders", headers=headers)).json()
        by_no = {r["order_no"]: r for r in rows}
        assert by_no[paid["order_no"]]["refundable"] is False
        assert by_no[paid["order_no"]]["reason_code"] == "already_applied"
        assert by_no[pending["order_no"]]["refundable"] is False
        assert by_no[pending["order_no"]]["reason_code"] == "not_paid"
        assert by_no[paid["order_no"]]["max_amount"] == "50.00"  # min(订单额, 余额)
