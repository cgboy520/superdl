"""账号注销:申请(键入手机号校验 + 幂等)→ 7 天冷静期 → 撤销/执行(前置校验 + 匿名化)。

注销后:access/refresh 401(文案「账号已注销」)、手机号释放可重注册、账本依法保留。
"""

import hashlib
from datetime import timedelta
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.timeutil import now_utc
from app.modules.account.models import AccountDeletionRequest, User
from app.modules.billing.models import BalanceLedger, Wallet
from app.modules.orchestrator.models import DataDisk, Instance
from tests.helpers import create_user_with_key, fund_wallet
from tests.test_account_auth import age_sms_codes
from tests.test_catalog import admin_headers

PHONE = "13800000060"


async def _create_request(
    client: AsyncClient, headers: dict, phone: str = PHONE, reason: str = "不再使用"
):
    return await client.post(
        "/api/v1/me/deletion-request",
        json={"phone": phone, "reason": reason},
        headers=headers,
    )


async def _backdate_request(sm: async_sessionmaker[AsyncSession], user_id: int, days: int) -> None:
    """把申请的 requested_at 回拨,越过冷静期。"""
    async with sm() as session:
        await session.execute(
            update(AccountDeletionRequest)
            .where(AccountDeletionRequest.user_id == user_id)
            .values(requested_at=now_utc() - timedelta(days=days))
        )
        await session.commit()


async def _seed_instance(
    sm: async_sessionmaker[AsyncSession], user_id: int, *, status: str = "running"
) -> str:
    async with sm() as session:
        inst = Instance(
            uuid=f"u{user_id}i{now_utc().timestamp()}".replace(".", "")[:32],
            user_id=user_id,
            name="t",
            sku_id=1,
            spec={
                "tier": "shared_std",
                "vram_gb": 8,
                "vcpu": 8,
                "mem_gb": 32,
                "disk_gb": 100,
                "pool_label": "hami",
                "gpu_cores_pct": 50,
            },
            price_hourly=Decimal("1.6800"),
            gpu_count=1,
            image_ref="img",
            status=status,
            k8s_namespace=f"tenant-{user_id}",
            jupyter_token="tok",
            authorized_keys=[],
        )
        session.add(inst)
        await session.commit()
        return inst.uuid


async def _seed_disk(
    sm: async_sessionmaker[AsyncSession], user_id: int, *, status: str = "active"
) -> str:
    async with sm() as session:
        disk = DataDisk(
            uuid=f"d{user_id}{now_utc().timestamp()}".replace(".", "")[:32],
            user_id=user_id,
            name="t",
            size_gb=100,
            juicefs_subpath=f"disk-{user_id}-{now_utc().timestamp()}".replace(".", ""),
            price_gb_month=Decimal("0.3500"),
            status=status,
        )
        session.add(disk)
        await session.commit()
        return disk.uuid


class TestCreate:
    async def test_phone_mismatch_400(self, client: AsyncClient, sm):
        headers, _, _ = await create_user_with_key(client, PHONE)
        resp = await _create_request(client, headers, phone="13900000999")
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "account.deletionPhoneMismatch"

    async def test_idempotent_returns_existing(self, client: AsyncClient, sm):
        headers, _, _ = await create_user_with_key(client, PHONE)
        first = await _create_request(client, headers, reason="第一次")
        assert first.status_code == 201, first.text
        assert first.json()["status"] == "pending"
        second = await _create_request(client, headers, reason="第二次")
        assert second.status_code == 201
        # 幂等:重复提交返回既有申请,不产生第二条
        assert second.json()["id"] == first.json()["id"]
        assert second.json()["reason"] == "第一次"

    async def test_get_none_returns_null(self, client: AsyncClient, sm):
        headers, _, _ = await create_user_with_key(client, PHONE)
        resp = await client.get("/api/v1/me/deletion-request", headers=headers)
        assert resp.status_code == 200
        assert resp.json() is None

    async def test_get_returns_pending_with_cooldown(self, client: AsyncClient, sm):
        headers, _, _ = await create_user_with_key(client, PHONE)
        created = await _create_request(client, headers)
        assert created.status_code == 201
        resp = await client.get("/api/v1/me/deletion-request", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "pending"
        assert body["cooldown_ends_at"] > body["requested_at"]


class TestCancel:
    async def test_cancel_twice_409(self, client: AsyncClient, sm):
        headers, _, _ = await create_user_with_key(client, PHONE)
        assert (await _create_request(client, headers)).status_code == 201
        first = await client.post("/api/v1/me/deletion-request/cancel", headers=headers)
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "cancelled"
        resp = await client.post("/api/v1/me/deletion-request/cancel", headers=headers)
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "account.deletionNotCancellable"

    async def test_cancel_without_request_404(self, client: AsyncClient, sm):
        headers, _, _ = await create_user_with_key(client, PHONE)
        resp = await client.post("/api/v1/me/deletion-request/cancel", headers=headers)
        assert resp.status_code == 404

    async def test_reapply_after_cancel(self, client: AsyncClient, sm):
        """cancelled 不占部分唯一索引:撤销后可重新申请。"""
        headers, _, _ = await create_user_with_key(client, PHONE)
        assert (await _create_request(client, headers)).status_code == 201
        cancelled = await client.post("/api/v1/me/deletion-request/cancel", headers=headers)
        assert cancelled.status_code == 200
        resp = await _create_request(client, headers)
        assert resp.status_code == 201
        assert resp.json()["status"] == "pending"


class TestCooldown:
    async def test_approve_within_cooldown_409(self, client: AsyncClient, sm):
        headers, _, _ = await create_user_with_key(client, PHONE)
        req_id = (await _create_request(client, headers)).json()["id"]
        admin = await admin_headers(sm, client)
        resp = await client.post(f"/api/admin/v1/deletion-requests/{req_id}/approve", headers=admin)
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "account.deletionCooldown"
        # 冷静期拦截不驳回:申请仍为 pending
        async with sm() as session:
            req = await session.get(AccountDeletionRequest, req_id)
            assert req is not None and req.status == "pending"

    async def test_reject_not_limited_by_cooldown(self, client: AsyncClient, sm):
        headers, _, _ = await create_user_with_key(client, PHONE)
        req_id = (await _create_request(client, headers)).json()["id"]
        admin = await admin_headers(sm, client)
        resp = await client.post(
            f"/api/admin/v1/deletion-requests/{req_id}/reject",
            json={"note": "资料待人工复核"},
            headers=admin,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "rejected"
        assert resp.json()["note"] == "资料待人工复核"


class TestApproveGuards:
    async def test_running_instance_blocks(self, client: AsyncClient, sm):
        headers, user_id, _ = await create_user_with_key(client, PHONE)
        req_id = (await _create_request(client, headers)).json()["id"]
        uuid = await _seed_instance(sm, user_id, status="running")
        await _backdate_request(sm, user_id, days=8)
        admin = await admin_headers(sm, client)
        resp = await client.post(f"/api/admin/v1/deletion-requests/{req_id}/approve", headers=admin)
        assert resp.status_code == 409
        body = resp.json()
        assert body["message_key"] == "account.deletionLeftovers"
        assert uuid in body["detail"]["instances"]
        # 校验不过自动驳回,残留清单写进 note
        async with sm() as session:
            req = await session.get(AccountDeletionRequest, req_id)
            assert req is not None and req.status == "rejected"
            assert req.note is not None and uuid in req.note

    async def test_terminal_instances_and_deleted_disk_pass(self, client: AsyncClient, sm):
        """released/failed 实例与 deleted 数据盘均为终态,不构成残留。"""
        headers, user_id, _ = await create_user_with_key(client, PHONE)
        req_id = (await _create_request(client, headers)).json()["id"]
        await _seed_instance(sm, user_id, status="released")
        await _seed_instance(sm, user_id, status="failed")
        await _seed_disk(sm, user_id, status="deleted")
        await _backdate_request(sm, user_id, days=8)
        admin = await admin_headers(sm, client)
        resp = await client.post(f"/api/admin/v1/deletion-requests/{req_id}/approve", headers=admin)
        assert resp.status_code == 200, resp.text

    async def test_active_disk_blocks(self, client: AsyncClient, sm):
        headers, user_id, _ = await create_user_with_key(client, PHONE)
        req_id = (await _create_request(client, headers)).json()["id"]
        uuid = await _seed_disk(sm, user_id, status="active")
        await _backdate_request(sm, user_id, days=8)
        admin = await admin_headers(sm, client)
        resp = await client.post(f"/api/admin/v1/deletion-requests/{req_id}/approve", headers=admin)
        assert resp.status_code == 409
        body = resp.json()
        assert body["message_key"] == "account.deletionLeftovers"
        assert uuid in body["detail"]["disks"]

    async def test_nonzero_balance_blocks(self, client: AsyncClient, sm):
        headers, user_id, _ = await create_user_with_key(client, PHONE)
        await fund_wallet(sm, user_id, "88.00")
        req_id = (await _create_request(client, headers)).json()["id"]
        await _backdate_request(sm, user_id, days=8)
        admin = await admin_headers(sm, client)
        resp = await client.post(f"/api/admin/v1/deletion-requests/{req_id}/approve", headers=admin)
        assert resp.status_code == 409
        body = resp.json()
        assert body["message_key"] == "account.deletionBalanceRemaining"
        assert body["params"]["balance"] == "88.00"
        # 自动驳回,note 引导先经退款流程提现
        async with sm() as session:
            req = await session.get(AccountDeletionRequest, req_id)
            assert req is not None and req.status == "rejected"
            assert req.note is not None and "88.00" in req.note and "退款" in req.note


class TestApproveSuccess:
    async def test_anonymization_and_token_revocation(self, client: AsyncClient, sm):
        headers, user_id, _ = await create_user_with_key(client, PHONE)
        # 实名字段(直接落库,绕开实名渠道)
        async with sm() as session:
            await session.execute(
                update(User)
                .where(User.id == user_id)
                .values(
                    id_name="张三",
                    id_number="1101************12",
                    verification_status="verified",
                )
            )
            await session.commit()
        # 登录拿 refresh token(执行后要验证旧凭证全废)
        await client.post(
            "/api/v1/auth/sms-code",
            json={"phone": PHONE, "purpose": "login"},
        )
        login = await client.post("/api/v1/auth/login", json={"phone": PHONE, "sms_code": "123456"})
        assert login.status_code == 200, login.text
        old_refresh = login.json()["refresh_token"]
        old_access = login.json()["access_token"]

        req_id = (await _create_request(client, headers)).json()["id"]
        await _backdate_request(sm, user_id, days=8)
        admin = await admin_headers(sm, client)
        resp = await client.post(f"/api/admin/v1/deletion-requests/{req_id}/approve", headers=admin)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "completed"

        expected_phone = f"del:{user_id}:{hashlib.sha256(PHONE.encode()).hexdigest()[:12]}"
        async with sm() as session:
            user = await session.get(User, user_id)
            assert user is not None
            # 手机号哈希化:原号码不再出现;实名字段清空;状态 deleted
            assert user.phone == expected_phone
            assert PHONE not in user.phone
            assert user.id_name is None
            assert user.id_number is None
            assert user.verification_status == "unverified"
            assert user.status == "deleted"

        # 旧 access → 401「账号已注销」
        me = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {old_access}"})
        assert me.status_code == 401
        assert me.json()["message_key"] == "account.accountDeleted"
        assert me.json()["message"] == "账号已注销"
        # 旧 refresh → 401「账号已注销」
        refresh = await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
        assert refresh.status_code == 401
        assert refresh.json()["message_key"] == "account.accountDeleted"
        # 登录 → 拒绝(手机号已释放,查无此号)。注意:登录路径按仓库防枚举口径
        # 统一 loginFailed(400),不可区分「已注销」与「未注册/凭证错」——
        # 「账号已注销」文案只出现在持有凭证的 access/refresh 路径(见上两条断言)。
        await client.post(
            "/api/v1/auth/sms-code",
            json={"phone": PHONE, "purpose": "login"},
        )
        relogin = await client.post(
            "/api/v1/auth/login", json={"phone": PHONE, "sms_code": "123456"}
        )
        assert relogin.status_code == 400
        assert relogin.json()["message_key"] == "account.loginFailed"
        # 同手机号可重新注册(匿名化释放了唯一约束)。
        # 上面 relogin 因「查无此号」在消费验证码后未 commit(上游 login 既有行为),
        # 该码未落消费标记会触发同号发码退避:回拨 created_at 越过 60s 窗口。
        await age_sms_codes(sm)
        send = await client.post(
            "/api/v1/auth/sms-code",
            json={"phone": PHONE, "purpose": "register"},
        )
        assert send.status_code == 204, send.text
        reregister = await client.post(
            "/api/v1/auth/register",
            json={"phone": PHONE, "sms_code": "123456", "accept_terms": True},
        )
        assert reregister.status_code == 201, reregister.text
        assert reregister.json()["user"]["id"] != user_id

    async def test_ledger_preserved(self, client: AsyncClient, sm):
        """账本按法定义务保留:注销只脱敏身份,balance_ledger 行不动。"""
        headers, user_id, _ = await create_user_with_key(client, PHONE)
        await fund_wallet(sm, user_id, "100.00")
        async with sm() as session:
            before = (
                await session.execute(
                    select(func.count())
                    .select_from(BalanceLedger)
                    .where(BalanceLedger.user_id == user_id)
                )
            ).scalar_one()
            assert before > 0
            # 余额清零(保留账本行),使执行前校验通过
            await session.execute(
                update(Wallet).where(Wallet.user_id == user_id).values(balance=Decimal("0.00"))
            )
            await session.commit()
        req_id = (await _create_request(client, headers)).json()["id"]
        await _backdate_request(sm, user_id, days=8)
        admin = await admin_headers(sm, client)
        resp = await client.post(f"/api/admin/v1/deletion-requests/{req_id}/approve", headers=admin)
        assert resp.status_code == 200, resp.text
        async with sm() as session:
            after = (
                await session.execute(
                    select(func.count())
                    .select_from(BalanceLedger)
                    .where(BalanceLedger.user_id == user_id)
                )
            ).scalar_one()
        assert after == before


class TestAdminRoles:
    async def test_admin_role_gate(self, client: AsyncClient, sm):
        headers, _, _ = await create_user_with_key(client, PHONE)
        req_id = (await _create_request(client, headers)).json()["id"]
        for role in ("readonly", "finance", "ops"):
            role_headers = await admin_headers(sm, client, role=role)
            listed = await client.get("/api/admin/v1/deletion-requests", headers=role_headers)
            assert listed.status_code == 200, f"{role} 应可读"
            assert any(r["id"] == req_id for r in listed.json())
            approve = await client.post(
                f"/api/admin/v1/deletion-requests/{req_id}/approve", headers=role_headers
            )
            assert approve.status_code == 403, f"{role} 不可执行注销"
            reject = await client.post(
                f"/api/admin/v1/deletion-requests/{req_id}/reject",
                json={"note": "越权测试"},
                headers=role_headers,
            )
            assert reject.status_code == 403, f"{role} 不可驳回"
        # 仅 admin 可写(驳回不受冷静期限制,用它验证写通路)
        admin = await admin_headers(sm, client)
        reject = await client.post(
            f"/api/admin/v1/deletion-requests/{req_id}/reject",
            json={"note": "超管驳回"},
            headers=admin,
        )
        assert reject.status_code == 200, reject.text

    async def test_admin_list_status_filter(self, client: AsyncClient, sm):
        headers, _, _ = await create_user_with_key(client, PHONE)
        assert (await _create_request(client, headers)).status_code == 201
        admin = await admin_headers(sm, client)
        pending = await client.get("/api/admin/v1/deletion-requests?status=pending", headers=admin)
        assert pending.status_code == 200
        assert len(pending.json()) == 1
        row = pending.json()[0]
        # 行内校验计数:无实例/盘/余额
        assert row["instances_active"] == 0
        assert row["disks_active"] == 0
        assert row["balance"] == "0.00"
        completed = await client.get(
            "/api/admin/v1/deletion-requests?status=completed", headers=admin
        )
        assert completed.status_code == 200
        assert completed.json() == []
