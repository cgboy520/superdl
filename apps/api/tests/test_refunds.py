"""退款闭环:申请口径/幂等/审批 → 打款出金/双人制衡/余额再校验/IDOR/渠道冲正拦截。"""

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
    """出金同步审计:审计写失败即出金失败回滚;成功时审计与业务同事务,中间件不双写。"""

    async def _approved_refund(self, client, sm, phone: str, payer_name: str) -> tuple[dict, int]:
        headers = await user_headers(client, phone)
        order = await paid_order(client, headers)
        rid = (await apply_refund(client, headers, order["order_no"], "30.00")).json()["id"]
        reviewer = await admin_headers(sm, client, role="finance")
        payer = await admin_headers(sm, client, role="finance", username=payer_name)
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

    async def test_idem_key_param_mismatch_409(self, client: AsyncClient, sm):
        """同键异参(改了金额):显式 409,绝不静默返回上一笔申请。"""
        headers = await user_headers(client, "13700000106")
        order = await paid_order(client, headers)
        r1 = await apply_refund(client, headers, order["order_no"], "20.00", idem="rf-mix")
        assert r1.status_code == 201
        r2 = await apply_refund(client, headers, order["order_no"], "21.00", idem="rf-mix")
        assert r2.status_code == 409
        assert r2.json()["message_key"] == "common.idempotencyKeyMismatch"
        # 同键同参仍是重放(回归不破)
        r3 = await apply_refund(client, headers, order["order_no"], "20.00", idem="rf-mix")
        assert r3.status_code == 200
        assert r3.headers["x-idempotent-replay"] == "true"
        assert r3.json()["refund_no"] == r1.json()["refund_no"]


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

    async def test_payout_idempotent_replay(self, client: AsyncClient, sm):
        """打款 Idempotency-Key:同键同参重放返回 200 + X-Idempotent-Replay,不重复出金。"""
        headers = await user_headers(client, "13700000141")
        order = await paid_order(client, headers, "50.00")
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        reviewer, payer = await finance_pair(sm, client)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "同意"},
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

        # 双击/重试(同键同参):重放返回既有单,钱包不再扣
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
        """同键异参(改了凭证号):409,不静默返回上一单。"""
        headers = await user_headers(client, "13700000142")
        order = await paid_order(client, headers, "50.00")
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        reviewer, payer = await finance_pair(sm, client)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "同意"},
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
            json={"channel": "offline", "ref": "OFF-201"},  # 异参
            headers={**payer, "Idempotency-Key": "payout-key-002"},
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "common.idempotencyKeyMismatch"

    async def test_payout_repeat_without_key_still_409(self, client: AsyncClient, sm):
        """无键的重复打款:维持状态机 409(不是静默重放)。"""
        headers = await user_headers(client, "13700000143")
        order = await paid_order(client, headers, "50.00")
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        reviewer, payer = await finance_pair(sm, client)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "同意"},
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
        assert w["balance"] == "30.00"  # 只出金一次

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


class TestPartialRefunds:
    """多次部分退款:已打款不占位,同单累计可退至订单额(公有云主流口径)。"""

    async def _review_and_payout(self, client, rid: int, reviewer: dict, payer: dict) -> None:
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "同意"},
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
        """首笔打款后可再申:两笔累计 = 订单额;再申任意金额被拒(上限 0)。"""
        headers = await user_headers(client, "13700000130")
        order = await paid_order(client, headers, "50.00")
        reviewer, payer = await finance_pair(sm, client)

        rid1 = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        await self._review_and_payout(client, rid1, reviewer, payer)
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "30.00"

        # 已打款不占活跃位:第二笔 30(累计 50 = 订单额)可申
        r2 = await apply_refund(client, headers, order["order_no"], "30.00", idem="rf-p2")
        assert r2.status_code == 201, r2.text
        await self._review_and_payout(client, r2.json()["id"], reviewer, payer)
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "0.00"

        # 累计已满:上限 0,再申被拒
        r3 = await apply_refund(client, headers, order["order_no"], "0.01", idem="rf-p3")
        assert r3.status_code == 400
        assert r3.json()["message_key"] == "billing.refundAmountExceeded"

    async def test_payout_cumulative_guard_on_data_anomaly(self, client: AsyncClient, sm):
        """出金闸:审批后订单额被改小(数据异常),累计将超额 → 打款 409,坚决不出金。"""
        headers = await user_headers(client, "13700000131")
        order = await paid_order(client, headers, "50.00")
        reviewer, payer = await finance_pair(sm, client)
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "同意"},
            headers=reviewer,
        )
        assert resp.status_code == 200
        async with sm() as session:  # 模拟修数事故:订单额被改小到 10
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
        assert w["balance"] == "50.00"  # 未出金

    async def test_eligible_orders_fully_refunded(self, client: AsyncClient, sm):
        """候选集口径:退满的订单置灰 fully_refunded;部分退款后 max_amount = 剩余可退。"""
        headers = await user_headers(client, "13700000132")
        order = await paid_order(client, headers, "50.00")
        reviewer, payer = await finance_pair(sm, client)
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        await self._review_and_payout(client, rid, reviewer, payer)

        rows = (await client.get("/api/v1/wallet/refunds/eligible-orders", headers=headers)).json()
        row = next(r for r in rows if r["order_no"] == order["order_no"])
        assert row["refundable"] is True  # 已打款不占位,可再申
        assert row["max_amount"] == "30.00"  # min(剩余 30, 余额 30)

        rid2 = (await apply_refund(client, headers, order["order_no"], "30.00")).json()["id"]
        await self._review_and_payout(client, rid2, reviewer, payer)
        rows = (await client.get("/api/v1/wallet/refunds/eligible-orders", headers=headers)).json()
        row = next(r for r in rows if r["order_no"] == order["order_no"])
        assert row["refundable"] is False
        assert row["reason_code"] == "fully_refunded"
        assert row["max_amount"] == "0.00"


class TestRefundStrategy:
    """退款策略(#23):可退余额口径(补偿 credit 不可提现) + 原路退回。"""

    async def test_compensation_credit_not_refundable(self, client: AsyncClient, sm):
        """补偿 credit 不可提现:充 50 花光 + 补偿 50(余额 50),退款申请全拒。

        可退额 = Σ充值 − Σ消费 = 0;钱包里的 50 是平台赠送,不是渠道实付。
        """
        headers = await user_headers(client, "13700000201")
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
        assert w["balance"] == "50.00"  # 余额还在,但可退额已归零
        resp = await apply_refund(client, headers, order["order_no"], "1.00")
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "billing.refundAmountExceeded"
        # 候选集同口径收紧:max_amount=0 且置灰
        eligible = (
            await client.get("/api/v1/wallet/refunds/eligible-orders", headers=headers)
        ).json()
        row = next(r for r in eligible if r["order_no"] == order["order_no"])
        assert row["max_amount"] == "0.00"
        assert row["refundable"] is False
        assert row["reason_code"] == "no_balance"

    async def test_payout_channel_must_match_order_channel(self, client: AsyncClient, sm):
        """原路退回:微信单只能 wechat_transfer(或例外 offline),alipay_transfer 拒。"""
        headers = await user_headers(client, "13700000202")
        order = await paid_order(client, headers, "50.00")
        async with sm() as session:  # mock 单无映射无法测,改记为微信单
            await session.execute(
                update(Order).where(Order.order_no == order["order_no"]).values(channel="wechat")
            )
            await session.commit()
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        reviewer, payer = await finance_pair(sm, client)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "同意"},
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
        """审批→打款之间用户继续消费:可退额蒸发,打款被硬闸拦下(钱包不动)。"""
        headers = await user_headers(client, "13700000203")
        order = await paid_order(client, headers, "50.00")
        async with sm() as session:
            uid = (
                await session.execute(
                    select(Order.user_id).where(Order.order_no == order["order_no"])
                )
            ).scalar_one()
            # 补偿 50:余额 100,但可退额仍 50(补偿不进)
            await billing_service.credit(session, uid, Decimal("50.00"), type_="adjust")
            await session.commit()
        rid = (await apply_refund(client, headers, order["order_no"], "20.00")).json()["id"]
        reviewer, payer = await finance_pair(sm, client)
        resp = await client.post(
            f"/api/admin/v1/refunds/{rid}/review",
            json={"approve": True, "comment": "同意"},
            headers=reviewer,
        )
        assert resp.status_code == 200, resp.text
        # 审批后用户又消费 40:可用余额 60 ≥ 20(过旧闸),可退额 50−40=10 < 20(新闸拦下)
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
        assert w["balance"] == "60.00"  # 未出金


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
