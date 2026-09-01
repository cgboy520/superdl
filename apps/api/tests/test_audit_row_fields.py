"""审计行的字段级不变量:超长请求不得毒死审计闸,每行都要有可与日志对上的 request_id。

- action/target 由请求路径拼出,长度不受控。不截断则 INSERT 抛
  StringDataRightTruncation,该行丢失且推进 fail-closed 闸的连续失败计数:
  连打 AUDIT_FAIL_CLOSED_THRESHOLD 次就把全站写操作变成 503,而
  AUDIT_WRITE_FAILED_TOTAL 被永久顶死,那条告警从此失去信号。
- request_id 是审计行与结构化日志之间唯一的连接键(AuditMiddleware 注册在
  ObservabilityMiddleware 之外,它的 finally 跑在 contextvar 解绑之后,
  值只能在响应头落定的一瞬间抄下)。
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core import audit
from app.core.audit import ACTION_MAX_LENGTH, TARGET_MAX_LENGTH, AuditLog
from tests.helpers import create_user_with_key

pytestmark = pytest.mark.usefixtures("fake")


class TestOverLongPath:
    async def test_long_path_does_not_poison_the_audit_gate(self, client: AsyncClient, sm):
        """400 字符路径的写请求:审计行照落(截断到列宽),闸门计数不动。

        挂了说明截断没做:每 10 个这样的匿名请求就把写操作打成 503,谁都能触发。
        """
        audit.reset_audit_gate()
        long_path = "/api/v1/" + "z" * 400
        for _ in range(audit.AUDIT_FAIL_CLOSED_THRESHOLD + 2):
            resp = await client.post(long_path, json={})
            assert resp.status_code == 404  # 路由不存在,但审计中间件照样要落行
        assert audit.audit_gate_open(), "审计闸被超长路径顶死"

        async with sm() as session:
            rows = (
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.action.like("POST /api/v1/zzz%"))
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == audit.AUDIT_FAIL_CLOSED_THRESHOLD + 2
        assert all(len(r.action) <= ACTION_MAX_LENGTH for r in rows)

    async def test_long_audit_target_is_clipped(self, client: AsyncClient, sm):
        """业务侧标注的 target 同样按列宽截断(它也来自请求侧的可变长度输入)。"""
        assert audit._clip("x" * (TARGET_MAX_LENGTH + 50), TARGET_MAX_LENGTH) == (
            "x" * TARGET_MAX_LENGTH
        )
        assert audit._clip(None, TARGET_MAX_LENGTH) is None


class TestFailedCredentialAttempts:
    async def test_failed_login_names_the_targeted_account_masked(self, client: AsyncClient, sm):
        """失败登录的审计行必须指出**哪个账号**被打(号码掩码),不能是匿名无目标行。

        挂了 = 事后回答不了「谁被撞库」:审计目标只在 service 成功返回后才标注,
        而失败请求走不到那一步。号码若以明文入库,审计表自己就成了新的 PII 面。
        """
        await create_user_with_key(client, "13800000230")
        resp = await client.post(
            "/api/v1/auth/login", json={"phone": "13800000230", "password": "not-my-password"}
        )
        assert resp.status_code == 400 and resp.json()["message_key"] == "account.loginFailed"

        async with sm() as session:
            row = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "POST /api/v1/auth/login", AuditLog.result == 400
                    )
                )
            ).scalar_one()
        assert row.target == "phone:138****0230"
        assert "13800000230" not in (row.target or "")  # 明文号码不得入审计表
        assert row.detail == {"action": "login"}

    async def test_successful_login_target_is_the_user_id(self, client: AsyncClient, sm):
        """成功后目标被覆盖成 user:{id}(预标注不能污染成功行的口径)。"""
        _, user_id, _ = await create_user_with_key(client, "13800000231")
        await client.post(
            "/api/v1/auth/sms-code", json={"phone": "13800000231", "purpose": "login"}
        )
        resp = await client.post(
            "/api/v1/auth/login", json={"phone": "13800000231", "sms_code": "123456"}
        )
        assert resp.status_code == 200, resp.text
        async with sm() as session:
            row = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "POST /api/v1/auth/login", AuditLog.result == 200
                    )
                )
            ).scalar_one()
        assert row.target == f"user:{user_id}"


class TestRequestIdJoin:
    async def test_row_carries_the_request_id_from_the_response_header(
        self, client: AsyncClient, sm
    ):
        """审计行的 request_id == 该次响应的 X-Request-ID。

        挂了说明中间件顺序又把它读没了(Observability 在内层,审计的 finally 跑在它
        解绑 contextvar 之后):没有这个键,一条审计行永远对不上它那次请求的日志。
        """
        headers, user_id, _ = await create_user_with_key(client, "13800000220")
        resp = await client.post(
            "/api/v1/tickets",
            json={"category": "other", "subject": "求助", "body": "请协助排查实例网络。"},
            headers={**headers, "User-Agent": "superdl-tests/1.0"},
        )
        assert resp.status_code == 201, resp.text
        request_id = resp.headers["x-request-id"]

        async with sm() as session:
            row = (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "POST /api/v1/tickets")
                )
            ).scalar_one()
        assert row.request_id == request_id
        assert row.user_agent == "superdl-tests/1.0"
        assert row.actor_type == "user" and row.actor_id == str(user_id)

    async def test_inbound_request_id_is_carried_through(self, client: AsyncClient, sm):
        """网关给的 X-Request-ID 沿用到审计行(全链路追踪只有一个 id)。"""
        headers, _, _ = await create_user_with_key(client, "13800000221")
        resp = await client.post(
            "/api/v1/tickets",
            json={"category": "other", "subject": "求助二", "body": "请协助排查实例磁盘。"},
            headers={**headers, "X-Request-ID": "gw-trace-abc123"},
        )
        assert resp.status_code == 201, resp.text
        async with sm() as session:
            row = (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "POST /api/v1/tickets")
                )
            ).scalar_one()
        assert row.request_id == "gw-trace-abc123"
