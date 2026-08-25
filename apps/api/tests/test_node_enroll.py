"""节点注册(管理侧 + 状态机):令牌生命周期、角色矩阵、审计不落 token。"""

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
from tests.test_catalog import admin_headers

CREATE_BODY = {"pool": "hami", "hostname": "gpu-node-7", "note": "机柜 A3", "ttl_hours": 24}


async def set_cluster_config(sm: async_sessionmaker[AsyncSession]) -> None:
    async with sm() as session:
        await set_platform_settings(
            session,
            {
                "cluster_server_url": "https://10.0.0.10:9345",
                "cluster_join_token": "K10abcdef0123456789::server:secrettoken",
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

        # 列表永不含 token;审计 detail 不落 token
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
        # 重放已轮换 token:旧 token 失效,新 token 有效
        assert r1.json()["token"] != r2.json()["token"]

    async def test_idempotency_replay_guarded_when_inflight(self, client, sm) -> None:
        """重放轮换令牌与 regenerate 同守卫:bootstrap 后(installing)重放 → 409,
        否则重放会掐断正在装机的脚本。"""
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

    async def test_role_matrix(self, client, sm) -> None:
        await set_cluster_config(sm)
        ro = await admin_headers(sm, client, role="readonly")
        fin = await admin_headers(sm, client, role="finance")
        assert (await client.get("/api/admin/v1/node-enrollments", headers=ro)).status_code == 200
        assert (
            await client.post("/api/admin/v1/node-enrollments", json=CREATE_BODY, headers=ro)
        ).status_code == 403
        assert (await client.get("/api/admin/v1/node-enrollments", headers=fin)).status_code == 403

    async def test_revoke_and_regenerate(self, client, sm) -> None:
        await set_cluster_config(sm)
        ah = await admin_headers(sm, client, role="ops")
        created = (
            await client.post("/api/admin/v1/node-enrollments", json=CREATE_BODY, headers=ah)
        ).json()
        eid = created["enrollment"]["id"]

        # pending 可 regenerate:换 token 回 pending
        regen = await client.post(
            f"/api/admin/v1/node-enrollments/{eid}/regenerate", json={}, headers=ah
        )
        assert regen.status_code == 200
        assert regen.json()["token"] != created["token"]

        # 吊销后 regenerate/再吊销均 409
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
        # 正常 bootstrap:pending→installing,拿到 join 参数与换发的 progress 令牌
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
            assert cfg["cluster_join_token"].endswith("secrettoken")
            # 窄化键面:支付/短信等敏感键绝不出注册链路
            assert "wechat_private_key" not in cfg and "sms_access_key_secret" not in cfg
            assert progress is not None and progress.startswith("sdlp_")
        # 注册令牌一次性:首跑已消费,重复 bootstrap 统一 404(防反复拉取 join token)
        async with sm() as session:
            with pytest.raises(AppError) as exc:
                await nodes_service.bootstrap(
                    session,
                    token,
                    hostname="gpu-node-7",
                    os_info={},
                    gpu_details=[],
                    client_ip=None,
                )
            assert exc.value.http_status == 404

        # 主机名不符 → failed + 409,令牌随即作废(后续统一 404)
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

    async def test_hostname_required_at_creation(self, sm) -> None:
        """签发时强制绑定主机名:不带 hostname 的创建请求直接被 schema 拒绝。"""
        from pydantic import ValidationError

        await set_cluster_config(sm)
        with pytest.raises(ValidationError):
            EnrollmentCreate.model_validate({"pool": "hami"})

    async def test_absolute_expiry_kills_inflight_token(self, sm) -> None:
        """令牌绝对过期:installing 也受 expires_at 约束(心跳不续命),过期即 404;
        落 expired 由对账器清扫(TestEnrollReconciler)。"""
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
        # progress 令牌/注册令牌均随绝对过期失效
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
        # 消费后的注册令牌不能再上报进度(只能由窄权限 progress 令牌上报)
        async with sm() as session:
            with pytest.raises(AppError) as exc:
                await nodes_service.report_progress(
                    session, token, phase="driver", state="ok", message=None
                )
            assert exc.value.http_status == 404
        # 需要重启 → rebooting;续跑第一条进度 → installing;agent_start ok → joining
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
            row = await nodes_service.report_progress(
                session, progress, phase="agent_start", state="ok", message=None
            )
            assert row.status == "joining"
        # 失败上报 → failed 落 error;终态后再上报 → 404
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
        # 赋值行替换为真实地址;护栏比较用的字面量原样保留(只替换第一次出现)
        base = get_settings().public_base_url.rstrip("/")
        assert f'API_BASE="{base}"' in resp.text
        assert resp.text.count("__API_BASE__") == 1  # 仅剩护栏比较字面量
        assert '!= "__API_BASE__"' in resp.text
        assert "/api/v1/node-enroll/bootstrap" in resp.text
        # 脚本零密钥;--token 已移除(token 只经 --token-file 文件传入,不进进程 argv)
        assert "sdlp_" not in resp.text
        assert "--token " not in resp.text

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

        # 无鉴权头 → 401;伪造令牌 → 统一 404
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
        assert body["rke2_join_token"].endswith("secrettoken")
        assert body["pool"] == "hami"
        assert body["rke2_server_url"] == "https://10.0.0.10:9345"
        # 首次 bootstrap 换发窄权限 progress 令牌,并下发脚本指纹
        assert body["progress_token"].startswith("sdlp_")
        assert len(body["script_sha256"]) == 64
        progress_bearer = {"Authorization": f"Bearer {body['progress_token']}"}

        # 注册令牌已消费:重复 bootstrap / 上报进度均 404
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

        # 进度推进:agent_start ok → joining;管理端列表可见且无 token
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

    async def test_revoked_token_uniform_404(self, client, sm) -> None:
        await set_cluster_config(sm)
        ah = await admin_headers(sm, client, role="ops")
        created = (
            await client.post(
                "/api/admin/v1/node-enrollments",
                json={"pool": "mig", "hostname": "n2"},
                headers=ah,
            )
        ).json()
        eid = created["enrollment"]["id"]
        await client.post(
            f"/api/admin/v1/node-enrollments/{eid}/revoke", json={"reason": "换机"}, headers=ah
        )
        resp = await client.post(
            "/api/v1/node-enroll/bootstrap",
            json={"hostname": "n2"},
            headers={"Authorization": f"Bearer {created['token']}"},
        )
        assert resp.status_code == 404

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
    async def test_joined_when_node_ready_and_pool_matches(self, client, sm) -> None:
        from app.core.k8s import set_orchestrator
        from app.core.k8s.base import NodeInfo
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

            # 节点未出现 → 不推进
            counts = await reconcile_enrollments_once(sm)
            assert counts == {"joined": 0, "failed": 0, "expired": 0}

            # K8s 出现 Ready 且池匹配 → joined,令牌即死;重复对账零动作
            fake.inject_node(
                NodeInfo(
                    name="gpu-b1-02",
                    pool_label="hami",
                    gpu_model_label="RTX4090",
                    gpu_total=8,
                    gpu_used=0,
                    status="Ready",
                )
            )
            counts = await reconcile_enrollments_once(sm)
            assert counts["joined"] == 1
            rows = (await client.get("/api/admin/v1/node-enrollments", headers=ah)).json()
            assert rows[0]["status"] == "joined" and rows[0]["joined_at"] is not None
            assert (
                await client.post(
                    "/api/v1/node-enroll/progress",
                    json={"phase": "x", "state": "ok"},
                    headers=bearer,
                )
            ).status_code == 404
            counts = await reconcile_enrollments_once(sm)
            assert counts == {"joined": 0, "failed": 0, "expired": 0}
        finally:
            set_orchestrator(None)

    async def test_pool_mismatch_fails(self, client, sm) -> None:
        from app.core.k8s import set_orchestrator
        from app.core.k8s.base import NodeInfo
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
            assert counts["failed"] == 1
            rows = (await client.get("/api/admin/v1/node-enrollments", headers=ah)).json()
            assert rows[0]["status"] == "failed" and "池标签不符" in rows[0]["error"]
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
            # pending 过期 → expired
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
            # installing 失联 → failed
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
        """对账器 SELECT ... FOR UPDATE SKIP LOCKED:行被并发事务(如 revoke)持锁时
        本轮跳过(下轮自愈);吊销提交后不得被对账的旧读数覆盖回非终态。"""
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
            # 让其满足「绝对过期 → expired」条件:若对账器读到该行就一定会迁移它
            async with sm() as session:
                await session.execute(
                    update(NodeEnrollment)
                    .where(NodeEnrollment.id == eid)
                    .values(expires_at=now_utc() - timedelta(minutes=1))
                )
                await session.commit()

            # 会话 A 持行锁并写入 revoked(模拟并发 revoke 未提交)
            async with sm() as locker:
                row = (
                    await locker.execute(
                        select(NodeEnrollment).where(NodeEnrollment.id == eid).with_for_update()
                    )
                ).scalar_one()
                row.status = "revoked"
                # skip_locked:对账器跳过被锁行,不阻塞也不迁移
                counts = await reconcile_enrollments_once(sm)
                assert counts == {"joined": 0, "failed": 0, "expired": 0}
                await locker.commit()

            # 吊销提交后:revoked 是终态,对账器不再选中,更不得覆盖成 expired
            counts = await reconcile_enrollments_once(sm)
            assert counts == {"joined": 0, "failed": 0, "expired": 0}
            rows = (await client.get("/api/admin/v1/node-enrollments", headers=ah)).json()
            assert rows[0]["status"] == "revoked"
        finally:
            set_orchestrator(None)


class TestNodeCordon:
    async def test_cordon_via_outbox_and_uncordon(self, client, sm) -> None:
        from app.core.k8s import set_orchestrator
        from app.core.k8s.fake import FakeOrchestrator
        from app.modules.nodes.patrol import node_spec_patrol
        from tests.helpers import drain, drain_strict

        fake = FakeOrchestrator()
        set_orchestrator(fake)
        try:
            ah = await admin_headers(sm, client, role="ops")
            await node_spec_patrol(sm)  # 台账就位(cordon 校验读 node_specs,不直连 K8s)
            # 未知节点 → 404
            assert (
                await client.post(
                    "/api/admin/v1/nodes/no-such-node/cordon",
                    json={"reason": "维护"},
                    headers=ah,
                )
            ).status_code == 404

            # cordon:请求只入队,drain 后 K8s 侧生效,列表可见 Cordoned
            resp = await client.post(
                "/api/admin/v1/nodes/fake-hami-node-1/cordon",
                json={"reason": "巡检维护"},
                headers=ah,
            )
            assert resp.status_code == 200 and resp.json()["queued"] is True
            assert "fake-hami-node-1" not in fake.cordoned_nodes  # 请求路径零 K8s 调用
            assert await drain_strict(sm) == (1, 0)  # cordon 任务必须成功而非仅被处理
            assert "fake-hami-node-1" in fake.cordoned_nodes
            from app.modules.nodes.patrol import node_spec_patrol

            await node_spec_patrol(sm)  # 节点视图只认巡检台账
            nodes = (await client.get("/api/admin/v1/nodes", headers=ah)).json()
            assert next(n for n in nodes if n["name"] == "fake-hami-node-1")["status"] == "Cordoned"

            # 重复 cordon 幂等;uncordon 恢复
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

            # readonly 不可写
            ro = await admin_headers(sm, client, role="readonly")
            assert (
                await client.post(
                    "/api/admin/v1/nodes/fake-hami-node-1/cordon",
                    json={"reason": "越权"},
                    headers=ro,
                )
            ).status_code == 403
        finally:
            set_orchestrator(None)

    async def test_out_of_order_replay_converges_to_latest_intent(self, client, sm) -> None:
        """乱序安全:cordon 与 uncordon 先后入队,handler 读台账期望态而非 payload,
        即便先发的 cordon 后执行,最终也收敛到 uncordon(管理员最后意图)。"""
        from app.core.k8s import set_orchestrator
        from app.core.k8s.fake import FakeOrchestrator
        from app.modules.nodes.patrol import node_spec_patrol
        from tests.helpers import drain

        fake = FakeOrchestrator()
        set_orchestrator(fake)
        try:
            ah = await admin_headers(sm, client, role="ops")
            await node_spec_patrol(sm)
            # 快速连发 cordon → uncordon(期望态最终为 False)
            await client.post(
                "/api/admin/v1/nodes/fake-hami-node-1/cordon",
                json={"reason": "维护"},
                headers=ah,
            )
            await client.post(
                "/api/admin/v1/nodes/fake-hami-node-1/uncordon",
                json={"reason": "完成"},
                headers=ah,
            )
            await drain(sm)  # 两个任务都按最新期望态执行:最终 uncordoned
            assert "fake-hami-node-1" not in fake.cordoned_nodes
            # 巡检收敛环:实际与期望一致,无收敛动作
            from sqlalchemy import select as _select

            from app.modules.nodes.models import NodeSpec

            async with sm() as session:
                row = (
                    await session.execute(
                        _select(NodeSpec).where(NodeSpec.node_name == "fake-hami-node-1")
                    )
                ).scalar_one()
                assert row.desired_unschedulable is False
        finally:
            set_orchestrator(None)
