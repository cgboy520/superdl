"""WP9 通知:站内信/24h 去重/Alertmanager 接入。验收:阈值可配、预警可审计、告警幂等。"""

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
        headers, uuid, user_id = await _provision_running(client, sm, fake)
        from decimal import Decimal

        async with sm() as session:
            balance = await wallet.get_balance(session, user_id)
            await wallet.debit(session, user_id, balance - Decimal("5.00"), type_="adjust")
            await session.commit()

        await balance_patrol(sm)
        await balance_patrol(sm)  # 同日重复巡检 → 去重

        rows = (await client.get("/api/v1/notifications", headers=headers)).json()
        warns = [r for r in rows if r["type"] == "balance_warn"]
        assert len(warns) == 1
        assert "小时" in warns[0]["content"]

    async def test_read_flow(self, client, sm, fake):
        headers, uuid, user_id = await _provision_running(client, sm, fake)
        from decimal import Decimal

        async with sm() as session:
            balance = await wallet.get_balance(session, user_id)
            await wallet.debit(session, user_id, balance - Decimal("5.00"), type_="adjust")
            await session.commit()
        await balance_patrol(sm)

        unread = (
            await client.get("/api/v1/notifications", params={"unread": True}, headers=headers)
        ).json()
        assert len(unread) == 1
        nid = unread[0]["id"]
        resp = await client.post(f"/api/v1/notifications/{nid}/read", headers=headers)
        assert resp.status_code == 204
        unread = (
            await client.get("/api/v1/notifications", params={"unread": True}, headers=headers)
        ).json()
        assert unread == []


class TestAlertmanagerWebhook:
    async def test_ingest_and_dedup(self, client, sm, fake):
        headers, uuid, user_id = await _provision_running(client, sm, fake)  # user_id=1
        resp = await client.post("/api/v1/webhooks/alertmanager", json=AM_PAYLOAD)
        assert resp.status_code == 200
        assert resp.json()["ingested"] == 1
        # 重放 → 幂等
        resp = await client.post("/api/v1/webhooks/alertmanager", json=AM_PAYLOAD)
        assert resp.json()["ingested"] == 0

        # 受影响租户收到 gpu_fault 通知
        rows = (await client.get("/api/v1/notifications", headers=headers)).json()
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
        headers, uuid, user_id = await _provision_running(client, sm, fake)
        async with sm() as session:
            balance = await wallet.get_balance(session, user_id)
            await wallet.debit(session, user_id, balance, type_="adjust")
            await session.commit()
        await balance_patrol(sm)  # 停机 + 欠费通知
        async with sm() as session:
            rows = (await session.execute(select(Notification))).scalars().all()
        assert any(r.type == "arrears" for r in rows)
