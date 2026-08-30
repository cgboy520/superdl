"""网关鉴权回调 /api/internal/v1/endpoint-auth 的鉴权矩阵与响应纪律。

这条端点是服务型实例的全部访问控制:它放行谁,谁就能打到用户容器。
- 鉴权矩阵挂了 = 平台在替用户漏钥匙(别人的 Key 能开你的门)
- 响应纪律挂了 = 拒绝响应把平台内部信息透给任意第三方(响应体会原样回给调用方)
- 审计豁免挂了 = 按服务 QPS 往 audit_log 灌行,真正要查的写操作被埋掉
- catch-all 路径挂了 = extAuth 把 path 当前缀时整条链路 404 → fail-close 全站不可用
"""

import pytest
from sqlalchemy import func, select

from app.core.audit import AuditLog
from app.core.config import get_settings
from app.modules.orchestrator.models import Instance, ServiceApiKey
from app.modules.orchestrator.reconciler import reconcile_once
from tests.helpers import drain, new_user, provision_service

pytestmark = pytest.mark.usefixtures("fake")

AUTH_PATH = "/api/internal/v1/endpoint-auth"


def host_for(slug: str) -> str:
    return f"{slug}.{get_settings().service_domain_suffix}"


async def endpoint_of(client, headers, uuid: str) -> str:
    resp = await client.get(f"/api/v1/instances/{uuid}/service", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["slug"]


async def issue_key(client, headers, uuid: str, name: str = "k") -> str:
    resp = await client.post(
        f"/api/v1/instances/{uuid}/api-keys", json={"name": name}, headers=headers
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
        """合法 Key 放行,并回归属头。

        两个头必须恒回:headersToBackend 是覆盖语义,少回一个就让客户端伪造的同名头
        原样透传给用户容器。
        """
        headers, uuid, _ = await provision_service(client, sm, fake, phone="13900000401")
        slug = await endpoint_of(client, headers, uuid)
        key = await issue_key(client, headers, uuid)

        resp = await call_auth(client, slug=slug, key=key)
        assert resp.status_code == 200, resp.text
        assert resp.headers["x-superdl-endpoint"] == slug
        assert resp.headers["x-superdl-key-id"].isdigit()

    async def test_bearer_header_also_accepted(self, client, sm, fake):
        """Authorization: Bearer 与 X-Api-Key 两种写法都收(OpenAI 兼容客户端只发前者)。"""
        headers, uuid, _ = await provision_service(client, sm, fake, phone="13900000402")
        slug = await endpoint_of(client, headers, uuid)
        key = await issue_key(client, headers, uuid)
        resp = await call_auth(client, slug=slug, authorization=f"Bearer {key}")
        assert resp.status_code == 200, resp.text

    async def test_wrong_key_denied(self, client, sm, fake):
        headers, uuid, _ = await provision_service(client, sm, fake, phone="13900000403")
        slug = await endpoint_of(client, headers, uuid)
        await issue_key(client, headers, uuid)
        assert (await call_auth(client, slug=slug, key="sk-not-a-real-key")).status_code == 401

    async def test_missing_key_denied(self, client, sm, fake):
        headers, uuid, _ = await provision_service(client, sm, fake, phone="13900000404")
        slug = await endpoint_of(client, headers, uuid)
        await issue_key(client, headers, uuid)
        assert (await call_auth(client, slug=slug)).status_code == 401

    async def test_revoked_key_denied(self, client, sm, fake):
        """吊销即刻生效:缓存条目被主动失效,不靠等 TTL。"""
        headers, uuid, _ = await provision_service(client, sm, fake, phone="13900000405")
        slug = await endpoint_of(client, headers, uuid)
        resp = await client.post(
            f"/api/v1/instances/{uuid}/api-keys", json={"name": "k"}, headers=headers
        )
        key, key_id = resp.json()["key"], resp.json()["id"]
        assert (await call_auth(client, slug=slug, key=key)).status_code == 200
        await client.delete(f"/api/v1/instances/{uuid}/api-keys/{key_id}", headers=headers)
        assert (await call_auth(client, slug=slug, key=key)).status_code == 401

    async def test_cross_tenant_key_denied(self, client, sm, fake):
        """A 用户的 Key 打 B 用户的端点必须 401。

        挂了就是租户隔离破了:Key 是全局唯一高熵串,「查得到」不等于「用得上」。
        """
        a_headers, a_uuid, _ = await provision_service(client, sm, fake, phone="13900000406")
        b_headers, b_uuid, _ = await provision_service(client, sm, fake, phone="13900000407")
        a_key = await issue_key(client, a_headers, a_uuid)
        b_slug = await endpoint_of(client, b_headers, b_uuid)
        assert (await call_auth(client, slug=b_slug, key=a_key)).status_code == 401

    async def test_public_endpoint_needs_no_key(self, client, sm, fake):
        """require_api_key=false 的端点无 Key 也放行,但归属头照回。"""
        headers, uuid, _ = await provision_service(
            client, sm, fake, phone="13900000408", require_api_key=False
        )
        slug = await endpoint_of(client, headers, uuid)
        resp = await call_auth(client, slug=slug)
        assert resp.status_code == 200, resp.text
        assert resp.headers["x-superdl-endpoint"] == slug
        # 匿名也必须显式回 key-id:省略这个头 = 客户端伪造值原样透传
        assert resp.headers["x-superdl-key-id"] == "anonymous"

    async def test_unknown_slug_denied(self, client, sm, fake):
        """端点不存在与密钥不对同码同文案:区分开就是一个枚举平台端点的预言机。"""
        headers, uuid, _ = await provision_service(client, sm, fake, phone="13900000409")
        key = await issue_key(client, headers, uuid)
        resp = await call_auth(client, slug="svc-doesnotex", key=key)
        assert resp.status_code == 401
        assert resp.json()["code"] == "API_KEY_INVALID"

    async def test_foreign_host_denied(self, client, sm, fake):
        """Host 不在服务域名下(例如 Jupyter 域)一律 401,不做「取第一段」的宽松解析。"""
        headers, uuid, _ = await provision_service(client, sm, fake, phone="13900000410")
        key = await issue_key(client, headers, uuid)
        resp = await client.get(
            AUTH_PATH, headers={"host": "svc-abc.evil.example.com", "x-api-key": key}
        )
        assert resp.status_code == 401

    async def test_not_running_instance_denied(self, client, sm, fake):
        """实例非 running 一律拒:Pod 可能还在优雅删除期活着,光删 HTTPRoute 有窗口。"""
        headers, uuid, _ = await provision_service(client, sm, fake, phone="13900000411")
        slug = await endpoint_of(client, headers, uuid)
        key = await issue_key(client, headers, uuid)
        assert (await call_auth(client, slug=slug, key=key)).status_code == 200

        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        async with sm() as session:
            status = (
                await session.execute(select(Instance.status).where(Instance.uuid == uuid))
            ).scalar_one()
        assert status == "stopped"
        assert (await call_auth(client, slug=slug, key=key)).status_code == 401


class TestLastUsed:
    async def test_last_used_written_on_首次回源(self, client, sm, fake):
        """last_used_at 首次回源即落库:它是排查「这把钥匙还在被谁用」的唯一线索。"""
        headers, uuid, _ = await provision_service(client, sm, fake, phone="13900000420")
        slug = await endpoint_of(client, headers, uuid)
        key = await issue_key(client, headers, uuid)
        assert (await call_auth(client, slug=slug, key=key)).status_code == 200
        async with sm() as session:
            row = (await session.execute(select(ServiceApiKey))).scalar_one()
        assert row.last_used_at is not None

    async def test_last_used_throttled_per_key(self, client, sm, fake):
        """节流:60s 窗口内的重复鉴权不再直写 last_used_at(写放大从 rps 降为 key 数/分钟)。"""
        headers, uuid, _ = await provision_service(client, sm, fake, phone="13900000421")
        slug = await endpoint_of(client, headers, uuid)
        key = await issue_key(client, headers, uuid)
        assert (await call_auth(client, slug=slug, key=key)).status_code == 200
        async with sm() as session:
            first = (await session.execute(select(ServiceApiKey))).scalar_one().last_used_at
        # 窗口内再次鉴权(缓存命中):last_used_at 不前进
        assert (await call_auth(client, slug=slug, key=key)).status_code == 200
        async with sm() as session:
            again = (await session.execute(select(ServiceApiKey))).scalar_one().last_used_at
        assert again == first


class TestAuthCache:
    async def test_cache_hit_skips_db(self, client, sm, fake):
        """缓存命中不回源:首次鉴权后删掉钥匙行,窗口内仍放行(TTL 收敛的明示取舍)。

        这是设计内的陈旧窗口(≤5s/进程),不是免死金牌:吊销走 revoke_api_key
        会主动失效本进程条目(见 test_revoked_key_denied),这里绕过它直改库,
        模拟的是「另一副本上的吊销」——跨进程收敛上界就是 TTL。
        """
        headers, uuid, _ = await provision_service(client, sm, fake, phone="13900000422")
        slug = await endpoint_of(client, headers, uuid)
        key = await issue_key(client, headers, uuid)
        assert (await call_auth(client, slug=slug, key=key)).status_code == 200
        # 直改库(绕过 revoke_api_key 的主动失效):窗口内缓存仍放行
        async with sm() as session:
            from app.core.timeutil import now_utc

            row = (await session.execute(select(ServiceApiKey))).scalar_one()
            row.revoked_at = now_utc()
            await session.commit()
        assert (await call_auth(client, slug=slug, key=key)).status_code == 200
        # TTL 到期后回源:拒
        from app.modules.orchestrator import service as orch_service

        orch_service.clear_endpoint_auth_cache()  # 等效 TTL 到期(不睡 5s)
        assert (await call_auth(client, slug=slug, key=key)).status_code == 401

    async def test_concurrent_same_key_all_pass(self, client, sm, fake):
        """并发一致性:同 key 并发 20 次鉴权全部 200,且只落一次 last_used 直写。"""
        import asyncio

        headers, uuid, _ = await provision_service(client, sm, fake, phone="13900000423")
        slug = await endpoint_of(client, headers, uuid)
        key = await issue_key(client, headers, uuid)
        results = await asyncio.gather(*(call_auth(client, slug=slug, key=key) for _ in range(20)))
        assert [r.status_code for r in results] == [200] * 20
        async with sm() as session:
            row = (await session.execute(select(ServiceApiKey))).scalar_one()
        assert row.last_used_at is not None


class TestResponseDiscipline:
    async def test_denial_body_leaks_nothing(self, client, sm, fake):
        """401 响应体会被网关原样回给任意第三方:不能带栈、内网主机名或 Set-Cookie。"""
        headers, uuid, _ = await provision_service(client, sm, fake, phone="13900000430")
        slug = await endpoint_of(client, headers, uuid)
        await issue_key(client, headers, uuid)
        resp = await call_auth(client, slug=slug, key="sk-wrong")
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
        """鉴权回调不进审计表,即使方法是 POST(它跟着客户端的方法走)。"""
        headers, uuid, _ = await provision_service(client, sm, fake, phone="13900000431")
        slug = await endpoint_of(client, headers, uuid)
        key = await issue_key(client, headers, uuid)
        async with sm() as session:
            before = (
                await session.execute(select(func.count()).select_from(AuditLog))
            ).scalar_one()
        resp = await client.post(AUTH_PATH, headers={"host": host_for(slug), "x-api-key": key})
        assert resp.status_code == 200, resp.text
        async with sm() as session:
            after = (await session.execute(select(func.count()).select_from(AuditLog))).scalar_one()
        assert after == before

    async def test_not_in_openapi(self, client):
        """内部端点不进 OpenAPI:进了就会被 orval 生成成前端 fetcher。"""
        schema = (await client.get("/openapi.json")).json()
        assert not [p for p in schema["paths"] if "endpoint-auth" in p]


class TestPathShapes:
    """鉴权回调是**一条精确路由**,不是 catch-all。

    网关侧用 `extAuth.http.pathOverride` 把鉴权请求的 path 恒定改写成
    `/api/internal/v1/endpoint-auth`;同位置的 `path` 字段是前缀语义,两者互斥。

    挂了说明:要么路由退化成 catch-all(把客户端可控的 path 连 query 拼进平台内部 URL),
    要么网关清单从 pathOverride 漂成 path(全部服务端点 fail-close 503)。
    """

    async def test_exact_path_accepted(self, client, sm, fake):
        headers, uuid, _ = await provision_service(client, sm, fake, phone="13900000440")
        slug = await endpoint_of(client, headers, uuid)
        key = await issue_key(client, headers, uuid)
        resp = await call_auth(client, slug=slug, key=key, path="")
        assert resp.status_code == 200, resp.text

    async def test_suffixes_do_not_authorize(self, client, sm, fake):
        """精确路径之外一律不放行(逐路径参数化会把一次完整开机重复三遍)。

        断「非 2xx」而不是钉死 404:带后缀是 404,而单个尾斜杠会先撞上 Starlette 的
        redirect_slashes(307)。两者对 ext_authz 是同一件事 —— 非 2xx 即拒绝,fail-close。
        """
        headers, uuid, _ = await provision_service(client, sm, fake, phone="13900000442")
        slug = await endpoint_of(client, headers, uuid)
        key = await issue_key(client, headers, uuid)
        for path in ("/", "/v1/chat/completions", "/健康?a=1"):
            resp = await call_auth(client, slug=slug, key=key, path=path)
            assert not 200 <= resp.status_code < 300, f"{path}: {resp.status_code} {resp.text}"

    async def test_all_methods_accepted(self, client, sm, fake):
        """ext_authz 用客户端原始方法回调:漏一个方法就是那一类请求全被 fail-close 拦死。"""
        headers, uuid, _ = await provision_service(client, sm, fake, phone="13900000441")
        slug = await endpoint_of(client, headers, uuid)
        key = await issue_key(client, headers, uuid)
        for method in ("get", "post", "put", "patch", "delete", "head", "options"):
            resp = await getattr(client, method)(
                AUTH_PATH, headers={"host": host_for(slug), "x-api-key": key}
            )
            assert resp.status_code == 200, f"{method}: {resp.text}"


class TestApiKeyQuotaRace:
    async def test_concurrent_create_cannot_exceed_quota(self, client, sm, fake, monkeypatch):
        """count-then-insert 必须在实例行锁内:并发建钥不越过上限(否则 20 把形同虚设)。"""
        import asyncio

        from app.core.errors import AppError, ErrorCode
        from app.modules.orchestrator import service as orch_service

        headers, uuid, user_id = await provision_service(client, sm, fake, phone="13900000460")
        monkeypatch.setattr(orch_service, "MAX_API_KEYS_PER_INSTANCE", 2)
        await issue_key(client, headers, uuid)  # 已有 1 把,余量 1

        async def create() -> None:
            async with sm() as session:
                await orch_service.create_api_key(session, user_id, uuid, name="race")

        results = await asyncio.gather(create(), create(), return_exceptions=True)
        oks = [r for r in results if r is None]
        rejected = [
            r for r in results if isinstance(r, AppError) and r.code is ErrorCode.VALIDATION_ERROR
        ]
        assert len(oks) == 1
        assert len(rejected) == 1
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
        """回调不带平台 JWT:带了就是鸡生蛋。保护只有边缘收口(prod 下 XFF → 404)。"""
        headers, uuid, _ = await provision_service(client, sm, fake, phone="13900000450")
        slug = await endpoint_of(client, headers, uuid)
        key = await issue_key(client, headers, uuid)
        # 无 Authorization、无 Cookie,纯 Host + X-Api-Key
        resp = await client.get(AUTH_PATH, headers={"host": host_for(slug), "x-api-key": key})
        assert resp.status_code == 200, resp.text

    async def test_unknown_user_cannot_enumerate(self, client, sm, fake):
        """未注册的调用方拿不到任何区分信息(全 401 同码)。"""
        await new_user(client, sm, "13900000451")
        resp = await client.get(AUTH_PATH, headers={"host": host_for("svc-aaaaaaaaaa")})
        assert resp.status_code == 401
        assert resp.json()["code"] == "API_KEY_INVALID"
