"""管理端 TOTP MFA:安全策略 admin_mfa_enabled(默认开)下全部管理角色强制绑定,二要素登录,
恢复码,重置救援;关闭时全员密码即登录。"""

import asyncio
from typing import get_args

import pyotp
import pytest
from httpx import AsyncClient

from app.modules.adminapi.schemas import AdminRole
from tests.helpers import admin_headers, complete_mfa_setup

pytestmark = pytest.mark.usefixtures("fake")


async def _login(client: AsyncClient, username: str, password: str = "pass1234"):
    return await client.post(
        "/api/admin/v1/auth/login", json={"username": username, "password": password}
    )


async def _create(client, sm, username: str, role: str) -> None:
    from app.modules.adminapi.service import create_admin

    async with sm() as session:
        await create_admin(session, username, "pass1234", role)


class TestMfaEnforcement:
    async def test_every_role_gets_setup_challenge(self, client: AsyncClient, sm):
        """登录只回绑定票、不直发 token,契约里的每个角色无一例外
        (ops 可签节点接入令牌、readonly 可导出流水/审计,免 MFA 即口令泄漏直通车)。"""
        for role in get_args(AdminRole):
            await _create(client, sm, f"mfa-{role}", role)
            body = (await _login(client, f"mfa-{role}")).json()
            assert body["status"] == "mfa_setup", (role, body)
            assert "access_token" not in body
            assert body["ticket"]

    async def test_switch_off_skips_mfa_for_everyone_and_on_restores(self, client: AsyncClient, sm):
        """安全策略 admin_mfa_enabled=false:未绑定者与已绑定者都直发 token(status=ok);
        重新开启后已绑定者回到二要素、未绑定者回到绑定票——挂了说明开关没接到登录路径,
        或关闭后已绑定者仍被挑战(与「关 = 全员免」的契约不符)。"""
        from sqlalchemy import delete

        from app.core.platform_config import PlatformSetting

        await _create(client, sm, "sw-bound", "admin")
        await _create(client, sm, "sw-unbound", "ops")
        await complete_mfa_setup(client, (await _login(client, "sw-bound")).json()["ticket"])

        async with sm() as session:
            session.add(PlatformSetting(key="admin_mfa_enabled", value="false"))
            await session.commit()
        for name in ("sw-bound", "sw-unbound"):
            body = (await _login(client, name)).json()
            assert body["status"] == "ok", (name, body)
            me = await client.get(
                "/api/admin/v1/me", headers={"Authorization": f"Bearer {body['access_token']}"}
            )
            assert me.status_code == 200 and me.json()["username"] == name

        async with sm() as session:
            await session.execute(
                delete(PlatformSetting).where(PlatformSetting.key == "admin_mfa_enabled")
            )
            await session.commit()
        assert (await _login(client, "sw-bound")).json()["status"] == "mfa_required"
        assert (await _login(client, "sw-unbound")).json()["status"] == "mfa_setup"


class TestSetupFlow:
    async def test_concurrent_begin_returns_the_stored_secret(self, client: AsyncClient, sm):
        """并发 begin(双发/双击/多标签页)只能落一枚密钥,且页面拿到的就是库里那枚。
        挂了说明:两路各生成一枚、后写者覆盖前者,用户照二维码输的首个动态码必然验不过。
        """
        await _create(client, sm, "race-admin", "admin")
        ticket = (await _login(client, "race-admin")).json()["ticket"]
        a, b = await asyncio.gather(
            client.post("/api/admin/v1/auth/mfa/setup/begin", json={"ticket": ticket}),
            client.post("/api/admin/v1/auth/mfa/setup/begin", json={"ticket": ticket}),
        )
        assert a.json()["secret"] == b.json()["secret"]
        assert a.json()["otpauth_uri"].startswith("otpauth://totp/")
        # 真正的判据不是两路自洽,而是「返回的密钥能绑定成功」= 与库里存的是同一枚
        resp = await client.post(
            "/api/admin/v1/auth/mfa/setup/confirm",
            json={"ticket": ticket, "code": pyotp.TOTP(a.json()["secret"]).now()},
        )
        assert resp.status_code == 200, resp.text

    async def test_confirm_wrong_code_rejected(self, client: AsyncClient, sm):
        await _create(client, sm, "wrong-admin", "admin")
        ticket = (await _login(client, "wrong-admin")).json()["ticket"]
        await client.post("/api/admin/v1/auth/mfa/setup/begin", json={"ticket": ticket})
        resp = await client.post(
            "/api/admin/v1/auth/mfa/setup/confirm", json={"ticket": ticket, "code": "000000"}
        )
        assert resp.json()["code"] == "MFA_CODE_INVALID"

    async def test_bind_raises_platform_alert(self, client: AsyncClient, sm):
        """TOTP 绑定成功即落平台告警(检测闭环:绑定只靠口令,抢先绑定必须可见)。"""
        from sqlalchemy import select

        from app.modules.notify.models import Notification

        await _create(client, sm, "alert-admin", "admin")
        await complete_mfa_setup(client, (await _login(client, "alert-admin")).json()["ticket"])
        async with sm() as session:
            rows = (
                (
                    await session.execute(
                        select(Notification).where(Notification.type == "admin_alert")
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1
        assert "alert-admin" in rows[0].content
        assert rows[0].severity == "warning"

    async def test_ticket_cannot_cross_stage(self, client: AsyncClient, sm):
        """setup 票不能拿去登录验证口(typ 校验),反之亦然。"""
        await _create(client, sm, "cross-admin", "admin")
        ticket = (await _login(client, "cross-admin")).json()["ticket"]
        resp = await client.post(
            "/api/admin/v1/auth/login/mfa", json={"ticket": ticket, "code": "123456"}
        )
        assert resp.json()["code"] == "MFA_TICKET_INVALID"


class TestVerifyLogin:
    async def _bound_admin(self, client: AsyncClient, sm, username: str) -> None:
        await _create(client, sm, username, "admin")
        ticket = (await _login(client, username)).json()["ticket"]
        await complete_mfa_setup(client, ticket)

    async def test_bound_admin_gets_verify_challenge(self, client: AsyncClient, sm):
        await self._bound_admin(client, sm, "bound-admin")
        body = (await _login(client, "bound-admin")).json()
        assert body["status"] == "mfa_required"

    async def test_totp_login_success(self, client: AsyncClient, sm):
        import time

        import pyotp

        await _create(client, sm, "totp-admin", "admin")
        ticket = (await _login(client, "totp-admin")).json()["ticket"]
        begin = await client.post("/api/admin/v1/auth/mfa/setup/begin", json={"ticket": ticket})
        secret = begin.json()["secret"]
        confirm = await client.post(
            "/api/admin/v1/auth/mfa/setup/confirm",
            json={"ticket": ticket, "code": pyotp.TOTP(secret).now()},
        )
        assert confirm.status_code == 200
        codes = confirm.json()["recovery_codes"]
        assert len(codes) == 10

        # 重新登录:TOTP 直过(绑定已用当前步,登录用下一枚——valid_window=1 接受相邻步;
        # 真实用户绑定与重登录间隔远大于 30s,不会遇到同码场景)
        ticket2 = (await _login(client, "totp-admin")).json()["ticket"]
        resp = await client.post(
            "/api/admin/v1/auth/login/mfa",
            json={"ticket": ticket2, "code": pyotp.TOTP(secret).at(int(time.time()) + 30)},
        )
        assert resp.status_code == 200
        assert resp.json()["recovery_codes_left"] is None

        # 恢复码:用后作废,重放即拒
        ticket3 = (await _login(client, "totp-admin")).json()["ticket"]
        use = await client.post(
            "/api/admin/v1/auth/login/mfa", json={"ticket": ticket3, "code": codes[0]}
        )
        assert use.status_code == 200
        assert use.json()["recovery_codes_left"] == 9
        ticket4 = (await _login(client, "totp-admin")).json()["ticket"]
        reuse = await client.post(
            "/api/admin/v1/auth/login/mfa", json={"ticket": ticket4, "code": codes[0]}
        )
        assert reuse.json()["code"] == "MFA_CODE_INVALID"

    async def test_same_totp_code_replay_rejected(self, client: AsyncClient, sm):
        """防重放(RFC 6238 §5.2):同一枚动态码第二次验证即拒,窗口内不能批量领 token
        (挂了 = 截获一枚码可在约 90s 窗口内无限重放,MFA 形同虚设)。"""
        import pyotp

        await _create(client, sm, "replay-admin", "admin")
        ticket = (await _login(client, "replay-admin")).json()["ticket"]
        begin = await client.post("/api/admin/v1/auth/mfa/setup/begin", json={"ticket": ticket})
        secret = begin.json()["secret"]
        code = pyotp.TOTP(secret).now()
        confirm = await client.post(
            "/api/admin/v1/auth/mfa/setup/confirm", json={"ticket": ticket, "code": code}
        )
        assert confirm.status_code == 200
        # 同码重放:绑定路径与登录路径都必须拒
        again = await client.post(
            "/api/admin/v1/auth/mfa/setup/confirm", json={"ticket": ticket, "code": code}
        )
        assert again.json()["code"] == "MFA_CODE_INVALID"
        ticket2 = (await _login(client, "replay-admin")).json()["ticket"]
        replay = await client.post(
            "/api/admin/v1/auth/login/mfa", json={"ticket": ticket2, "code": code}
        )
        assert replay.json()["code"] == "MFA_CODE_INVALID"

    async def test_mfa_rate_limited_after_5_failures(self, client: AsyncClient, sm):
        await self._bound_admin(client, sm, "brute-admin")  # 绑定成功验证计 1 次配额
        ticket = (await _login(client, "brute-admin")).json()["ticket"]
        # 成功也计配额(防窗口内批量领 token):5 次/10min 中绑定已耗 1,剩 4 次失败配额
        for _ in range(4):
            resp = await client.post(
                "/api/admin/v1/auth/login/mfa", json={"ticket": ticket, "code": "000000"}
            )
            assert resp.json()["code"] == "MFA_CODE_INVALID"
        resp = await client.post(
            "/api/admin/v1/auth/login/mfa", json={"ticket": ticket, "code": "000000"}
        )
        assert resp.status_code == 429


class TestRecoveryRegenAndReset:
    async def test_regenerate_invalidates_old_codes(self, client: AsyncClient, sm):
        await _create(client, sm, "regen-admin", "admin")
        ticket = (await _login(client, "regen-admin")).json()["ticket"]
        begin = await client.post("/api/admin/v1/auth/mfa/setup/begin", json={"ticket": ticket})
        import pyotp

        confirm = await client.post(
            "/api/admin/v1/auth/mfa/setup/confirm",
            json={"ticket": ticket, "code": pyotp.TOTP(begin.json()["secret"]).now()},
        )
        old_codes = confirm.json()["recovery_codes"]
        token = confirm.json()["access_token"]
        regen = await client.post(
            "/api/admin/v1/me/mfa/recovery-codes", headers={"Authorization": f"Bearer {token}"}
        )
        assert regen.status_code == 200
        new_codes = regen.json()["recovery_codes"]
        assert set(new_codes) != set(old_codes)
        # 旧码已作废
        ticket2 = (await _login(client, "regen-admin")).json()["ticket"]
        stale = await client.post(
            "/api/admin/v1/auth/login/mfa", json={"ticket": ticket2, "code": old_codes[1]}
        )
        assert stale.json()["code"] == "MFA_CODE_INVALID"

    async def test_reset_by_super_admin(self, client: AsyncClient, sm):
        """锁死救援:另一位超管重置 → 绑定清空 + 会话踢掉 → 下次登录重新强制绑定。"""
        from sqlalchemy import select

        from app.modules.adminapi.models import AdminUser

        h = await admin_headers(sm, client)  # admin-user(超管)
        await _create(client, sm, "rescue-admin", "admin")
        ticket = (await _login(client, "rescue-admin")).json()["ticket"]
        victim_token = await complete_mfa_setup(client, ticket)

        async with sm() as session:
            victim_id = (
                await session.execute(
                    select(AdminUser.id).where(AdminUser.username == "rescue-admin")
                )
            ).scalar_one()
        resp = await client.post(
            f"/api/admin/v1/admins/{victim_id}/mfa/reset",
            headers=h,
            json={"reason": "验证器丢失救援"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["totp_enabled"] is False
        # 旧 token 已踢(token_version+1)
        me = await client.get(
            "/api/admin/v1/me", headers={"Authorization": f"Bearer {victim_token}"}
        )
        assert me.status_code == 401
        # 下次登录重新走绑定流
        assert (await _login(client, "rescue-admin")).json()["status"] == "mfa_setup"

    async def test_reset_self_forbidden(self, client: AsyncClient, sm):
        from sqlalchemy import select

        from app.modules.adminapi.models import AdminUser

        h = await admin_headers(sm, client)
        async with sm() as session:
            my_id = (
                await session.execute(
                    select(AdminUser.id).where(AdminUser.username == "admin-user")
                )
            ).scalar_one()
        resp = await client.post(
            f"/api/admin/v1/admins/{my_id}/mfa/reset",
            headers=h,
            json={"reason": "尝试自重置"},
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "MFA_RESET_SELF_FORBIDDEN"
