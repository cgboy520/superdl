"""管理端 route×role 鉴权矩阵扫描(P1-41b)。

adminapi 端点表从 app.openapi() 自动收集;矩阵在本文件显式声明 ——
新增端点未登记即红(收集比对失败),角色门变更未过评审即红(行为断言失败)。

行为断言(每端点 × 每角色):
- 匿名(无 token):anon 端点不得 403,其余一律 401;
- 已认证角色:admin 恒许;白名单角色不得 401/403(业务层 404/422 与鉴权无关);
  非白名单角色一律 403。
token 直接铸造(不经登录,bcrypt 成本与本测试无关);MFA 绑定与登录链路
由 test_admin_mfa.py 覆盖。
"""

import re

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_token
from app.modules.adminapi.models import AdminUser

_ADMIN_ONLY: frozenset[str] = frozenset()
_OPS: frozenset[str] = frozenset({"ops"})
_OPS_RO: frozenset[str] = frozenset({"ops", "readonly"})
_ANY_READ: frozenset[str] = frozenset({"ops", "finance", "readonly"})
_FIN: frozenset[str] = frozenset({"finance"})
_FIN_RO: frozenset[str] = frozenset({"finance", "readonly"})

# 角色矩阵:"anon" = 匿名可达;"any" = 任意已认证管理角色;frozenset = 白名单(admin 恒许)
MATRIX: dict[str, str | frozenset[str]] = {
    "GET /api/admin/v1/adjustments": _FIN_RO,
    "POST /api/admin/v1/adjustments": _FIN,
    "POST /api/admin/v1/adjustments/{adjustment_id}/review": _FIN,
    "GET /api/admin/v1/admins": _ADMIN_ONLY,
    "POST /api/admin/v1/admins": _ADMIN_ONLY,
    "PATCH /api/admin/v1/admins/{admin_id}": _ADMIN_ONLY,
    "POST /api/admin/v1/admins/{admin_id}/mfa/reset": _ADMIN_ONLY,
    "POST /api/admin/v1/admins/{admin_id}/reset-password": _ADMIN_ONLY,
    "GET /api/admin/v1/alerts": _ANY_READ,
    "GET /api/admin/v1/alerts/unread-count": _ANY_READ,
    "POST /api/admin/v1/alerts/{alert_id}/ack": _OPS,
    "GET /api/admin/v1/announcements": _ANY_READ,
    "POST /api/admin/v1/announcements": _OPS,
    "POST /api/admin/v1/announcements/{announcement_id}/revoke": _OPS,
    "GET /api/admin/v1/audit": _ANY_READ,
    "GET /api/admin/v1/audit/export": _ANY_READ,
    "POST /api/admin/v1/auth/login": "anon",
    "POST /api/admin/v1/auth/login/mfa": "anon",
    "POST /api/admin/v1/auth/mfa/setup/begin": "anon",
    "POST /api/admin/v1/auth/mfa/setup/confirm": "anon",
    "POST /api/admin/v1/auth/refresh": "anon",
    "GET /api/admin/v1/cluster/gpu-models": _OPS_RO,
    "GET /api/admin/v1/cluster/status": _OPS_RO,
    "POST /api/admin/v1/cluster/test-connection": _OPS,
    "GET /api/admin/v1/deletion-requests": _ANY_READ,
    "POST /api/admin/v1/deletion-requests/{request_id}/approve": _ADMIN_ONLY,
    "POST /api/admin/v1/deletion-requests/{request_id}/reject": _ADMIN_ONLY,
    "GET /api/admin/v1/finance/anomalies": _FIN_RO,
    "POST /api/admin/v1/finance/orders/{order_no}/backfill": _FIN,
    "POST /api/admin/v1/finance/orders/{order_no}/verify": _FIN,
    "GET /api/admin/v1/images": _OPS_RO,
    "POST /api/admin/v1/images": _OPS,
    "DELETE /api/admin/v1/images/{image_id}": _OPS,
    "PATCH /api/admin/v1/images/{image_id}": _OPS,
    "GET /api/admin/v1/images/{image_id}/nodes": _OPS_RO,
    "POST /api/admin/v1/images/{image_id}/prewarm": _OPS,
    "GET /api/admin/v1/instances": _ANY_READ,
    "GET /api/admin/v1/instances/{uuid}/events": _ANY_READ,
    "POST /api/admin/v1/instances/{uuid}/force-stop": _OPS,
    "GET /api/admin/v1/invoices": _ANY_READ,
    "POST /api/admin/v1/invoices/{invoice_id}/issue": _FIN,
    "POST /api/admin/v1/invoices/{invoice_id}/reject": _FIN,
    "GET /api/admin/v1/legal-docs": _ANY_READ,
    "PUT /api/admin/v1/legal-docs/versions/{version_id}": _ADMIN_ONLY,
    "POST /api/admin/v1/legal-docs/versions/{version_id}/archive": _ADMIN_ONLY,
    "POST /api/admin/v1/legal-docs/versions/{version_id}/publish": _ADMIN_ONLY,
    "GET /api/admin/v1/legal-docs/{doc_key}/versions": _ANY_READ,
    "POST /api/admin/v1/legal-docs/{doc_key}/versions": _ADMIN_ONLY,
    "GET /api/admin/v1/me": "any",
    "POST /api/admin/v1/me/mfa/recovery-codes": "any",
    "POST /api/admin/v1/me/password": "any",
    "GET /api/admin/v1/node-enrollments": _OPS_RO,
    "POST /api/admin/v1/node-enrollments": _OPS,
    "POST /api/admin/v1/node-enrollments/{enrollment_id}/regenerate": _OPS,
    "POST /api/admin/v1/node-enrollments/{enrollment_id}/revoke": _OPS,
    "GET /api/admin/v1/nodes": _OPS_RO,
    "GET /api/admin/v1/nodes/port-pool": _OPS_RO,
    "POST /api/admin/v1/nodes/{node_name}/cordon": _OPS,
    "GET /api/admin/v1/nodes/{node_name}/metrics": _OPS_RO,
    "POST /api/admin/v1/nodes/{node_name}/uncordon": _OPS,
    "GET /api/admin/v1/orders": _FIN_RO,
    "GET /api/admin/v1/orders/export": _FIN_RO,
    "GET /api/admin/v1/outbox/dead": _OPS_RO,
    "GET /api/admin/v1/outbox/tasks": _ANY_READ,
    "POST /api/admin/v1/outbox/{task_id}/discard": _OPS,
    "POST /api/admin/v1/outbox/{task_id}/retry": _OPS,
    "GET /api/admin/v1/overview": _ANY_READ,
    "GET /api/admin/v1/platform-config": _ADMIN_ONLY,
    "PUT /api/admin/v1/platform-config": _ADMIN_ONLY,
    "POST /api/admin/v1/platform-config/test-sms": _ADMIN_ONLY,
    "GET /api/admin/v1/policies": _ANY_READ,
    "PUT /api/admin/v1/policies": _OPS,
    "GET /api/admin/v1/reconciliation": _FIN_RO,
    "GET /api/admin/v1/reconciliation/export": _FIN_RO,
    "GET /api/admin/v1/refunds": _FIN_RO,
    "POST /api/admin/v1/refunds/{refund_id}/cancel": _FIN,
    "POST /api/admin/v1/refunds/{refund_id}/payout": _FIN,
    "POST /api/admin/v1/refunds/{refund_id}/review": _FIN,
    "GET /api/admin/v1/reports/oversell": _ANY_READ,
    "GET /api/admin/v1/reports/revenue": _ANY_READ,
    "GET /api/admin/v1/skus": _ANY_READ,
    "POST /api/admin/v1/skus": _OPS,
    "GET /api/admin/v1/skus/capacity-preview": _OPS_RO,
    "PATCH /api/admin/v1/skus/{sku_id}": _OPS,
    "GET /api/admin/v1/skus/{sku_id}/impact": _ANY_READ,
    "GET /api/admin/v1/tenants": _ANY_READ,
    "GET /api/admin/v1/tenants/{user_id}/adjust-context": _ANY_READ,
    "GET /api/admin/v1/tenants/{user_id}/bills": _ANY_READ,
    "POST /api/admin/v1/tenants/{user_id}/freeze": _OPS,
    "GET /api/admin/v1/tenants/{user_id}/ledger": _ANY_READ,
    "GET /api/admin/v1/tenants/{user_id}/ledger/export": _ANY_READ,
    "GET /api/admin/v1/tenants/{user_id}/quota": _ANY_READ,
    "PUT /api/admin/v1/tenants/{user_id}/quota": _OPS,
    "POST /api/admin/v1/tenants/{user_id}/unfreeze": _OPS,
    "GET /api/admin/v1/tickets": _ANY_READ,
    "GET /api/admin/v1/tickets/{ticket_id}": _ANY_READ,
    "POST /api/admin/v1/tickets/{ticket_id}/reply": _OPS,
    "POST /api/admin/v1/tickets/{ticket_id}/status": _OPS,
}

_ROLES = ("readonly", "ops", "finance", "admin")


def _collect_admin_endpoints() -> set[str]:
    """从 OpenAPI 收集管理端端点表(与路由注册同一事实源)。"""
    from app.main import create_app

    spec = create_app().openapi()
    out: set[str] = set()
    for path, ops in spec["paths"].items():
        if not path.startswith("/api/admin/v1"):
            continue
        for method in ops:
            out.add(f"{method.upper()} {path}")
    return out


def test_matrix_matches_openapi_table() -> None:
    """端点表 ↔ 矩阵双向比对:新端点未登记/已删端点残留即红。"""
    endpoints = _collect_admin_endpoints()
    missing = endpoints - MATRIX.keys()
    stale = MATRIX.keys() - endpoints
    assert not missing and not stale, (
        f"角色矩阵未同步;未登记端点: {sorted(missing)};已删残留: {sorted(stale)}"
    )


def _sample_url(path: str) -> str:
    """路径参数替换为样例值(不存在实体:业务 404/422 均可,鉴权结论不受影响)。"""

    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        if name == "uuid":
            return "00000000-0000-0000-0000-000000000000"
        if name == "order_no":
            return "NOPE000000"
        if name == "node_name":
            return "node-nope"
        if name == "doc_key":
            return "terms"
        return "999999"  # int 主键类参数

    return re.sub(r"\{(\w+)\}", repl, path)


async def _mint_admin_headers(sm: async_sessionmaker[AsyncSession], role: str) -> dict[str, str]:
    """直接落 AdminUser 行 + 铸造 admin audience token(跳过登录的 bcrypt 成本;
    登录/MFA 链路本身由 test_admin_mfa.py 覆盖)。token_version 默认 0,与 ver 一致。"""
    async with sm() as session:
        admin = AdminUser(username=f"matrix-{role}", password_hash="x", role=role, status="active")
        session.add(admin)
        await session.commit()
        await session.refresh(admin)
        token = create_token(str(admin.id), "admin", extra={"ver": admin.token_version})
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("endpoint", sorted(MATRIX), ids=lambda e: e)
async def test_endpoint_role_gate(
    client: AsyncClient, sm: async_sessionmaker[AsyncSession], endpoint: str
) -> None:
    method, path = endpoint.split(" ", 1)
    allowed = MATRIX[endpoint]
    url = _sample_url(path)
    headers_by_role = {role: await _mint_admin_headers(sm, role) for role in _ROLES}

    async def call(headers: dict[str, str] | None) -> int:
        kwargs: dict = {"headers": headers or {}}
        if method != "GET":
            kwargs["json"] = {}  # 空体:参数校验 422 也属「已过鉴权」,与角色门正交
        resp = await client.request(method, url, **kwargs)
        return resp.status_code

    # 匿名
    anon_status = await call(None)
    if allowed == "anon":
        assert anon_status != 403, f"匿名端点不得 403:{endpoint}"
    else:
        assert anon_status == 401, f"匿名应 401:{endpoint} -> {anon_status}"

    # 已认证角色
    for role in _ROLES:
        status = await call(headers_by_role[role])
        if allowed == "anon":
            assert status != 403, f"匿名端点带 token 不得 403:{endpoint}({role})"
            continue
        if allowed == "any" or role == "admin" or role in allowed:
            assert status not in (401, 403), (
                f"{role} 应通过角色门:{endpoint} -> {status}(业务 404/422 可,401/403 不可)"
            )
        else:
            assert status == 403, f"{role} 应被 403 拦截:{endpoint} -> {status}"
