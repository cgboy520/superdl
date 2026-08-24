from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditLog
from app.core.errors import AppError
from app.core.security import create_token, decode_token
from app.core.timeutil import now_utc
from app.modules.account.models import SmsCode, User

PHONE = "13800000001"


async def send_code(client: AsyncClient, phone: str = PHONE, purpose: str = "register") -> None:
    resp = await client.post("/api/v1/auth/sms-code", json={"phone": phone, "purpose": purpose})
    assert resp.status_code == 204, resp.text


async def age_sms_codes(sm: async_sessionmaker[AsyncSession]) -> None:
    """把既有验证码的 created_at 回拨,越过 60s 限频窗口(不影响有效期)。"""
    async with sm() as session:
        await session.execute(update(SmsCode).values(created_at=now_utc() - timedelta(minutes=2)))
        await session.commit()


async def register(client: AsyncClient, phone: str = PHONE, password: str | None = None) -> dict:
    await send_code(client, phone, "register")
    body: dict = {"phone": phone, "sms_code": "123456", "accept_terms": True}
    if password:
        body["password"] = password
    resp = await client.post("/api/v1/auth/register", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestRegister:
    async def test_register_then_me(self, client: AsyncClient):
        data = await register(client)
        assert data["user"]["phone"] == PHONE
        resp = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {data['access_token']}"}
        )
        assert resp.status_code == 200
        assert resp.json()["phone"] == PHONE

    async def test_password_byte_boundary(self, client: AsyncClient):
        """bcrypt 上限 72 字节:多字节口令按字符数会绕过 max_length,必须按字节拦成 422。

        72 字节(24 个汉字)可注册;73 字节(25 个汉字)在 schema 层拒掉,不得到哈希层炸 500。
        """
        await send_code(client, "13800000071", "register")
        ok = await client.post(
            "/api/v1/auth/register",
            json={
                "phone": "13800000071",
                "sms_code": "123456",
                "password": "汉" * 24,  # 72 字节
                "accept_terms": True,
            },
        )
        assert ok.status_code == 201, ok.text

        await send_code(client, "13800000072", "register")
        too_long = await client.post(
            "/api/v1/auth/register",
            json={
                "phone": "13800000072",
                "sms_code": "123456",
                "password": "汉" * 25,  # 75 字节
                "accept_terms": True,
            },
        )
        assert too_long.status_code == 422
        assert too_long.json()["code"] == "VALIDATION_ERROR"

    async def test_password_min_length_12(self, client: AsyncClient):
        """用户口令最小 12 位:11 位 422,12 位可注册。"""
        await send_code(client, "13800000073", "register")
        short = await client.post(
            "/api/v1/auth/register",
            json={
                "phone": "13800000073",
                "sms_code": "123456",
                "password": "x9k" * 3 + "m2",  # 11 位
                "accept_terms": True,
            },
        )
        assert short.status_code == 422
        assert short.json()["code"] == "VALIDATION_ERROR"

        await send_code(client, "13800000074", "register")
        ok = await client.post(
            "/api/v1/auth/register",
            json={
                "phone": "13800000074",
                "sms_code": "123456",
                "password": "x9k" * 4,  # 12 位
                "accept_terms": True,
            },
        )
        assert ok.status_code == 201, ok.text

    async def test_password_weak_blacklist(self, client: AsyncClient):
        """弱口令黑名单(Top 20,不区分大小写):满足长度仍 422;改密路径同表拦截。"""
        await send_code(client, "13800000075", "register")
        weak = await client.post(
            "/api/v1/auth/register",
            json={
                "phone": "13800000075",
                "sms_code": "123456",
                "password": "Password123",  # 黑名单(大小写不敏感)
                "accept_terms": True,
            },
        )
        assert weak.status_code == 422
        assert weak.json()["code"] == "VALIDATION_ERROR"

        # 改密路径:同一 PasswordStr 校验链
        data = await register(client, "13800000076", password="x9k" * 4)
        await send_code(client, "13800000076", "reset_password")
        resp = await client.post(
            "/api/v1/auth/password/reset",
            json={
                "phone": "13800000076",
                "sms_code": "123456",
                "new_password": "qwerty123456",
            },
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "VALIDATION_ERROR"
        assert data["access_token"]  # 原会话未被改密失败影响

    async def test_duplicate_phone(self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]):
        await register(client)
        await age_sms_codes(sm)
        await send_code(client)
        resp = await client.post(
            "/api/v1/auth/register",
            json={"phone": PHONE, "sms_code": "123456", "accept_terms": True},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "PHONE_TAKEN"

    async def test_wrong_code(self, client: AsyncClient):
        await send_code(client)
        resp = await client.post(
            "/api/v1/auth/register",
            json={"phone": PHONE, "sms_code": "999999", "accept_terms": True},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "SMS_CODE_INVALID"

    async def test_expired_code(self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]):
        await send_code(client)
        async with sm() as session:
            await session.execute(
                update(SmsCode).values(expires_at=now_utc() - timedelta(minutes=1))
            )
            await session.commit()
        resp = await client.post(
            "/api/v1/auth/register",
            json={"phone": PHONE, "sms_code": "123456", "accept_terms": True},
        )
        assert resp.json()["code"] == "SMS_CODE_INVALID"

    async def test_sms_rate_limit(self, client: AsyncClient):
        await send_code(client)
        resp = await client.post(
            "/api/v1/auth/sms-code", json={"phone": PHONE, "purpose": "register"}
        )
        assert resp.status_code == 429
        assert resp.json()["code"] == "SMS_TOO_FREQUENT"


class TestLogin:
    async def test_login_with_password(self, client: AsyncClient):
        await register(client, password="secret123456")
        resp = await client.post(
            "/api/v1/auth/login", json={"phone": PHONE, "password": "secret123456"}
        )
        assert resp.status_code == 200
        assert resp.json()["access_token"]

    async def test_login_failure_is_indistinguishable(self, client: AsyncClient):
        """未注册的号 与 已注册但密码错,响应必须逐字节相同。

        可区分即是一个免登录的手机号枚举 oracle。
        (request_id 是每请求随机值、不含账号信息,比对时剔除。)
        """
        await register(client, password="secret123456")
        registered = await client.post(
            "/api/v1/auth/login", json={"phone": PHONE, "password": "wrong-pass"}
        )
        unknown = await client.post(
            "/api/v1/auth/login", json={"phone": "13800009999", "password": "wrong-pass"}
        )
        assert registered.status_code == unknown.status_code
        r, u = registered.json(), unknown.json()
        r.pop("request_id")
        u.pop("request_id")
        assert r == u
        # 验证码路径同理
        bad_code_registered = await client.post(
            "/api/v1/auth/login", json={"phone": PHONE, "sms_code": "000000"}
        )
        bad_code_unknown = await client.post(
            "/api/v1/auth/login", json={"phone": "13800009998", "sms_code": "000000"}
        )
        br, bu = bad_code_registered.json(), bad_code_unknown.json()
        br.pop("request_id")
        bu.pop("request_id")
        assert br == bu

    async def test_login_with_sms(self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]):
        await register(client)
        await age_sms_codes(sm)
        await send_code(client, PHONE, "login")
        resp = await client.post("/api/v1/auth/login", json={"phone": PHONE, "sms_code": "123456"})
        assert resp.status_code == 200

    async def test_frozen_user(self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]):
        await register(client, password="secret123456")
        async with sm() as session:
            await session.execute(update(User).values(status="frozen"))
            await session.commit()
        resp = await client.post(
            "/api/v1/auth/login", json={"phone": PHONE, "password": "secret123456"}
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "USER_FROZEN"

    async def test_access_token_cannot_refresh(self, client: AsyncClient):
        data = await register(client)
        resp = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": data["access_token"]}
        )
        assert resp.status_code == 401

    async def test_successful_logins_not_rate_limited(self, client: AsyncClient):
        """连登不锁:成功登录不计入失败配额。"""
        await register(client, "13800000081", password="secret123456")
        for _ in range(6):
            resp = await client.post(
                "/api/v1/auth/login", json={"phone": "13800000081", "password": "secret123456"}
            )
            assert resp.status_code == 200, resp.text

    async def test_failure_counter_reset_by_success(self, client: AsyncClient):
        """失败才计数,成功一次清零:手滑几次后登成功,不应背着之前的失败配额。"""
        phone = "13800000082"
        await register(client, phone, password="secret123456")
        for _ in range(4):
            resp = await client.post(
                "/api/v1/auth/login", json={"phone": phone, "password": "wrong-pass"}
            )
            assert resp.json()["code"] == "LOGIN_FAILED"
        ok = await client.post(
            "/api/v1/auth/login", json={"phone": phone, "password": "secret123456"}
        )
        assert ok.status_code == 200, ok.text
        # 计数已清零:再错 5 次仍是 LOGIN_FAILED,第 6 次才 429
        for _ in range(5):
            resp = await client.post(
                "/api/v1/auth/login", json={"phone": phone, "password": "wrong-pass"}
            )
            assert resp.json()["code"] == "LOGIN_FAILED"
        resp = await client.post(
            "/api/v1/auth/login", json={"phone": phone, "password": "wrong-pass"}
        )
        assert resp.status_code == 429
        assert resp.json()["code"] == "RATE_LIMITED"

    async def test_locked_bucket_blocks_before_password_check(self, client: AsyncClient):
        """桶已封禁时连正确密码也 429:封禁期内的请求在 bcrypt 之前被拦下,
        不再为撞库流量支付哈希成本。"""
        phone = "13800000083"
        await register(client, phone, password="secret123456")
        for _ in range(5):
            resp = await client.post(
                "/api/v1/auth/login", json={"phone": phone, "password": "wrong-pass"}
            )
            assert resp.json()["code"] == "LOGIN_FAILED"
        # 桶已满(5/5):正确密码同样 429 —— 证明廉价准入先于凭据校验
        resp = await client.post(
            "/api/v1/auth/login", json={"phone": phone, "password": "secret123456"}
        )
        assert resp.status_code == 429
        assert resp.json()["code"] == "RATE_LIMITED"


class TestAudienceIsolation:
    def test_admin_token_rejected_for_user_scope(self):
        admin_token = create_token("1", "admin")
        with pytest.raises(AppError) as exc:
            decode_token(admin_token, "user")
        assert exc.value.code == "UNAUTHORIZED"

    def test_user_token_rejected_for_admin_scope(self):
        user_token = create_token("1", "user")
        with pytest.raises(AppError):
            decode_token(user_token, "admin")


class TestAudit:
    async def test_write_operations_audited(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        await register(client)
        async with sm() as session:
            rows = (await session.execute(select(AuditLog))).scalars().all()
        actions = {r.action for r in rows}
        assert "POST /api/v1/auth/sms-code" in actions
        assert "POST /api/v1/auth/register" in actions
        reg = next(r for r in rows if r.action == "POST /api/v1/auth/register")
        assert reg.result == 201
        assert reg.target and reg.target.startswith("user:")


async def issue_code(sm, phone: str, purpose: str, code: str = "123456") -> None:
    """直接落一条验证码(绕开 60s 发送间隔;注册助手刚发过码时不能再发)。"""
    from datetime import timedelta

    from app.core.crypto import hash_sms_code
    from app.core.timeutil import now_utc
    from app.modules.account.models import SmsCode

    async with sm() as session:
        session.add(
            SmsCode(
                phone=phone,
                code_hash=hash_sms_code(phone, purpose, code),
                purpose=purpose,
                expires_at=now_utc() + timedelta(minutes=5),
            )
        )
        await session.commit()


class TestPasswordReset:
    async def test_set_then_login_with_new_password(self, client: AsyncClient, sm):
        """无密码账号也能凭验证码设密码,旧会话被撤销,新 token 立即可用。"""
        pair = await register(client, "13800000090")
        old_access = pair["access_token"]

        await issue_code(sm, "13800000090", "reset_password")
        resp = await client.post(
            "/api/v1/auth/password/reset",
            json={"phone": "13800000090", "sms_code": "123456", "new_password": "newpass123456"},
        )
        assert resp.status_code == 200, resp.text
        new_pair = resp.json()

        # 旧 access token 因 token_version 变更立即失效
        assert (
            await client.get("/api/v1/me", headers={"Authorization": f"Bearer {old_access}"})
        ).status_code == 401
        assert (
            await client.get(
                "/api/v1/me", headers={"Authorization": f"Bearer {new_pair['access_token']}"}
            )
        ).status_code == 200
        # 新密码可登录
        resp = await client.post(
            "/api/v1/auth/login", json={"phone": "13800000090", "password": "newpass123456"}
        )
        assert resp.status_code == 200, resp.text

    async def test_unknown_phone_needs_code_first(self, client: AsyncClient):
        """未注册手机号:先要过验证码那关,不构成「这个号存不存在」的探测口。"""
        resp = await client.post(
            "/api/v1/auth/password/reset",
            json={"phone": "13800000092", "sms_code": "123456", "new_password": "newpass123456"},
        )
        assert resp.json()["code"] == "SMS_CODE_INVALID"

    async def test_new_password_byte_limit(self, client: AsyncClient, sm):
        """找回/设置密码同走字节上限:73 字节的多字节口令 422,不进哈希层。"""
        await register(client, "13800000093")
        await issue_code(sm, "13800000093", "reset_password")
        resp = await client.post(
            "/api/v1/auth/password/reset",
            json={
                "phone": "13800000093",
                "sms_code": "123456",
                "new_password": "汉" * 25,  # 75 字节
            },
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "VALIDATION_ERROR"


class TestRegisterRace:
    async def test_concurrent_register_same_phone(self, sm, monkeypatch):
        """并发注册同号:验证码一次性消费闸(行锁)在请求层先兜住并发;若仍同时到达
        写库,唯一约束 + IntegrityError 捕获保证负方拿 PHONE_TAKEN 而不是 500。

        服务层旁路验证码闸,专测最后防线。
        """
        import asyncio

        from app.core.errors import AppError
        from app.modules.account import service as account_service

        async def _noop_consume(session, phone, code, purpose) -> None:
            return None

        monkeypatch.setattr(account_service, "_consume_sms_code", _noop_consume)
        phone = "13800000073"
        async with sm() as s1, sm() as s2:
            results = await asyncio.gather(
                account_service.register(s1, phone, "111111", None, accept_terms=True),
                account_service.register(s2, phone, "222222", None, accept_terms=True),
                return_exceptions=True,
            )
        winners = [r for r in results if not isinstance(r, Exception)]
        losers = [r for r in results if isinstance(r, AppError)]
        assert len(winners) == 1 and len(losers) == 1, results
        assert losers[0].code == "PHONE_TAKEN"


class TestSmsQuotaAndBackoff:
    async def test_unconsumed_sends_do_not_burn_victim_daily_quota(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """代耗回归:攻击者替受害者请求验证码,耗不到受害者的 10 次/日配额。

        日配额只按「消费」计(攻击者读不到码,永远计不上);发送侧由 IP 限流与
        同号递增退避兜底轰炸成本。挂了 = 受害者当日收不到码也登不上。
        """
        phone = "13800000096"
        async with sm() as session:
            # 同号已有 10 条未消费验证码(旧口径下该号当日配额已被耗尽)
            for _ in range(10):
                session.add(
                    SmsCode(
                        phone=phone,
                        code_hash="0" * 64,
                        purpose="register",
                        expires_at=now_utc() + timedelta(minutes=5),
                        created_at=now_utc() - timedelta(hours=2),
                    )
                )
            await session.commit()
        # 受害者自己请求:不被日配额挡(退避上限 480s,最近一条在 2h 前 → 放行)
        resp = await client.post(
            "/api/v1/auth/sms-code", json={"phone": phone, "purpose": "register"}
        )
        assert resp.status_code == 204, resp.text
        # 消费(注册)同样不受那 10 条未消费记录影响
        resp = await client.post(
            "/api/v1/auth/register",
            json={"phone": phone, "sms_code": "123456", "accept_terms": True},
        )
        assert resp.status_code == 201, resp.text

    async def test_send_backoff_escalates_on_unconsumed_codes(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """同号连续未消费 → 发送间隔递增(60s → 120s);正常消费后连续计数归零。"""
        phone = "13800000097"
        await send_code(client, phone)
        resp = await client.post(
            "/api/v1/auth/sms-code", json={"phone": phone, "purpose": "register"}
        )
        assert resp.status_code == 429
        assert resp.json()["code"] == "SMS_TOO_FREQUENT"
        assert resp.json()["params"]["seconds"] <= 60  # 第一条:基础间隔

        # 越过基础间隔后第二条放行;两条未消费 → 退避升到 120s
        async with sm() as session:
            await session.execute(
                update(SmsCode)
                .where(SmsCode.phone == phone)
                .values(created_at=now_utc() - timedelta(seconds=61))
            )
            await session.commit()
        resp = await client.post(
            "/api/v1/auth/sms-code", json={"phone": phone, "purpose": "register"}
        )
        assert resp.status_code == 204, resp.text
        resp = await client.post(
            "/api/v1/auth/sms-code", json={"phone": phone, "purpose": "register"}
        )
        assert resp.status_code == 429
        assert 60 < resp.json()["params"]["seconds"] <= 120

    async def test_consume_quota_counts_only_successful_reads(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """日配额计在消费侧:消费满 10 次才限;校验失败的尝试不占额度。"""
        from app.core.crypto import hash_sms_code
        from app.core.errors import ErrorCode
        from app.modules.account import service as account_service

        phone = "13800000098"
        codes = [f"{200000 + i}" for i in range(11)]
        async with sm() as session:
            for code in codes:
                session.add(
                    SmsCode(
                        phone=phone,
                        code_hash=hash_sms_code(phone, "login", code),
                        purpose="login",
                        expires_at=now_utc() + timedelta(minutes=5),
                    )
                )
            await session.commit()
        # 失败尝试不占配额
        async with sm() as session:
            with pytest.raises(AppError):
                await account_service._consume_sms_code(session, phone, "999999", "login")
        # 消费与发送逆序(每次选中最新一条未消费记录)
        for code in reversed(codes[1:]):
            async with sm() as session:
                await account_service._consume_sms_code(session, phone, code, "login")
                await session.commit()
        # 第 11 次消费超出日配额
        async with sm() as session:
            with pytest.raises(AppError) as exc:
                await account_service._consume_sms_code(session, phone, codes[0], "login")
            assert exc.value.code == ErrorCode.RATE_LIMITED
