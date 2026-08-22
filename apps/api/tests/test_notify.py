"""通知:站内信/24h 去重/Alertmanager 接入。验收:阈值可配、预警可审计、告警幂等。"""

import pytest
from sqlalchemy import select

from app.core.k8s import set_orchestrator
from app.core.k8s.fake import FakeOrchestrator
from app.modules.billing import wallet
from app.modules.billing.patrol import balance_patrol
from app.modules.notify.models import Notification
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

    async def test_list_pagination_beyond_50(self, client, sm, fake):
        """站内信不再封顶最新 50 条:limit/cursor 游标翻页,降序不重不漏。"""
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
        from tests.test_catalog import admin_headers

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
