"""管理端补充 API:租户管理/调账双复核/节点/超卖报表/审计检索。"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.k8s import set_orchestrator
from app.core.k8s.fake import FakeOrchestrator
from app.modules.adminapi.service import create_admin
from tests.test_catalog import admin_headers
from tests.test_orchestrator_lifecycle import _provision_running

pytestmark = pytest.mark.usefixtures("fake")


@pytest.fixture
def fake():
    orch = FakeOrchestrator(auto_ready=False)
    set_orchestrator(orch)
    yield orch
    set_orchestrator(None)


async def second_admin_headers(
    sm: async_sessionmaker[AsyncSession], client, username: str, role: str = "finance"
) -> dict[str, str]:
    async with sm() as session:
        await create_admin(session, username, "pass1234", role)
    resp = await client.post(
        "/api/admin/v1/auth/login", json={"username": username, "password": "pass1234"}
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


class TestTenants:
    async def test_list_and_freeze(self, client, sm, fake):
        headers, uuid, user_id = await _provision_running(client, sm, fake)
        ah = await admin_headers(sm, client, role="ops")

        tenants = (await client.get("/api/admin/v1/tenants", headers=ah)).json()
        me = next(t for t in tenants if t["id"] == user_id)
        assert me["phone_masked"].startswith("139") and "****" in me["phone_masked"]
        assert me["instances"] == 1
        assert me["balance"] == "100.00"

        resp = await client.post(
            f"/api/admin/v1/tenants/{user_id}/freeze", json={"reason": "违规"}, headers=ah
        )
        assert resp.json()["status"] == "frozen"
        # 冻结后用户请求被拒
        resp = await client.get("/api/v1/me", headers=headers)
        assert resp.status_code == 403

        resp = await client.post(
            f"/api/admin/v1/tenants/{user_id}/unfreeze", json={"reason": "误判"}, headers=ah
        )
        assert resp.json()["status"] == "active"


class TestAdjustments:
    async def test_dual_review_flow(self, client, sm, fake):
        headers, uuid, user_id = await _provision_running(client, sm, fake)
        finance_a = await second_admin_headers(sm, client, "fin-a")
        finance_b = await second_admin_headers(sm, client, "fin-b")

        resp = await client.post(
            "/api/admin/v1/adjustments",
            json={"user_id": user_id, "amount": "25.50", "reason": "GPU 故障补偿"},
            headers=finance_a,
        )
        assert resp.status_code == 201, resp.text
        adj_id = resp.json()["id"]

        # 发起人自审 → 403
        resp = await client.post(
            f"/api/admin/v1/adjustments/{adj_id}/review",
            json={"approve": True},
            headers=finance_a,
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ADMIN_SECOND_REVIEW_REQUIRED"

        # 第二管理员复核 → 生效
        resp = await client.post(
            f"/api/admin/v1/adjustments/{adj_id}/review",
            json={"approve": True, "comment": "属实"},
            headers=finance_b,
        )
        assert resp.json()["status"] == "approved"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "125.50"

        # 已处理的单不可重复复核
        resp = await client.post(
            f"/api/admin/v1/adjustments/{adj_id}/review",
            json={"approve": True},
            headers=finance_b,
        )
        assert resp.status_code == 409

    async def test_negative_adjustment_and_reject(self, client, sm, fake):
        headers, uuid, user_id = await _provision_running(client, sm, fake)
        fin_a = await second_admin_headers(sm, client, "fin-c")
        fin_b = await second_admin_headers(sm, client, "fin-d")

        resp = await client.post(
            "/api/admin/v1/adjustments",
            json={"user_id": user_id, "amount": "-10.00", "reason": "误退回收"},
            headers=fin_a,
        )
        adj_id = resp.json()["id"]
        resp = await client.post(
            f"/api/admin/v1/adjustments/{adj_id}/review",
            json={"approve": False, "comment": "证据不足"},
            headers=fin_b,
        )
        assert resp.json()["status"] == "rejected"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "100.00"  # 驳回不动账

    async def test_readonly_cannot_create(self, client, sm, fake):
        ro = await admin_headers(sm, client, role="readonly")
        resp = await client.post(
            "/api/admin/v1/adjustments",
            json={"user_id": 1, "amount": "1.00", "reason": "test"},
            headers=ro,
        )
        assert resp.status_code == 403


class TestNodesAndReports:
    async def test_nodes_view(self, client, sm, fake):
        ah = await admin_headers(sm, client, role="ops")
        nodes = (await client.get("/api/admin/v1/nodes", headers=ah)).json()
        pools = {n["pool_label"] for n in nodes}
        assert {"kata", "hami", "mig"} <= pools

    async def test_oversell_report(self, client, sm, fake):
        headers, uuid, user_id = await _provision_running(client, sm, fake)  # hami 池 50% × 1
        ah = await admin_headers(sm, client, role="finance")
        report = (await client.get("/api/admin/v1/reports/oversell", headers=ah)).json()
        hami = next(r for r in report if r["pool"] == "hami")
        assert hami["physical_gpus"] == 32
        assert hami["sold_share"] == 0.5
        assert hami["oversell_ratio"] == round(0.5 / 32, 3)

    async def test_audit_search(self, client, sm, fake):
        headers, uuid, _user_id = await _provision_running(client, sm, fake)
        ah = await admin_headers(sm, client, role="admin")
        rows = (await client.get("/api/admin/v1/audit", headers=ah)).json()
        assert any(r["action"] == "POST /api/v1/instances" for r in rows)
        user_rows = (
            await client.get("/api/admin/v1/audit", params={"actor_type": "user"}, headers=ah)
        ).json()
        assert all(r["actor_type"] == "user" for r in user_rows)
