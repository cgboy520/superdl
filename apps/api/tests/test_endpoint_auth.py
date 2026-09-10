"""网关鉴权回调 /api/internal/v1/endpoint-auth 的鉴权矩阵与响应纪律。

这条端点是在线服务的全部访问控制:它放行谁,谁就能打到用户容器。
- 鉴权矩阵挂了 = 平台在替用户漏钥匙(别人的 Key 能开你的门,或同一用户的 Key 串服务)
- 响应纪律挂了 = 拒绝响应把平台内部信息透给任意第三方(响应体会原样回给调用方)
- 审计豁免挂了 = 按服务 QPS 往 audit_log 灌行,真正要查的写操作被埋掉
- catch-all 路径挂了 = extAuth 把 path 当前缀时整条链路 404 → fail-close 全站不可用
"""

import pytest
from sqlalchemy import func, select

from app.core.audit import AuditLog
from app.core.config import get_settings
from app.modules.orchestrator.reconciler import reconcile_once
from app.modules.services.models import ServiceApiKey
from tests.helpers import drain, new_user, provision_service

pytestmark = pytest.mark.usefixtures("fake")

AUTH_PATH = "/api/internal/v1/endpoint-auth"


def host_for(slug: str) -> str:
    return f"{slug}.{get_settings().service_domain_suffix}"


async def issue_key(client, headers, slug: str, name: str = "k") -> str:
    resp = await client.post(
        f"/api/v1/services/{slug}/api-keys", json={"name": name}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["key"]


async def call_auth(client, *, slug: str, key: str | None = None, path: str = "", **over):
    headers = {"host": host_for(slug)}
    if key is not None:
        headers["x-api-key"] = key
    headers.update(over)
    return await client.get(f"{AUTH_PATH}{path}", headers=headers)


class TestAuthMatrix:
    async def test_valid_key_passes_with_identity_headers(self, client, sm, fake):
        """合法 Key 放行,并回归属头。两个头必须恒回:headersToBackend 是覆盖语义。"""
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000401")
        key = await issue_key(client, headers, svc["slug"])
        resp = await call_auth(client, slug=svc["slug"], key=key)
        assert resp.status_code == 200, resp.text
        assert resp.headers["x-superdl-endpoint"] == svc["slug"]
        assert resp.headers["x-superdl-key-id"].isdigit()

    async def test_bearer_header_also_accepted(self, client, sm, fake):
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000402")
        key = await issue_key(client, headers, svc["slug"])
        resp = await call_auth(client, slug=svc["slug"], authorization=f"Bearer {key}")
        assert resp.status_code == 200, resp.text

    async def test_wrong_key_denied(self, client, sm, fake):
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000403")
        await issue_key(client, headers, svc["slug"])
        assert (
            await call_auth(client, slug=svc["slug"], key="sk-not-a-real-key")
        ).status_code == 401

    async def test_missing_key_denied(self, client, sm, fake):
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000404")
        await issue_key(client, headers, svc["slug"])
        assert (await call_auth(client, slug=svc["slug"])).status_code == 401

    async def test_revoked_key_denied(self, client, sm, fake):
        """吊销即刻生效:缓存条目被主动失效,不靠等 TTL。"""
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000405")
        slug = svc["slug"]
        resp = await client.post(
            f"/api/v1/services/{slug}/api-keys", json={"name": "k"}, headers=headers
        )
        key, key_id = resp.json()["key"], resp.json()["id"]
        assert (await call_auth(client, slug=slug, key=key)).status_code == 200
        await client.delete(f"/api/v1/services/{slug}/api-keys/{key_id}", headers=headers)
        assert (await call_auth(client, slug=slug, key=key)).status_code == 401

    async def test_cross_tenant_key_denied(self, client, sm, fake):
        """A 用户的 Key 打 B 用户的服务必须 401:Key 是全局唯一高熵串,「查得到」不等于「用得上」。"""
        a_headers, a_svc, _ = await provision_service(client, sm, fake, phone="13900000406")
        _b_headers, b_svc, _ = await provision_service(client, sm, fake, phone="13900000407")
        a_key = await issue_key(client, a_headers, a_svc["slug"])
        assert (await call_auth(client, slug=b_svc["slug"], key=a_key)).status_code == 401

    async def test_same_user_other_service_denied(self, client, sm, fake):
        """同一用户的 Key 也不能跨服务:归属是服务级,不是账号级。"""
        headers, first, _ = await provision_service(client, sm, fake, phone="13900000412")
        second = await client.post(
            "/api/v1/services",
            json={
                "sku_id": first["current_instance"]["sku_id"],
                "image_ref": first["container"]["image_ref"],
                "ssh_key_ids": [],
                "service_port": 8000,
            },
            headers=headers,
        )
        assert second.status_code == 202, second.text
        key = await issue_key(client, headers, first["slug"])
        # 第二个服务还没就绪也一样 401:归属不对是第一道就拦下的
        assert (await call_auth(client, slug=second.json()["slug"], key=key)).status_code == 401
        assert (await call_auth(client, slug=first["slug"], key=key)).status_code == 200

    async def test_public_endpoint_needs_no_key(self, client, sm, fake):
        """require_api_key=false 的服务无 Key 也放行,但归属头照回;PATCH 翻回去即刻要 Key。"""
        headers, svc, _ = await provision_service(
            client, sm, fake, phone="13900000408", require_api_key=False
        )
        slug = svc["slug"]
        resp = await call_auth(client, slug=slug)
        assert resp.status_code == 200, resp.text
        assert resp.headers["x-superdl-endpoint"] == slug
        # 匿名也必须显式回 key-id:省略这个头 = 客户端伪造值原样透传
        assert resp.headers["x-superdl-key-id"] == "anonymous"
        patched = await client.patch(
            f"/api/v1/services/{slug}", json={"require_api_key": True}, headers=headers
        )
        assert patched.status_code == 200, patched.text
        assert (await call_auth(client, slug=slug)).status_code == 401

    async def test_unknown_slug_denied(self, client, sm, fake):
        """服务不存在与密钥不对同码同文案:区分开就是一个枚举平台端点的预言机。"""
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000409")
        key = await issue_key(client, headers, svc["slug"])
        resp = await call_auth(client, slug="svc-doesnotex", key=key)
        assert resp.status_code == 401
        assert resp.json()["code"] == "API_KEY_INVALID"

    async def test_foreign_host_denied(self, client, sm, fake):
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000410")
        key = await issue_key(client, headers, svc["slug"])
        resp = await client.get(
            AUTH_PATH, headers={"host": "svc-abc.evil.example.com", "x-api-key": key}
        )
        assert resp.status_code == 401

    async def test_stopped_service_denied(self, client, sm, fake):
        """当前实例非 running 一律拒:Pod 可能还在优雅删除期活着,光删 HTTPRoute 有窗口。"""
        headers, svc, user_id = await provision_service(client, sm, fake, phone="13900000411")
        slug = svc["slug"]
        key = await issue_key(client, headers, slug)
        assert (await call_auth(client, slug=slug, key=key)).status_code == 200
        await client.post(f"/api/v1/services/{slug}/stop", headers=headers)
        await drain(sm)
        fake.finish_delete(f"tenant-{user_id}", svc["current_instance"]["uuid"])
        await reconcile_once(sm)
        assert (await client.get(f"/api/v1/services/{slug}", headers=headers)).json()[
            "status"
        ] == "stopped"
        assert (await call_auth(client, slug=slug, key=key)).status_code == 401

    async def test_deleted_service_denied(self, client, sm, fake):
        """删除服务即全部密钥吊销、服务不再放行,即使 slug 仍在库里。"""
        headers, svc, user_id = await provision_service(client, sm, fake, phone="13900000413")
        slug = svc["slug"]
        key = await issue_key(client, headers, slug)
        await client.post(f"/api/v1/services/{slug}/stop", headers=headers)
        await drain(sm)
        fake.finish_delete(f"tenant-{user_id}", svc["current_instance"]["uuid"])
        await reconcile_once(sm)
        assert (await client.delete(f"/api/v1/services/{slug}", headers=headers)).status_code == 200
        assert (await call_auth(client, slug=slug, key=key)).status_code == 401


class TestLastUsed:
    async def test_last_used_written_on_first_origin(self, client, sm, fake):
        """last_used_at 首次回源即落库:它是排查「这把钥匙还在被谁用」的唯一线索。"""
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000420")
        key = await issue_key(client, headers, svc["slug"])
        assert (await call_auth(client, slug=svc["slug"], key=key)).status_code == 200
        async with sm() as session:
            row = (await session.execute(select(ServiceApiKey))).scalar_one()
        assert row.last_used_at is not None

    async def test_last_used_throttled_per_key(self, client, sm, fake):
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000421")
        key = await issue_key(client, headers, svc["slug"])
        assert (await call_auth(client, slug=svc["slug"], key=key)).status_code == 200
        async with sm() as session:
            first = (await session.execute(select(ServiceApiKey))).scalar_one().last_used_at
        assert (await call_auth(client, slug=svc["slug"], key=key)).status_code == 200
        async with sm() as session:
            again = (await session.execute(select(ServiceApiKey))).scalar_one().last_used_at
        assert again == first


class TestAuthCache:
    async def test_cache_hit_skips_db(self, client, sm, fake):
        """缓存命中不回源:首次鉴权后直改库吊销(模拟另一副本),窗口内仍放行,TTL 到期后拒。"""
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000422")
        key = await issue_key(client, headers, svc["slug"])
        assert (await call_auth(client, slug=svc["slug"], key=key)).status_code == 200
        async with sm() as session:
            from app.core.timeutil import now_utc

            row = (await session.execute(select(ServiceApiKey))).scalar_one()
            row.revoked_at = now_utc()
            await session.commit()
        assert (await call_auth(client, slug=svc["slug"], key=key)).status_code == 200
        from app.modules.services import service as services_service

        services_service.clear_endpoint_auth_cache()  # 等效 TTL 到期(不睡 5s)
        assert (await call_auth(client, slug=svc["slug"], key=key)).status_code == 401

    async def test_concurrent_same_key_all_pass(self, client, sm, fake):
        import asyncio

        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000423")
        key = await issue_key(client, headers, svc["slug"])
        results = await asyncio.gather(
            *(call_auth(client, slug=svc["slug"], key=key) for _ in range(20))
        )
        assert [r.status_code for r in results] == [200] * 20
        async with sm() as session:
            row = (await session.execute(select(ServiceApiKey))).scalar_one()
        assert row.last_used_at is not None


class TestResponseDiscipline:
    async def test_denial_body_leaks_nothing(self, client, sm, fake):
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000430")
        await issue_key(client, headers, svc["slug"])
        resp = await call_auth(client, slug=svc["slug"], key="sk-wrong")
        assert resp.status_code == 401
        raw = resp.text
        assert "Traceback" not in raw
        assert "svc.cluster.local" not in raw
        assert "/opt/" not in raw and "app/modules" not in raw
        assert "postgres" not in raw.lower()
        assert "set-cookie" not in {k.lower() for k in resp.headers}
        body = resp.json()
        assert set(body) == {"code", "message", "message_key", "params", "detail", "request_id"}
        assert body["detail"] is None
        assert body["code"] == "API_KEY_INVALID"

    async def test_not_audited(self, client, sm, fake):
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000431")
        key = await issue_key(client, headers, svc["slug"])
        async with sm() as session:
            before = (
                await session.execute(select(func.count()).select_from(AuditLog))
            ).scalar_one()
        resp = await client.post(
            AUTH_PATH, headers={"host": host_for(svc["slug"]), "x-api-key": key}
        )
        assert resp.status_code == 200, resp.text
        async with sm() as session:
            after = (await session.execute(select(func.count()).select_from(AuditLog))).scalar_one()
        assert after == before

    async def test_not_in_openapi(self, client):
        schema = (await client.get("/openapi.json")).json()
        assert not [p for p in schema["paths"] if "endpoint-auth" in p]


class TestPathShapes:
    """鉴权回调是**一条精确路由**,不是 catch-all(pathOverride 与 path 互斥)。"""

    async def test_exact_path_accepted(self, client, sm, fake):
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000440")
        key = await issue_key(client, headers, svc["slug"])
        assert (await call_auth(client, slug=svc["slug"], key=key, path="")).status_code == 200

    async def test_suffixes_do_not_authorize(self, client, sm, fake):
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000442")
        key = await issue_key(client, headers, svc["slug"])
        for path in ("/", "/v1/chat/completions", "/健康?a=1"):
            resp = await call_auth(client, slug=svc["slug"], key=key, path=path)
            assert not 200 <= resp.status_code < 300, f"{path}: {resp.status_code} {resp.text}"

    async def test_all_methods_accepted(self, client, sm, fake):
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000441")
        key = await issue_key(client, headers, svc["slug"])
        for method in ("get", "post", "put", "patch", "delete", "head", "options"):
            resp = await getattr(client, method)(
                AUTH_PATH, headers={"host": host_for(svc["slug"]), "x-api-key": key}
            )
            assert resp.status_code == 200, f"{method}: {resp.text}"


class TestApiKeyQuotaRace:
    async def test_concurrent_create_cannot_exceed_quota(self, client, sm, fake, monkeypatch):
        """count-then-insert 必须在服务行锁内:并发建钥不越过上限(否则 20 把形同虚设)。"""
        import asyncio

        from app.core.errors import AppError, ErrorCode
        from app.modules.services import service as services_service

        headers, svc, user_id = await provision_service(client, sm, fake, phone="13900000460")
        monkeypatch.setattr(services_service, "MAX_API_KEYS_PER_SERVICE", 2)
        await issue_key(client, headers, svc["slug"])  # 已有 1 把,余量 1

        async def create() -> None:
            async with sm() as session:
                await services_service.create_api_key(session, user_id, svc["slug"], name="race")

        results = await asyncio.gather(create(), create(), return_exceptions=True)
        oks = [r for r in results if r is None]
        rejected = [
            r for r in results if isinstance(r, AppError) and r.code is ErrorCode.VALIDATION_ERROR
        ]
        assert len(oks) == 1 and len(rejected) == 1
        async with sm() as session:
            live = (
                await session.execute(
                    select(func.count())
                    .select_from(ServiceApiKey)
                    .where(ServiceApiKey.revoked_at.is_(None))
                )
            ).scalar_one()
        assert live == 2


class TestNoAuthRequired:
    async def test_no_platform_jwt_needed(self, client, sm, fake):
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000450")
        key = await issue_key(client, headers, svc["slug"])
        resp = await client.get(
            AUTH_PATH, headers={"host": host_for(svc["slug"]), "x-api-key": key}
        )
        assert resp.status_code == 200, resp.text

    async def test_unknown_user_cannot_enumerate(self, client, sm, fake):
        await new_user(client, sm, "13900000451")
        resp = await client.get(AUTH_PATH, headers={"host": host_for("svc-aaaaaaaaaa")})
        assert resp.status_code == 401
        assert resp.json()["code"] == "API_KEY_INVALID"
