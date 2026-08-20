"""WP23 节点注册(管理侧 + 状态机):令牌生命周期、角色矩阵、审计不落 token。"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditLog
from app.core.errors import AppError
from app.core.platform_config import set_platform_settings
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
                "rke2_server_url": "https://10.0.0.10:9345",
                "rke2_join_token": "K10abcdef0123456789::server:secrettoken",
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
        assert "平台配置" in resp.json()["message"]

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

        # revoke 需 reason;吊销后 regenerate/再吊销均 409
        assert (
            await client.post(f"/api/admin/v1/node-enrollments/{eid}/revoke", json={}, headers=ah)
        ).status_code == 422
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
                EnrollmentCreate(pool="kata"),
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
        # 正常 bootstrap:pending→installing,拿到 join 参数
        async with sm() as session:
            row, cfg = await nodes_service.bootstrap(
                session,
                token,
                hostname="gpu-node-7",
                os_info={"os_release": "Ubuntu 24.04"},
                gpus=["RTX 4090"],
                client_ip="10.0.0.77",
            )
            assert row.status == "installing"
            assert cfg["rke2_join_token"].endswith("secrettoken")
        # 重复 bootstrap(脚本重跑)仍放行
        async with sm() as session:
            row, _ = await nodes_service.bootstrap(
                session, token, hostname="gpu-node-7", os_info={}, gpus=[], client_ip=None
            )
            assert row.status == "installing"

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
                    session, token2, hostname="other-host", os_info={}, gpus=[], client_ip=None
                )
            assert exc.value.http_status == 409
        async with sm() as session:
            with pytest.raises(AppError) as exc:
                await nodes_service.bootstrap(
                    session, token2, hostname="expected-host", os_info={}, gpus=[], client_ip=None
                )
            assert exc.value.http_status == 404

    async def test_progress_drives_status(self, sm) -> None:
        await set_cluster_config(sm)
        async with sm() as session:
            _e, token = await nodes_service.create_enrollment(
                session, EnrollmentCreate(pool="mig"), created_by=1, idempotency_key=None
            )
        async with sm() as session:
            await nodes_service.bootstrap(
                session, token, hostname="mig-node-1", os_info={}, gpus=[], client_ip=None
            )
        # 需要重启 → rebooting;续跑第一条进度 → installing;rke2_start ok → joining
        async with sm() as session:
            row = await nodes_service.report_progress(
                session, token, phase="reboot", state="rebooting", message=None
            )
            assert row.status == "rebooting"
        async with sm() as session:
            row = await nodes_service.report_progress(
                session, token, phase="registries", state="ok", message=None
            )
            assert row.status == "installing"
        async with sm() as session:
            row = await nodes_service.report_progress(
                session, token, phase="rke2_start", state="ok", message=None
            )
            assert row.status == "joining"
        # 失败上报 → failed 落 error;终态后再上报 → 404
        async with sm() as session:
            _e2, token2 = await nodes_service.create_enrollment(
                session, EnrollmentCreate(pool="hami"), created_by=1, idempotency_key=None
            )
        async with sm() as session:
            await nodes_service.bootstrap(
                session, token2, hostname="hami-node-9", os_info={}, gpus=[], client_ip=None
            )
        async with sm() as session:
            row = await nodes_service.report_progress(
                session, token2, phase="driver", state="failed", message="apt 安装失败"
            )
            assert row.status == "failed" and row.error is not None
            assert "apt" in row.error
        async with sm() as session:
            with pytest.raises(AppError) as exc:
                await nodes_service.report_progress(
                    session, token2, phase="driver", state="ok", message=None
                )
            assert exc.value.http_status == 404
