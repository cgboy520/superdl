"""站内信、预警去重与 Alertmanager 接入契约。"""

import pytest
from sqlalchemy import select

from app.modules.billing import wallet
from app.modules.billing.patrol import balance_patrol
from app.modules.notify.models import Notification
from tests.helpers import (
    admin_headers,
    get_instance,
    provision_running,
    register,
    set_platform_setting,
    user_headers_with_id,
)

pytestmark = pytest.mark.usefixtures("fake")


AM_HEADERS = {"Authorization": "Bearer test-alertmanager-token"}

AM_PAYLOAD = {
    "alerts": [
        {
            "fingerprint": "abc123",
            "startsAt": "2026-08-19T10:00:00Z",
            "labels": {
                "alertname": "GPUXidCriticalError",
                "severity": "critical",
                "namespace": "tenant-1",
                "hostname": "gpu-01",
            },
            "annotations": {"summary": "GPU Xid 79 fatal error on node gpu-01"},
        }
    ]
}


def am_payload_for(user_id: int) -> dict:
    """AM_PAYLOAD 的租户归属版:namespace 指向真实注册用户。"""
    import copy

    payload = copy.deepcopy(AM_PAYLOAD)
    payload["alerts"][0]["labels"]["namespace"] = f"tenant-{user_id}"
    return payload


class TestBalanceWarnNotification:
    async def test_unread_count_endpoint(self, client, sm, fake):
        """未读数端点:标记已读后减少。"""
        headers, user_id = await user_headers_with_id(client, "13700000061")
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
        """低余额预警:counts 记 warned、实例不停机;同日重复巡检按去重键只留一条站内信。"""
        headers, uuid, user_id = await provision_running(client, sm, fake)
        from decimal import Decimal

        async with sm() as session:
            balance = await wallet.get_balance(session, user_id)
            await wallet.debit(
                session, user_id, balance - Decimal("5.00"), type_="adjust", allow_negative=True
            )
            await session.commit()

        counts = await balance_patrol(sm)
        assert counts["warned"] == 1
        await balance_patrol(sm)

        rows = (await client.get("/api/v1/notifications", headers=headers)).json()["items"]
        warns = [r for r in rows if r["type"] == "balance_warn"]
        assert len(warns) == 1
        assert "小时" in warns[0]["content"]
        assert (await get_instance(client, headers, uuid))["status"] == "running"

    async def test_read_all_marks_everything_and_is_idempotent(self, client, sm, fake):
        """全部已读:多条未读一次清零;重复调用幂等 204。"""
        headers, user_id = await user_headers_with_id(client, "13700000064")
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
        resp = await client.post("/api/v1/notifications/read-all", headers=headers)
        assert resp.status_code == 204
        all_items = (await client.get("/api/v1/notifications", headers=headers)).json()["items"]
        assert len(all_items) == 3

    async def test_read_all_scoped_to_self(self, client, sm, fake):
        """全部已读只动本人:其他用户的未读不受影响。"""
        headers, user_id = await user_headers_with_id(client, "13700000065")
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
        """站内信 limit/cursor 游标翻页,降序不重不漏。"""
        headers, user_id = await user_headers_with_id(client, "13700000066")
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
        assert ids == sorted(ids, reverse=True)
        assert len(ids) == 60
        assert page2["next_cursor"] is None


class TestAlertmanagerWebhook:
    async def test_critical_alert_sms_to_oncall(self, client, sm, fake):
        """critical 平台告警:配置值班手机号后经 outbox 短信直发,重放幂等。"""
        from app.core.outbox import OutboxTask

        await set_platform_setting(sm, "oncall_phone", "+8613900001111")
        resp = await client.post(
            "/api/v1/webhooks/alertmanager", json=AM_PAYLOAD, headers=AM_HEADERS
        )
        assert resp.status_code == 200
        assert resp.json()["ingested"] == 1

        async with sm() as session:
            tasks = list(
                (
                    await session.execute(select(OutboxTask).where(OutboxTask.type == "notify.sms"))
                ).scalars()
            )
        assert any(t.payload.get("phone") == "+8613900001111" for t in tasks)

    async def test_ingest_and_dedup(self, client, sm, fake):
        from app.core.outbox import OutboxTask

        headers, _uuid, _user_id = await provision_running(client, sm, fake)
        resp = await client.post(
            "/api/v1/webhooks/alertmanager", json=AM_PAYLOAD, headers=AM_HEADERS
        )
        assert resp.status_code == 200
        assert resp.json()["ingested"] == 1
        async with sm() as session:
            sms_tasks = list(
                (
                    await session.execute(select(OutboxTask).where(OutboxTask.type == "notify.sms"))
                ).scalars()
            )
        resp = await client.post(
            "/api/v1/webhooks/alertmanager", json=AM_PAYLOAD, headers=AM_HEADERS
        )
        assert resp.json()["ingested"] == 0
        async with sm() as session:
            again = list(
                (
                    await session.execute(select(OutboxTask).where(OutboxTask.type == "notify.sms"))
                ).scalars()
            )
        assert len(again) == len(sms_tasks)

        rows = (await client.get("/api/v1/notifications", headers=headers)).json()["items"]
        assert any(r["type"] == "gpu_fault" for r in rows)

        ah = await admin_headers(sm, client, role="ops")
        alerts = (await client.get("/api/admin/v1/alerts", headers=ah)).json()["items"]
        assert any(a["title"] == "GPUXidCriticalError" for a in alerts)

    async def test_token_enforced_when_configured(self, client, sm, fake, monkeypatch):
        from app.core.config import get_settings

        monkeypatch.setattr(get_settings(), "alertmanager_token", "s3cret")
        resp = await client.post(
            "/api/v1/webhooks/alertmanager", json=AM_PAYLOAD, headers=AM_HEADERS
        )
        assert resp.status_code == 401
        resp = await client.post(
            "/api/v1/webhooks/alertmanager",
            json=AM_PAYLOAD,
            headers={"Authorization": "Bearer s3cret"},
        )
        assert resp.status_code == 200

    async def test_arrears_notice_recorded(self, client, sm, fake):
        _headers, _uuid, user_id = await provision_running(client, sm, fake)
        async with sm() as session:
            balance = await wallet.get_balance(session, user_id)
            await wallet.debit(session, user_id, balance, type_="adjust", allow_negative=True)
            await session.commit()
        await balance_patrol(sm)
        async with sm() as session:
            rows = (await session.execute(select(Notification))).scalars().all()
        assert any(r.type == "arrears" for r in rows)


class TestAlertAck:
    """告警闭环:ack 落确认人/时间、重复 ack 409、unread-count 准确、severity 过滤、角色门。"""

    async def test_ack_records_actor_and_time(self, client, sm, fake):
        await client.post("/api/v1/webhooks/alertmanager", json=AM_PAYLOAD, headers=AM_HEADERS)
        ops = await admin_headers(sm, client, role="ops")
        alerts = (await client.get("/api/admin/v1/alerts", headers=ops)).json()["items"]
        target = next(a for a in alerts if a["title"] == "GPUXidCriticalError")
        assert target["acked_at"] is None
        assert target["target_kind"] == "node" and target["target_id"] == "gpu-01"

        resp = await client.post(f"/api/admin/v1/alerts/{target['id']}/ack", headers=ops)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["acked_at"] is not None
        assert body["acked_by_username"] == "ops-user"

        again = await client.post(f"/api/admin/v1/alerts/{target['id']}/ack", headers=ops)
        assert again.status_code == 409
        assert again.json()["message_key"] == "adminapi.alertAlreadyAcked"

    async def test_unread_count_tracks_ack(self, client, sm, fake):
        user = await register(client, "13900000991")
        await client.post(
            "/api/v1/webhooks/alertmanager",
            json=am_payload_for(user["user"]["id"]),
            headers=AM_HEADERS,
        )
        ops = await admin_headers(sm, client, role="ops")
        body = (await client.get("/api/admin/v1/alerts/unread-count", headers=ops)).json()
        assert body["count"] == 3
        assert body["critical_count"] == 2

        alerts = (await client.get("/api/admin/v1/alerts", headers=ops)).json()["items"]
        gpu = next(a for a in alerts if a["type"] == "gpu_fault")
        assert gpu["target_kind"] == "tenant" and gpu["target_id"] == str(user["user"]["id"])
        resp = await client.post(f"/api/admin/v1/alerts/{gpu['id']}/ack", headers=ops)
        assert resp.status_code == 200
        body = (await client.get("/api/admin/v1/alerts/unread-count", headers=ops)).json()
        assert body["count"] == 2
        assert body["critical_count"] == 1

    async def test_forged_namespace_without_real_user_no_tenant_notify(self, client, sm, fake):
        """namespace=tenant-<不存在的用户>:平台流照落,租户短信/站内信不出。"""
        await client.post("/api/v1/webhooks/alertmanager", json=AM_PAYLOAD, headers=AM_HEADERS)
        async with sm() as session:
            rows = (await session.execute(select(Notification))).scalars().all()
        assert [r.type for r in rows] == ["admin_alert"]

    async def test_severity_filter(self, client, sm, fake):
        user = await register(client, "13900000992")
        await client.post(
            "/api/v1/webhooks/alertmanager",
            json=am_payload_for(user["user"]["id"]),
            headers=AM_HEADERS,
        )
        ops = await admin_headers(sm, client, role="ops")
        critical = (
            await client.get("/api/admin/v1/alerts", params={"severity": "critical"}, headers=ops)
        ).json()["items"]
        assert len(critical) == 2
        assert all(a["severity"] == "critical" for a in critical)
        warning = (
            await client.get("/api/admin/v1/alerts", params={"severity": "warning"}, headers=ops)
        ).json()["items"]
        assert [a["title"] for a in warning] == ["管理员完成二要素(TOTP)绑定"]

    async def test_acked_filter_and_paging_reach_older_rows(self, client, sm, fake):
        """确认状态在库里过滤、游标能翻到更早的告警:否则超出单页的异常在管理端永远看不到。"""
        async with sm() as session:
            for i in range(25):
                session.add(
                    Notification(
                        user_id=None,
                        type="admin_alert",
                        severity="warning",
                        title=f"告警{i:02d}",
                        content="x",
                        dedup_key=f"t-page:{i}",
                    )
                )
            await session.commit()
        ops = await admin_headers(sm, client, role="ops")

        first = (await client.get("/api/admin/v1/alerts?limit=10", headers=ops)).json()
        assert len(first["items"]) == 10 and first["next_cursor"]
        oldest_seen = first["items"][-1]["id"]
        second = (
            await client.get(
                "/api/admin/v1/alerts",
                params={"limit": 10, "cursor": first["next_cursor"]},
                headers=ops,
            )
        ).json()
        assert len(second["items"]) == 10
        assert all(a["id"] < oldest_seen for a in second["items"])

        target = first["items"][0]["id"]
        assert (
            await client.post(f"/api/admin/v1/alerts/{target}/ack", headers=ops)
        ).status_code == 200
        acked = (
            await client.get("/api/admin/v1/alerts", params={"acked": True}, headers=ops)
        ).json()
        assert [a["id"] for a in acked["items"]] == [target]
        unacked = (
            await client.get(
                "/api/admin/v1/alerts", params={"acked": False, "limit": 100}, headers=ops
            )
        ).json()
        assert target not in [a["id"] for a in unacked["items"]]
        assert all(a["acked_at"] is None for a in unacked["items"])

    async def test_type_filter(self, client, sm, fake):
        """类型过滤在库里做:租户 GPU 故障与平台告警能分开看。"""
        user = await register(client, "13900000993")
        await client.post(
            "/api/v1/webhooks/alertmanager",
            json=am_payload_for(user["user"]["id"]),
            headers=AM_HEADERS,
        )
        ops = await admin_headers(sm, client, role="ops")
        faults = (
            await client.get("/api/admin/v1/alerts", params={"type": "gpu_fault"}, headers=ops)
        ).json()["items"]
        assert faults and all(a["type"] == "gpu_fault" for a in faults)
        bad = await client.get("/api/admin/v1/alerts", params={"type": "typo"}, headers=ops)
        assert bad.status_code == 422, "表外的 type 必须 422,不能当成空结果"

    async def test_ack_non_alert_404(self, client, sm, fake):
        """普通站内信不可确认:404。"""
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


class TestAlertmanagerAuthHardening:
    async def test_unconfigured_token_rejects_all(self, client, sm, fake, monkeypatch):
        """未配置 token 时任何环境一律返回 401。"""
        from app.core.config import get_settings

        monkeypatch.setattr(get_settings(), "alertmanager_token", None)
        resp = await client.post(
            "/api/v1/webhooks/alertmanager", json=AM_PAYLOAD, headers=AM_HEADERS
        )
        assert resp.status_code == 401

    async def test_missing_bearer_rejected(self, client, sm, fake):
        resp = await client.post("/api/v1/webhooks/alertmanager", json=AM_PAYLOAD)
        assert resp.status_code == 401


class TestAlertmanagerWebhookHardening:
    async def test_bad_json_rejected_with_400(self, client, sm, fake):
        resp = await client.post(
            "/api/v1/webhooks/alertmanager",
            content=b"not-json",
            headers={"Content-Type": "application/json", **AM_HEADERS},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "VALIDATION_ERROR"

    async def test_long_strings_truncated(self, client, sm, fake):
        """summary 等字段入库前截断。"""
        payload = {
            "alerts": [
                {
                    **AM_PAYLOAD["alerts"][0],
                    "fingerprint": "trunc1",
                    "annotations": {"summary": "x" * 5000},
                }
            ]
        }
        resp = await client.post("/api/v1/webhooks/alertmanager", json=payload, headers=AM_HEADERS)
        assert resp.status_code == 200
        async with sm() as session:
            row = (
                await session.execute(
                    select(Notification).where(Notification.type == "admin_alert")
                )
            ).scalar_one()
        assert len(row.content) == 1024

    async def test_alerts_list_capped(self, client, sm, fake, monkeypatch):
        """单次报文的 alerts 条数封顶。"""
        from app.modules.notify import router as notify_router

        monkeypatch.setattr(notify_router, "ALERT_MAX_ALERTS", 3)
        payload = {
            "alerts": [{**AM_PAYLOAD["alerts"][0], "fingerprint": f"cap{i}"} for i in range(5)]
        }
        resp = await client.post("/api/v1/webhooks/alertmanager", json=payload, headers=AM_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["ingested"] == 3

    async def test_ip_rate_limited(self, client, sm, fake, monkeypatch):
        """Alertmanager 接入按 IP 限流。"""
        from app.modules.notify import router as notify_router

        monkeypatch.setattr(notify_router, "ALERT_RATE_LIMIT", 2)
        for _ in range(2):
            resp = await client.post(
                "/api/v1/webhooks/alertmanager", json={"alerts": []}, headers=AM_HEADERS
            )
            assert resp.status_code == 200
        resp = await client.post(
            "/api/v1/webhooks/alertmanager", json={"alerts": []}, headers=AM_HEADERS
        )
        assert resp.status_code == 429
        assert resp.headers["retry-after"].isdigit()
