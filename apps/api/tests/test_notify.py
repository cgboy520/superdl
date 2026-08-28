"""通知:站内信/24h 去重/Alertmanager 接入。验收:阈值可配、预警可审计、告警幂等。"""

import pytest
from sqlalchemy import select

from app.core.k8s import set_orchestrator
from app.core.k8s.fake import FakeOrchestrator
from app.modules.billing import wallet
from app.modules.billing.patrol import balance_patrol
from app.modules.notify.models import Notification
from tests.test_catalog import admin_headers
from tests.test_orchestrator_lifecycle import _provision_running

pytestmark = pytest.mark.usefixtures("fake")


@pytest.fixture
def fake():
    orch = FakeOrchestrator(auto_ready=False)
    set_orchestrator(orch)
    yield orch
    set_orchestrator(None)


AM_PAYLOAD = {
    "alerts": [
        {
            "fingerprint": "abc123",
            "startsAt": "2026-08-19T10:00:00Z",
            "labels": {
                "alertname": "GPUXidCriticalError",
                "severity": "critical",
                "namespace": "tenant-1",
            },
            "annotations": {"summary": "GPU Xid 79 fatal error on node gpu-01"},
        }
    ]
}


class TestBalanceWarnNotification:
    async def test_unread_count_endpoint(self, client, sm, fake):
        """未读数轻端点:DB count 与列表分页解耦,标记已读后减少。"""
        headers, _uuid, user_id = await _provision_running(client, sm, fake)
        async with sm() as session:
            for i in range(3):
                session.add(
                    Notification(
                        user_id=user_id,
                        type="instance",
                        title=f"通知{i}",
                        content="x",
                        dedup_key=f"t-unread:{user_id}:{i}",
                    )
                )
            await session.commit()
        resp = await client.get("/api/v1/notifications/unread-count", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["unread_count"] == 3
        first = (await client.get("/api/v1/notifications", headers=headers)).json()["items"][0]
        await client.post(f"/api/v1/notifications/{first['id']}/read", headers=headers)
        resp = await client.get("/api/v1/notifications/unread-count", headers=headers)
        assert resp.json()["unread_count"] == 2

    async def test_patrol_writes_notification_with_dedup(self, client, sm, fake):
        headers, _uuid, user_id = await _provision_running(client, sm, fake)
        from decimal import Decimal

        async with sm() as session:
            balance = await wallet.get_balance(session, user_id)
            await wallet.debit(
                session, user_id, balance - Decimal("5.00"), type_="adjust", allow_negative=True
            )
            await session.commit()

        await balance_patrol(sm)
        await balance_patrol(sm)  # 同日重复巡检 → 去重

        rows = (await client.get("/api/v1/notifications", headers=headers)).json()["items"]
        warns = [r for r in rows if r["type"] == "balance_warn"]
        assert len(warns) == 1
        assert "小时" in warns[0]["content"]

    async def test_read_flow(self, client, sm, fake):
        headers, _uuid, user_id = await _provision_running(client, sm, fake)
        from decimal import Decimal

        async with sm() as session:
            balance = await wallet.get_balance(session, user_id)
            await wallet.debit(
                session, user_id, balance - Decimal("5.00"), type_="adjust", allow_negative=True
            )
            await session.commit()
        await balance_patrol(sm)

        unread = (
            await client.get("/api/v1/notifications", params={"unread": True}, headers=headers)
        ).json()["items"]
        assert len(unread) == 1
        nid = unread[0]["id"]
        resp = await client.post(f"/api/v1/notifications/{nid}/read", headers=headers)
        assert resp.status_code == 204
        unread = (
            await client.get("/api/v1/notifications", params={"unread": True}, headers=headers)
        ).json()["items"]
        assert unread == []

    async def test_read_all_marks_everything_and_is_idempotent(self, client, sm, fake):
        """全部已读:多条未读一次清零;重复调用幂等(仍 204,不再改动任何行)。"""
        headers, _uuid, user_id = await _provision_running(client, sm, fake)
        async with sm() as session:
            for i in range(3):
                session.add(
                    Notification(
                        user_id=user_id, type="account", title=f"t{i}", content="c", severity="info"
                    )
                )
            await session.commit()
        unread = (
            await client.get("/api/v1/notifications", params={"unread": True}, headers=headers)
        ).json()["items"]
        assert len(unread) == 3

        resp = await client.post("/api/v1/notifications/read-all", headers=headers)
        assert resp.status_code == 204
        unread = (
            await client.get("/api/v1/notifications", params={"unread": True}, headers=headers)
        ).json()["items"]
        assert unread == []
        # 重复调用幂等;全部列表仍可见(已读不消失)
        resp = await client.post("/api/v1/notifications/read-all", headers=headers)
        assert resp.status_code == 204
        all_items = (await client.get("/api/v1/notifications", headers=headers)).json()["items"]
        assert len(all_items) == 3

    async def test_read_all_scoped_to_self(self, client, sm, fake):
        """全部已读只动本人:其他用户的未读不受影响。"""
        headers, _uuid, user_id = await _provision_running(client, sm, fake)
        async with sm() as session:
            session.add(
                Notification(
                    user_id=user_id, type="account", title="mine", content="c", severity="info"
                )
            )
            session.add(
                Notification(
                    user_id=user_id + 999,
                    type="account",
                    title="other",
                    content="c",
                    severity="info",
                )
            )
            await session.commit()
        resp = await client.post("/api/v1/notifications/read-all", headers=headers)
        assert resp.status_code == 204
        async with sm() as session:
            rows = (await session.execute(select(Notification))).scalars().all()
        by_title = {r.title: r for r in rows}
        assert by_title["mine"].read_at is not None
        assert by_title["other"].read_at is None

    async def test_list_pagination_beyond_50(self, client, sm, fake):
        """站内信不封顶条数:limit/cursor 游标翻页,降序不重不漏。"""
        headers, _uuid, user_id = await _provision_running(client, sm, fake)
        async with sm() as session:
            for i in range(60):
                session.add(
                    Notification(
                        user_id=user_id,
                        type="announcement",
                        title=f"公告 {i}",
                        content="c",
                        severity="info",
                    )
                )
            await session.commit()

        page1 = (
            await client.get("/api/v1/notifications", params={"limit": 50}, headers=headers)
        ).json()
        assert len(page1["items"]) == 50
        assert page1["next_cursor"] is not None
        page2 = (
            await client.get(
                "/api/v1/notifications",
                params={"limit": 50, "cursor": page1["next_cursor"]},
                headers=headers,
            )
        ).json()
        ids = [n["id"] for n in page1["items"] + page2["items"]]
        assert ids == sorted(ids, reverse=True)  # 全局降序
        assert len(ids) == 60
        assert page2["next_cursor"] is None


class TestAlertmanagerWebhook:
    async def test_critical_alert_sms_to_oncall(self, client, sm, fake):
        """critical 平台告警:配置值班手机号后经 outbox 短信直发(不依赖站内信流),重放幂等。"""
        from app.core.outbox import OutboxTask
        from app.core.platform_config import PlatformSetting

        async with sm() as session:
            session.add(PlatformSetting(key="oncall_phone", value="13900001111", updated_by=None))
            await session.commit()
        resp = await client.post("/api/v1/webhooks/alertmanager", json=AM_PAYLOAD)
        assert resp.status_code == 200
        assert resp.json()["ingested"] == 1

        async with sm() as session:
            tasks = list(
                (
                    await session.execute(select(OutboxTask).where(OutboxTask.type == "notify.sms"))
                ).scalars()
            )
        assert any(t.payload.get("phone") == "13900001111" for t in tasks)
        # 重放(同 fingerprint+startsAt):不再补发短信任务
        resp = await client.post("/api/v1/webhooks/alertmanager", json=AM_PAYLOAD)
        assert resp.json()["ingested"] == 0
        async with sm() as session:
            again = list(
                (
                    await session.execute(select(OutboxTask).where(OutboxTask.type == "notify.sms"))
                ).scalars()
            )
        assert len(again) == len(tasks)

    async def test_ingest_and_dedup(self, client, sm, fake):
        headers, _uuid, _user_id = await _provision_running(client, sm, fake)  # user_id=1
        resp = await client.post("/api/v1/webhooks/alertmanager", json=AM_PAYLOAD)
        assert resp.status_code == 200
        assert resp.json()["ingested"] == 1
        # 重放 → 幂等
        resp = await client.post("/api/v1/webhooks/alertmanager", json=AM_PAYLOAD)
        assert resp.json()["ingested"] == 0

        # 受影响租户收到 gpu_fault 通知
        rows = (await client.get("/api/v1/notifications", headers=headers)).json()["items"]
        assert any(r["type"] == "gpu_fault" for r in rows)

        # 平台级告警流(admin)
        ah = await admin_headers(sm, client, role="ops")
        alerts = (await client.get("/api/admin/v1/alerts", headers=ah)).json()
        assert any(a["title"] == "GPUXidCriticalError" for a in alerts)

    async def test_token_enforced_when_configured(self, client, sm, fake, monkeypatch):
        from app.core.config import get_settings

        monkeypatch.setattr(get_settings(), "alertmanager_token", "s3cret")
        resp = await client.post("/api/v1/webhooks/alertmanager", json=AM_PAYLOAD)
        assert resp.status_code == 401
        resp = await client.post(
            "/api/v1/webhooks/alertmanager",
            json=AM_PAYLOAD,
            headers={"Authorization": "Bearer s3cret"},
        )
        assert resp.status_code == 200

    async def test_arrears_notice_recorded(self, client, sm, fake):
        _headers, _uuid, user_id = await _provision_running(client, sm, fake)
        async with sm() as session:
            balance = await wallet.get_balance(session, user_id)
            await wallet.debit(session, user_id, balance, type_="adjust", allow_negative=True)
            await session.commit()
        await balance_patrol(sm)  # 停机 + 欠费通知
        async with sm() as session:
            rows = (await session.execute(select(Notification))).scalars().all()
        assert any(r.type == "arrears" for r in rows)


class TestAlertAck:
    """告警闭环:ack 落确认人/时间、重复 ack 409、unread-count 准确、severity 过滤、角色门。"""

    async def test_ack_records_actor_and_time(self, client, sm, fake):
        await client.post("/api/v1/webhooks/alertmanager", json=AM_PAYLOAD)
        ops = await admin_headers(sm, client, role="ops")
        alerts = (await client.get("/api/admin/v1/alerts", headers=ops)).json()
        target = next(a for a in alerts if a["title"] == "GPUXidCriticalError")
        assert target["acked_at"] is None
        assert target["target_kind"] == "node"  # GPU 前缀 alertname → 节点页

        resp = await client.post(f"/api/admin/v1/alerts/{target['id']}/ack", headers=ops)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["acked_at"] is not None
        assert body["acked_by_username"] == "ops-user"

        again = await client.post(f"/api/admin/v1/alerts/{target['id']}/ack", headers=ops)
        assert again.status_code == 409
        assert again.json()["message_key"] == "adminapi.alertAlreadyAcked"

    async def test_unread_count_tracks_ack(self, client, sm, fake):
        await client.post("/api/v1/webhooks/alertmanager", json=AM_PAYLOAD)
        ops = await admin_headers(sm, client, role="ops")
        # 平台 admin_alert + 租户 gpu_fault(namespace tenant-1 → user_id=1)各一条
        count = (await client.get("/api/admin/v1/alerts/unread-count", headers=ops)).json()["count"]
        assert count == 2

        alerts = (await client.get("/api/admin/v1/alerts", headers=ops)).json()
        gpu = next(a for a in alerts if a["type"] == "gpu_fault")
        assert gpu["target_kind"] == "tenant" and gpu["target_id"] == "1"
        resp = await client.post(f"/api/admin/v1/alerts/{gpu['id']}/ack", headers=ops)
        assert resp.status_code == 200
        count = (await client.get("/api/admin/v1/alerts/unread-count", headers=ops)).json()["count"]
        assert count == 1

    async def test_severity_filter(self, client, sm, fake):
        await client.post("/api/v1/webhooks/alertmanager", json=AM_PAYLOAD)
        ops = await admin_headers(sm, client, role="ops")
        critical = (
            await client.get("/api/admin/v1/alerts", params={"severity": "critical"}, headers=ops)
        ).json()
        assert len(critical) == 2
        assert all(a["severity"] == "critical" for a in critical)
        warning = (
            await client.get("/api/admin/v1/alerts", params={"severity": "warning"}, headers=ops)
        ).json()
        assert warning == []

    async def test_ack_non_alert_404(self, client, sm, fake):
        """普通站内信(非告警流类型)不可确认:404,不暴露存在性之外的写面。"""
        ops = await admin_headers(sm, client, role="ops")
        async with sm() as session:
            row = Notification(user_id=None, type="announcement", title="t", content="c")
            session.add(row)
            await session.commit()
            await session.refresh(row)
            nid = row.id
        resp = await client.post(f"/api/admin/v1/alerts/{nid}/ack", headers=ops)
        assert resp.status_code == 404
        resp = await client.post("/api/admin/v1/alerts/999999/ack", headers=ops)
        assert resp.status_code == 404

    async def test_ack_role_gate(self, client, sm, fake):
        await client.post("/api/v1/webhooks/alertmanager", json=AM_PAYLOAD)
        finance = await admin_headers(sm, client, role="finance")
        alerts = (await client.get("/api/admin/v1/alerts", headers=finance)).json()
        resp = await client.post(f"/api/admin/v1/alerts/{alerts[0]['id']}/ack", headers=finance)
        assert resp.status_code == 403


class TestAlertmanagerAuthHardening:
    async def test_dev_without_token_rejected(self, client, sm, fake, monkeypatch):
        """安全:除 test 外,未配置 token 一律拒绝接入。"""
        from app.core.config import get_settings

        monkeypatch.setattr(get_settings(), "environment", "dev")
        resp = await client.post("/api/v1/webhooks/alertmanager", json=AM_PAYLOAD)
        assert resp.status_code == 401


class TestAlertmanagerWebhookHardening:
    async def test_non_ascii_authorization_rejected_not_500(self, client, sm, fake, monkeypatch):
        """非 ASCII 的 Authorization 头:compare_digest 收 str 抛 TypeError,必须先 encode。"""
        from app.core.config import get_settings

        monkeypatch.setattr(get_settings(), "alertmanager_token", "s3cret")
        resp = await client.post(
            "/api/v1/webhooks/alertmanager",
            json=AM_PAYLOAD,
            headers={b"authorization": "Bearer caf\u00e9".encode("latin-1")},
        )
        assert resp.status_code == 401

    async def test_oversized_body_rejected(self, client, sm, fake):
        """报文体积上限:畸形/恶意大报文不能撑爆解析与写库。"""
        body = b'{"alerts": []}' + b" " * (1024 * 1024)
        resp = await client.post(
            "/api/v1/webhooks/alertmanager",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 413

    async def test_bad_json_rejected_with_400(self, client, sm, fake):
        resp = await client.post(
            "/api/v1/webhooks/alertmanager",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "VALIDATION_ERROR"

    async def test_long_strings_truncated(self, client, sm, fake):
        """外部提交的 summary 等字段长度不受信:入库前截断。"""
        payload = {
            "alerts": [
                {
                    **AM_PAYLOAD["alerts"][0],
                    "fingerprint": "trunc1",
                    "annotations": {"summary": "x" * 5000},
                }
            ]
        }
        resp = await client.post("/api/v1/webhooks/alertmanager", json=payload)
        assert resp.status_code == 200
        async with sm() as session:
            row = (
                await session.execute(
                    select(Notification).where(Notification.type == "admin_alert")
                )
            ).scalar_one()
        assert len(row.content) == 1024

    async def test_alerts_list_capped(self, client, sm, fake, monkeypatch):
        """单次报文的 alerts 条数封顶:防一条报文灌入上万条通知。"""
        from app.modules.notify import router as notify_router

        monkeypatch.setattr(notify_router, "ALERT_MAX_ALERTS", 3)
        payload = {
            "alerts": [{**AM_PAYLOAD["alerts"][0], "fingerprint": f"cap{i}"} for i in range(5)]
        }
        resp = await client.post("/api/v1/webhooks/alertmanager", json=payload)
        assert resp.status_code == 200
        assert resp.json()["ingested"] == 3

    async def test_ip_rate_limited(self, client, sm, fake, monkeypatch):
        """IP 限流兜底;阈值远在 Alertmanager 重试节奏之上,正常重试不受阻。"""
        from app.modules.notify import router as notify_router

        monkeypatch.setattr(notify_router, "ALERT_RATE_LIMIT", 2)
        for _ in range(2):
            resp = await client.post("/api/v1/webhooks/alertmanager", json={"alerts": []})
            assert resp.status_code == 200
        resp = await client.post("/api/v1/webhooks/alertmanager", json={"alerts": []})
        assert resp.status_code == 429
        assert resp.headers["retry-after"].isdigit()
