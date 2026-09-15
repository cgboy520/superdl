"""节点注册(管理侧 + 状态机):令牌生命周期、角色矩阵、审计不落 token。"""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditLog
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.platform_config import set_platform_settings
from app.core.timeutil import now_utc
from app.modules.nodes import service as nodes_service
from app.modules.nodes.models import NodeEnrollment
from app.modules.nodes.schemas import EnrollmentCreate
from tests.helpers import CREATE_BODY, admin_headers, drain, drain_strict


async def set_cluster_config(sm: async_sessionmaker[AsyncSession]) -> None:
    """写入节点接入的集群配置(server_url + join_token)。"""
    async with sm() as session:
        await set_platform_settings(
            session,
            {
                "cluster_server_url": "https://10.0.0.10:9345",
                "cluster_join_token": "agent-fixture-0123456789-secrettoken",
            },
            updated_by=None,
        )
        await session.commit()


async def enrollment_rows(sm: async_sessionmaker[AsyncSession]) -> list[NodeEnrollment]:
    async with sm() as session:
        return list((await session.execute(select(NodeEnrollment))).scalars())


class TestAdminEnrollments:
    async def test_create_requires_cluster_config(self, client, sm) -> None:
        ah = await admin_headers(sm, client, role="ops")
        resp = await client.post("/api/admin/v1/node-enrollments", json=CREATE_BODY, headers=ah)
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "nodes.clusterNotConfigured"

    async def test_create_returns_token_once_and_audits(self, client, sm) -> None:
        await set_cluster_config(sm)
        ah = await admin_headers(sm, client, role="ops")
        resp = await client.post("/api/admin/v1/node-enrollments", json=CREATE_BODY, headers=ah)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        token = body["token"]
        assert token.startswith("sdln_") and len(token) > 40
        assert token in body["curl_command"] and token in body["wget_command"]
        assert "/api/v1/node-enroll/script" in body["curl_command"]
        assert body["enrollment"]["status"] == "pending"

        rows = (await client.get("/api/admin/v1/node-enrollments", headers=ah)).json()
        assert token not in str(rows)
        async with sm() as session:
            logs = list(
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.target == f"node_enrollment:{body['enrollment']['id']}"
                        )
                    )
                ).scalars()
            )
            assert logs and all(token not in str(log.detail) for log in logs)

    async def test_idempotency_key_replay_no_duplicate(self, client, sm) -> None:
        await set_cluster_config(sm)
        ah = await admin_headers(sm, client, role="ops")
        headers = {**ah, "Idempotency-Key": "idem-node-1"}
        r1 = await client.post("/api/admin/v1/node-enrollments", json=CREATE_BODY, headers=headers)
        r2 = await client.post("/api/admin/v1/node-enrollments", json=CREATE_BODY, headers=headers)
        assert r1.status_code == 201 and r2.status_code == 201
        assert r1.json()["enrollment"]["id"] == r2.json()["enrollment"]["id"]
        assert len(await enrollment_rows(sm)) == 1
        assert r1.json()["token"] != r2.json()["token"]

    async def test_idempotency_replay_guarded_when_inflight(self, client, sm) -> None:
        """重放轮换令牌与 regenerate 同守卫:installing 重放 → 409。"""
        await set_cluster_config(sm)
        ah = await admin_headers(sm, client, role="ops")
        headers = {**ah, "Idempotency-Key": "idem-node-2"}
        r1 = await client.post("/api/admin/v1/node-enrollments", json=CREATE_BODY, headers=headers)
        assert r1.status_code == 201
        token = r1.json()["token"]
        resp = await client.post(
            "/api/v1/node-enroll/bootstrap",
            json={"hostname": "gpu-node-7"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        r2 = await client.post("/api/admin/v1/node-enrollments", json=CREATE_BODY, headers=headers)
        assert r2.status_code == 409
        assert r2.json()["message_key"] == "nodes.regenerateNotAllowed"

    async def test_revoke_and_regenerate(self, client, sm) -> None:
        await set_cluster_config(sm)
        ah = await admin_headers(sm, client, role="ops")
        created = (
            await client.post("/api/admin/v1/node-enrollments", json=CREATE_BODY, headers=ah)
        ).json()
        eid = created["enrollment"]["id"]

        regen = await client.post(
            f"/api/admin/v1/node-enrollments/{eid}/regenerate", json={}, headers=ah
        )
        assert regen.status_code == 200
        assert regen.json()["token"] != created["token"]

        resp = await client.post(
            f"/api/admin/v1/node-enrollments/{eid}/revoke", json={"reason": "误发"}, headers=ah
        )
        assert resp.status_code == 200 and resp.json()["status"] == "revoked"
        assert (
            await client.post(
                f"/api/admin/v1/node-enrollments/{eid}/revoke",
                json={"reason": "再吊"},
                headers=ah,
            )
        ).status_code == 409
        assert (
            await client.post(
                f"/api/admin/v1/node-enrollments/{eid}/regenerate", json={}, headers=ah
            )
        ).status_code == 409

    async def test_regenerate_reason_in_audit(self, client, sm) -> None:
        """带 reason 的重新生成:审计行 detail 含 reason(不落 token)。"""
        await set_cluster_config(sm)
        ah = await admin_headers(sm, client, role="ops")
        created = (
            await client.post("/api/admin/v1/node-enrollments", json=CREATE_BODY, headers=ah)
        ).json()
        eid = created["enrollment"]["id"]
        resp = await client.post(
            f"/api/admin/v1/node-enrollments/{eid}/regenerate",
            json={"reason": "装机命令外泄,轮换"},
            headers=ah,
        )
        assert resp.status_code == 200, resp.text
        token = resp.json()["token"]
        async with sm() as session:
            logs = list(
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.target == f"node_enrollment:{eid}")
                    )
                ).scalars()
            )
        regen = [log for log in logs if log.detail and log.detail.get("action") == "regenerate"]
        assert len(regen) == 1
        assert regen[0].detail["reason"] == "装机命令外泄,轮换"
        assert token not in str(regen[0].detail)


class TestEnrollmentStateMachine:
    async def test_illegal_transition_rejected(self, sm) -> None:
        await set_cluster_config(sm)
        async with sm() as session:
            enrollment, _token = await nodes_service.create_enrollment(
                session,
                EnrollmentCreate(pool="kata", hostname="kata-node-1"),
                created_by=1,
                idempotency_key=None,
            )
            with pytest.raises(AppError) as exc:
                nodes_service.transition_enrollment(enrollment, "joined")
            assert exc.value.http_status == 409

    async def test_bootstrap_flow_and_hostname_guard(self, sm) -> None:
        await set_cluster_config(sm)
        async with sm() as session:
            _e, token = await nodes_service.create_enrollment(
                session,
                EnrollmentCreate(pool="hami", hostname="gpu-node-7"),
                created_by=1,
                idempotency_key=None,
            )
        async with sm() as session:
            row, cfg, progress = await nodes_service.bootstrap(
                session,
                token,
                hostname="gpu-node-7",
                os_info={"os_release": "Ubuntu 24.04"},
                gpu_details=[{"name": "RTX 4090"}],
                client_ip="10.0.0.77",
            )
            assert row.status == "installing"
            assert cfg.cluster_join_token.endswith("secrettoken")
            assert progress is not None and progress.startswith("sdlp_")

        async with sm() as session:
            _e2, token2 = await nodes_service.create_enrollment(
                session,
                EnrollmentCreate(pool="hami", hostname="expected-host"),
                created_by=1,
                idempotency_key=None,
            )
        async with sm() as session:
            with pytest.raises(AppError) as exc:
                await nodes_service.bootstrap(
                    session,
                    token2,
                    hostname="other-host",
                    os_info={},
                    gpu_details=[],
                    client_ip=None,
                )
            assert exc.value.http_status == 409
        async with sm() as session:
            with pytest.raises(AppError) as exc:
                await nodes_service.bootstrap(
                    session,
                    token2,
                    hostname="expected-host",
                    os_info={},
                    gpu_details=[],
                    client_ip=None,
                )
            assert exc.value.http_status == 404

    async def test_concurrent_bootstrap_consumes_token_once(self, sm) -> None:
        """同一注册令牌并发 bootstrap:行锁下只有一个成功,另一个 404;挂了说明令牌可被消费两次。"""
        await set_cluster_config(sm)
        async with sm() as session:
            _e, token = await nodes_service.create_enrollment(
                session,
                EnrollmentCreate(pool="hami", hostname="gpu-race-1"),
                created_by=1,
                idempotency_key=None,
            )

        async def attempt():
            async with sm() as session:
                return await nodes_service.bootstrap(
                    session,
                    token,
                    hostname="gpu-race-1",
                    os_info={},
                    gpu_details=[],
                    client_ip=None,
                )

        results = await asyncio.gather(attempt(), attempt(), return_exceptions=True)
        ok = [r for r in results if not isinstance(r, BaseException)]
        failed = [r for r in results if isinstance(r, BaseException)]
        assert len(ok) == 1 and len(failed) == 1
        assert isinstance(failed[0], AppError) and failed[0].http_status == 404
        rows = await enrollment_rows(sm)
        assert [r.status for r in rows] == ["installing"]

    async def test_absolute_expiry_kills_inflight_token(self, sm) -> None:
        """令牌绝对过期:installing 也受 expires_at 约束,过期即 404。"""
        await set_cluster_config(sm)
        async with sm() as session:
            _e, token = await nodes_service.create_enrollment(
                session,
                EnrollmentCreate(pool="hami", hostname="gpu-ttl-1"),
                created_by=1,
                idempotency_key=None,
            )
        async with sm() as session:
            _row, _cfg, progress = await nodes_service.bootstrap(
                session, token, hostname="gpu-ttl-1", os_info={}, gpu_details=[], client_ip=None
            )
            assert progress is not None
        async with sm() as session:
            await session.execute(
                update(NodeEnrollment)
                .where(NodeEnrollment.progress_token_hash.is_not(None))
                .values(expires_at=now_utc() - timedelta(minutes=1))
            )
            await session.commit()
        async with sm() as session:
            with pytest.raises(AppError) as exc:
                await nodes_service.report_progress(
                    session, progress, phase="driver", state="ok", message=None
                )
            assert exc.value.http_status == 404

    async def test_progress_drives_status(self, sm) -> None:
        await set_cluster_config(sm)
        async with sm() as session:
            _e, token = await nodes_service.create_enrollment(
                session,
                EnrollmentCreate(pool="mig", hostname="mig-node-1"),
                created_by=1,
                idempotency_key=None,
            )
        async with sm() as session:
            _r, _c, progress = await nodes_service.bootstrap(
                session, token, hostname="mig-node-1", os_info={}, gpu_details=[], client_ip=None
            )
            assert progress is not None
        async with sm() as session:
            row = await nodes_service.report_progress(
                session, progress, phase="reboot", state="rebooting", message=None
            )
            assert row.status == "rebooting"
        async with sm() as session:
            row = await nodes_service.report_progress(
                session, progress, phase="registries", state="ok", message=None
            )
            assert row.status == "installing"
        async with sm() as session:
            _e2, token2 = await nodes_service.create_enrollment(
                session,
                EnrollmentCreate(pool="hami", hostname="hami-node-9"),
                created_by=1,
                idempotency_key=None,
            )
        async with sm() as session:
            _r2, _c2, progress2 = await nodes_service.bootstrap(
                session, token2, hostname="hami-node-9", os_info={}, gpu_details=[], client_ip=None
            )
            assert progress2 is not None
        async with sm() as session:
            row = await nodes_service.report_progress(
                session, progress2, phase="driver", state="failed", message="apt 安装失败"
            )
            assert row.status == "failed" and row.error is not None
            assert "apt" in row.error
        async with sm() as session:
            with pytest.raises(AppError) as exc:
                await nodes_service.report_progress(
                    session, progress2, phase="driver", state="ok", message=None
                )
            assert exc.value.http_status == 404


class TestEnrollRouterAnonymous:
    async def test_script_served_with_api_base(self, client, sm) -> None:
        resp = await client.get("/api/v1/node-enroll/script")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/x-shellscript")
        base = get_settings().public_base_url.rstrip("/")
        assert f'API_BASE="{base}"' in resp.text
        assert resp.text.count("__API_BASE__") == 1
        assert '!= "__API_BASE__"' in resp.text
        assert "/api/v1/node-enroll/bootstrap" in resp.text

    async def test_bootstrap_and_progress_http_flow(self, client, sm) -> None:
        await set_cluster_config(sm)
        ah = await admin_headers(sm, client, role="ops")
        created = (
            await client.post(
                "/api/admin/v1/node-enrollments",
                json={"pool": "hami", "hostname": "gpu-a3-01"},
                headers=ah,
            )
        ).json()
        token = created["token"]
        bearer = {"Authorization": f"Bearer {token}"}

        assert (
            await client.post("/api/v1/node-enroll/bootstrap", json={"hostname": "n1"})
        ).status_code == 401
        assert (
            await client.post(
                "/api/v1/node-enroll/bootstrap",
                json={"hostname": "n1"},
                headers={"Authorization": "Bearer sdln_forged"},
            )
        ).status_code == 404

        resp = await client.post(
            "/api/v1/node-enroll/bootstrap",
            json={
                "hostname": "gpu-a3-01",
                "os_info": {"os_release": "Ubuntu 24.04", "kernel": "6.8", "arch": "x86_64"},
                "gpu_details": [{"name": "NVIDIA RTX4090"}],
            },
            headers=bearer,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["cluster_join_token"].endswith("secrettoken")
        assert body["pool"] == "hami"
        assert body["cluster_server_url"] == "https://10.0.0.10:9345"
        assert body["progress_token"].startswith("sdlp_")
        assert len(body["script_sha256"]) == 64
        progress_bearer = {"Authorization": f"Bearer {body['progress_token']}"}

        assert (
            await client.post(
                "/api/v1/node-enroll/bootstrap", json={"hostname": "gpu-a3-01"}, headers=bearer
            )
        ).status_code == 404
        assert (
            await client.post(
                "/api/v1/node-enroll/progress",
                json={"phase": "driver", "state": "ok"},
                headers=bearer,
            )
        ).status_code == 404

        for phase, state in [("driver", "ok"), ("agent_install", "ok"), ("agent_start", "ok")]:
            resp = await client.post(
                "/api/v1/node-enroll/progress",
                json={"phase": phase, "state": state},
                headers=progress_bearer,
            )
            assert resp.status_code == 204, resp.text
        rows = (await client.get("/api/admin/v1/node-enrollments?active=true", headers=ah)).json()
        assert rows[0]["status"] == "joining" and rows[0]["node_name"] == "gpu-a3-01"
        assert token not in str(rows) and body["progress_token"] not in str(rows)

    async def test_bootstrap_rate_limited(self, client, sm) -> None:
        statuses = []
        for _ in range(31):
            r = await client.post(
                "/api/v1/node-enroll/bootstrap",
                json={"hostname": "n3"},
                headers={"Authorization": "Bearer sdln_bogus"},
            )
            statuses.append(r.status_code)
        assert statuses[:30] == [404] * 30
        assert statuses[30] == 429


class TestEnrollReconciler:
    async def test_platform_labels_node_then_joins(self, client, sm) -> None:
        """入网时池标签由平台打:节点以未打标状态注册,对账器打整套标签后才判 joined。"""
        from app.core.k8s import set_orchestrator
        from app.core.k8s.base import POOL_NODE_LABEL, NodeInfo
        from app.core.k8s.fake import FakeOrchestrator
        from app.modules.nodes.reconciler import reconcile_enrollments_once

        fake = FakeOrchestrator()
        set_orchestrator(fake)
        try:
            await set_cluster_config(sm)
            ah = await admin_headers(sm, client, role="ops")
            created = (
                await client.post(
                    "/api/admin/v1/node-enrollments",
                    json={"pool": "hami", "hostname": "gpu-b1-02"},
                    headers=ah,
                )
            ).json()
            token = created["token"]
            bearer = {"Authorization": f"Bearer {token}"}
            boot = await client.post(
                "/api/v1/node-enroll/bootstrap", json={"hostname": "gpu-b1-02"}, headers=bearer
            )
            progress_bearer = {"Authorization": f"Bearer {boot.json()['progress_token']}"}
            await client.post(
                "/api/v1/node-enroll/progress",
                json={"phase": "agent_start", "state": "ok"},
                headers=progress_bearer,
            )

            counts = await reconcile_enrollments_once(sm)
            assert counts == {"joined": 0, "failed": 0, "expired": 0, "labeled": 0}

            fake.inject_node(
                NodeInfo(
                    name="gpu-b1-02",
                    pool_label="",
                    gpu_model_label="RTX4090",
                    gpu_total=8,
                    gpu_used=0,
                    status="Ready",
                )
            )
            counts = await reconcile_enrollments_once(sm)
            assert counts["labeled"] == 1 and counts["joined"] == 1
            labels = fake.node_labels["gpu-b1-02"]
            assert labels[POOL_NODE_LABEL] == "hami"
            assert labels["nvidia.com/gpu.deploy.device-plugin"] == "false"
            rows = (await client.get("/api/admin/v1/node-enrollments", headers=ah)).json()
            assert rows[0]["status"] == "joined" and rows[0]["joined_at"] is not None
            counts = await reconcile_enrollments_once(sm)
            assert counts == {"joined": 0, "failed": 0, "expired": 0, "labeled": 0}
        finally:
            set_orchestrator(None)

    async def test_node_self_declared_pool_is_overwritten(self, client, sm) -> None:
        """平台按登记覆盖节点池标签,节点正常入网。"""
        from app.core.k8s import set_orchestrator
        from app.core.k8s.base import POOL_NODE_LABEL, NodeInfo
        from app.core.k8s.fake import FakeOrchestrator
        from app.modules.nodes.reconciler import reconcile_enrollments_once

        fake = FakeOrchestrator()
        set_orchestrator(fake)
        try:
            await set_cluster_config(sm)
            ah = await admin_headers(sm, client, role="ops")
            created = (
                await client.post(
                    "/api/admin/v1/node-enrollments",
                    json={"pool": "kata", "hostname": "wrong-pool-node"},
                    headers=ah,
                )
            ).json()
            bearer = {"Authorization": f"Bearer {created['token']}"}
            await client.post(
                "/api/v1/node-enroll/bootstrap",
                json={"hostname": "wrong-pool-node"},
                headers=bearer,
            )
            fake.inject_node(
                NodeInfo(
                    name="wrong-pool-node",
                    pool_label="hami",
                    gpu_model_label="RTX4090",
                    gpu_total=8,
                    gpu_used=0,
                    status="Ready",
                )
            )
            counts = await reconcile_enrollments_once(sm)
            assert counts["failed"] == 0 and counts["joined"] == 1
            assert fake.node_labels["wrong-pool-node"][POOL_NODE_LABEL] == "kata"
            rows = (await client.get("/api/admin/v1/node-enrollments", headers=ah)).json()
            assert rows[0]["status"] == "joined"
        finally:
            set_orchestrator(None)

    async def test_expired_and_stale_heartbeat(self, client, sm) -> None:
        from datetime import timedelta

        from sqlalchemy import update

        from app.core.k8s import set_orchestrator
        from app.core.k8s.fake import FakeOrchestrator
        from app.core.timeutil import now_utc
        from app.modules.nodes.reconciler import reconcile_enrollments_once

        set_orchestrator(FakeOrchestrator())
        try:
            await set_cluster_config(sm)
            ah = await admin_headers(sm, client, role="ops")
            e1 = (
                await client.post(
                    "/api/admin/v1/node-enrollments",
                    json={"pool": "mig", "hostname": "mig-stale-1"},
                    headers=ah,
                )
            ).json()
            async with sm() as session:
                await session.execute(
                    update(NodeEnrollment)
                    .where(NodeEnrollment.id == e1["enrollment"]["id"])
                    .values(expires_at=now_utc() - timedelta(minutes=1))
                )
                await session.commit()
            e2 = (
                await client.post(
                    "/api/admin/v1/node-enrollments",
                    json={"pool": "hami", "hostname": "stale-node"},
                    headers=ah,
                )
            ).json()
            await client.post(
                "/api/v1/node-enroll/bootstrap",
                json={"hostname": "stale-node"},
                headers={"Authorization": f"Bearer {e2['token']}"},
            )
            async with sm() as session:
                await session.execute(
                    update(NodeEnrollment)
                    .where(NodeEnrollment.id == e2["enrollment"]["id"])
                    .values(last_report_at=now_utc() - timedelta(hours=3))
                )
                await session.commit()

            counts = await reconcile_enrollments_once(sm)
            assert counts["expired"] == 1 and counts["failed"] == 1
        finally:
            set_orchestrator(None)

    async def test_skip_locked_row_never_clobbers_concurrent_revoke(self, client, sm) -> None:
        """对账器 FOR UPDATE SKIP LOCKED:被锁行本轮跳过;吊销提交后不被覆盖回非终态。"""
        from app.core.k8s import set_orchestrator
        from app.core.k8s.fake import FakeOrchestrator
        from app.modules.nodes.reconciler import reconcile_enrollments_once

        set_orchestrator(FakeOrchestrator())
        try:
            await set_cluster_config(sm)
            ah = await admin_headers(sm, client, role="ops")
            created = (
                await client.post(
                    "/api/admin/v1/node-enrollments",
                    json={"pool": "hami", "hostname": "lock-node"},
                    headers=ah,
                )
            ).json()
            eid = created["enrollment"]["id"]
            await client.post(
                "/api/v1/node-enroll/bootstrap",
                json={"hostname": "lock-node"},
                headers={"Authorization": f"Bearer {created['token']}"},
            )
            async with sm() as session:
                await session.execute(
                    update(NodeEnrollment)
                    .where(NodeEnrollment.id == eid)
                    .values(expires_at=now_utc() - timedelta(minutes=1))
                )
                await session.commit()

            async with sm() as locker:
                row = (
                    await locker.execute(
                        select(NodeEnrollment).where(NodeEnrollment.id == eid).with_for_update()
                    )
                ).scalar_one()
                row.status = "revoked"
                counts = await reconcile_enrollments_once(sm)
                assert counts == {"joined": 0, "failed": 0, "expired": 0, "labeled": 0}
                await locker.commit()

            counts = await reconcile_enrollments_once(sm)
            assert counts == {"joined": 0, "failed": 0, "expired": 0, "labeled": 0}
            rows = (await client.get("/api/admin/v1/node-enrollments", headers=ah)).json()
            assert rows[0]["status"] == "revoked"
        finally:
            set_orchestrator(None)


class TestNodeCordon:
    async def test_cordon_via_outbox_and_uncordon(self, client, sm) -> None:
        from app.core.k8s import set_orchestrator
        from app.core.k8s.fake import FakeOrchestrator
        from app.modules.nodes.patrol import node_spec_patrol

        fake = FakeOrchestrator()
        set_orchestrator(fake)
        try:
            ah = await admin_headers(sm, client, role="ops")
            await node_spec_patrol(sm)
            assert (
                await client.post(
                    "/api/admin/v1/nodes/no-such-node/cordon",
                    json={"reason": "维护"},
                    headers=ah,
                )
            ).status_code == 404

            resp = await client.post(
                "/api/admin/v1/nodes/fake-hami-node-1/cordon",
                json={"reason": "巡检维护"},
                headers=ah,
            )
            assert resp.status_code == 200 and resp.json()["queued"] is True
            assert "fake-hami-node-1" not in fake.cordoned_nodes
            assert await drain_strict(sm) == (1, 0)
            assert "fake-hami-node-1" in fake.cordoned_nodes
            from app.modules.nodes.patrol import node_spec_patrol

            await node_spec_patrol(sm)
            nodes = (await client.get("/api/admin/v1/nodes", headers=ah)).json()
            assert next(n for n in nodes if n["name"] == "fake-hami-node-1")["status"] == "Cordoned"

            await client.post(
                "/api/admin/v1/nodes/fake-hami-node-1/cordon",
                json={"reason": "再次"},
                headers=ah,
            )
            await drain(sm)
            assert "fake-hami-node-1" in fake.cordoned_nodes
            await client.post(
                "/api/admin/v1/nodes/fake-hami-node-1/uncordon",
                json={"reason": "维护完成"},
                headers=ah,
            )
            await drain(sm)
            assert "fake-hami-node-1" not in fake.cordoned_nodes
        finally:
            set_orchestrator(None)

    async def test_out_of_order_replay_converges_to_latest_intent(self, client, sm) -> None:
        """乱序安全:handler 按台账期望态而非 payload 执行,最终仍是 uncordon。"""
        from app.core.k8s import set_orchestrator
        from app.core.k8s.fake import FakeOrchestrator
        from app.core.outbox import OutboxTask
        from app.modules.nodes.models import NodeSpec
        from app.modules.nodes.patrol import node_spec_patrol

        fake = FakeOrchestrator()
        set_orchestrator(fake)
        try:
            ah = await admin_headers(sm, client, role="ops")
            await node_spec_patrol(sm)
            await client.post(
                "/api/admin/v1/nodes/fake-hami-node-1/cordon",
                json={"reason": "维护"},
                headers=ah,
            )
            async with sm() as session:
                cordon_task = (
                    await session.execute(
                        select(OutboxTask).where(OutboxTask.type == "node.cordon")
                    )
                ).scalar_one()
                cordon_task.next_retry_at = now_utc() + timedelta(hours=1)
                await session.commit()
                cordon_id = cordon_task.id
            await client.post(
                "/api/admin/v1/nodes/fake-hami-node-1/uncordon",
                json={"reason": "完成"},
                headers=ah,
            )
            assert await drain(sm) == 1
            assert "fake-hami-node-1" not in fake.cordoned_nodes
            async with sm() as session:
                await session.execute(
                    update(OutboxTask)
                    .where(OutboxTask.id == cordon_id)
                    .values(next_retry_at=now_utc())
                )
                await session.commit()
            assert await drain(sm) == 1
            assert "fake-hami-node-1" not in fake.cordoned_nodes
            async with sm() as session:
                row = (
                    await session.execute(
                        select(NodeSpec).where(NodeSpec.node_name == "fake-hami-node-1")
                    )
                ).scalar_one()
                assert row.desired_unschedulable is False
        finally:
            set_orchestrator(None)


class TestNodeDecommission:
    """节点退役:彻底摘掉节点,令牌不可复用。"""

    @staticmethod
    async def _joined_node(client, sm, *, hostname: str, pool: str = "hami") -> dict:
        """推进节点登记为 joined,返回管理员 headers 与进度令牌。"""
        from app.core.k8s import get_orchestrator
        from app.core.k8s.base import NodeInfo
        from app.modules.nodes.reconciler import reconcile_enrollments_once

        await set_cluster_config(sm)
        ah = await admin_headers(sm, client, role="ops")
        created = (
            await client.post(
                "/api/admin/v1/node-enrollments",
                json={"pool": pool, "hostname": hostname},
                headers=ah,
            )
        ).json()
        boot = await client.post(
            "/api/v1/node-enroll/bootstrap",
            json={"hostname": hostname},
            headers={"Authorization": f"Bearer {created['token']}"},
        )
        get_orchestrator().inject_node(  # type: ignore[attr-defined]
            NodeInfo(
                name=hostname,
                pool_label=pool,
                gpu_model_label="RTX4090",
                gpu_total=8,
                gpu_used=0,
                status="Ready",
            )
        )
        await reconcile_enrollments_once(sm)
        return {"headers": ah, "progress_token": boot.json()["progress_token"]}

    async def test_decommission_revokes_token_and_deletes_node(self, client, sm) -> None:
        """退役:停调度期望态落台账、令牌作废、经 outbox 从集群删 Node。"""
        from app.core.k8s import get_orchestrator, set_orchestrator
        from app.core.k8s.fake import FakeOrchestrator
        from app.core.outbox import OutboxTask
        from app.modules.nodes.models import NodeSpec
        from app.modules.nodes.patrol import node_spec_patrol

        fake = FakeOrchestrator()
        set_orchestrator(fake)
        try:
            ctx = await self._joined_node(client, sm, hostname="sold-node-1")
            ah = ctx["headers"]
            await node_spec_patrol(sm)

            resp = await client.post(
                "/api/admin/v1/nodes/sold-node-1/decommission",
                json={"reason": "机器已售出"},
                headers=ah,
            )
            assert resp.status_code == 200
            assert resp.json() == {
                "node_name": "sold-node-1",
                "revoked_enrollments": 1,
                "queued": True,
            }
            assert "sold-node-1" not in fake.deleted_nodes
            async with sm() as session:
                enrollment = (
                    await session.execute(
                        select(NodeEnrollment).where(NodeEnrollment.node_name == "sold-node-1")
                    )
                ).scalar_one()
                assert enrollment.status == "revoked"
                assert "机器已售出" in (enrollment.error or "")
                row = (
                    await session.execute(
                        select(NodeSpec).where(NodeSpec.node_name == "sold-node-1")
                    )
                ).scalar_one()
                assert row.desired_unschedulable is True
                types = [t.type for t in (await session.execute(select(OutboxTask))).scalars()]
                assert "node.decommission" in types

            assert (
                await client.post(
                    "/api/v1/node-enroll/progress",
                    json={"phase": "waiting_node", "state": "ok"},
                    headers={"Authorization": f"Bearer {ctx['progress_token']}"},
                )
            ).status_code == 404

            assert await drain_strict(sm) == (1, 0)
            assert "sold-node-1" in fake.deleted_nodes
            assert all(n.name != "sold-node-1" for n in await get_orchestrator().list_nodes())
        finally:
            set_orchestrator(None)

    async def test_decommission_replay_on_absent_node_succeeds(self, client, sm) -> None:
        """节点已不在集群时重放删除成功,不进死信。"""
        from app.core.k8s import set_orchestrator
        from app.core.k8s.fake import FakeOrchestrator
        from app.core.outbox import enqueue
        from app.modules.nodes.patrol import node_spec_patrol

        fake = FakeOrchestrator()
        set_orchestrator(fake)
        try:
            ctx = await self._joined_node(client, sm, hostname="seized-node-1")
            await node_spec_patrol(sm)
            await client.post(
                "/api/admin/v1/nodes/seized-node-1/decommission",
                json={"reason": "设备被扣押"},
                headers=ctx["headers"],
            )
            assert await drain_strict(sm) == (1, 0)
            async with sm() as session:
                enqueue(session, "node.decommission", {"node_name": "seized-node-1"})
                await session.commit()
            assert await drain_strict(sm) == (1, 0)
        finally:
            set_orchestrator(None)

    async def test_decommission_unknown_node_404(self, client, sm) -> None:
        """台账里没有的节点名不产出删除任务。"""
        from app.core.k8s import set_orchestrator
        from app.core.k8s.fake import FakeOrchestrator

        set_orchestrator(FakeOrchestrator())
        try:
            ah = await admin_headers(sm, client, role="ops")
            resp = await client.post(
                "/api/admin/v1/nodes/no-such-node/decommission",
                json={"reason": "误操作"},
                headers=ah,
            )
            assert resp.status_code == 404
            assert resp.json()["message_key"] == "nodes.nodeNotFound"
            assert await drain(sm) == 0
        finally:
            set_orchestrator(None)

    async def test_manual_revoke_still_rejects_terminal(self, client, sm) -> None:
        """「终态 → revoked」只给退役用:管理端手工吊销终态仍 409。"""
        from app.core.k8s import set_orchestrator
        from app.core.k8s.fake import FakeOrchestrator

        fake = FakeOrchestrator()
        set_orchestrator(fake)
        try:
            ctx = await self._joined_node(client, sm, hostname="joined-node-1")
            rows = await enrollment_rows(sm)
            resp = await client.post(
                f"/api/admin/v1/node-enrollments/{rows[0].id}/revoke",
                json={"reason": "手工吊销"},
                headers=ctx["headers"],
            )
            assert resp.status_code == 409
            assert resp.json()["message_key"] == "nodes.alreadyTerminal"
        finally:
            set_orchestrator(None)
