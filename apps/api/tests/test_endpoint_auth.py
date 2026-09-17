"""Gateway auth callback /api/internal/v1/endpoint-auth: auth matrix, response discipline, audit
exemption, exact route."""

import pytest
from sqlalchemy import func, select

from app.core.audit import AuditLog
from app.core.config import get_settings
from app.modules.orchestrator.reconciler import reconcile_once
from app.modules.services.models import ServiceApiKey
from tests.helpers import drain, provision_service

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
        """A valid key passes and the two ownership headers are always returned."""
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
        """Revocation takes effect at once (the cache entry is invalidated actively)."""
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
        """User A's key against user B's service → 401."""
        a_headers, a_svc, _ = await provision_service(client, sm, fake, phone="13900000406")
        _b_headers, b_svc, _ = await provision_service(client, sm, fake, phone="13900000407")
        a_key = await issue_key(client, a_headers, a_svc["slug"])
        assert (await call_auth(client, slug=b_svc["slug"], key=a_key)).status_code == 401

    async def test_same_user_other_service_denied(self, client, sm, fake):
        """The same user's key does not work across services."""
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
        assert (await call_auth(client, slug=second.json()["slug"], key=key)).status_code == 401
        assert (await call_auth(client, slug=first["slug"], key=key)).status_code == 200

    async def test_public_endpoint_needs_no_key(self, client, sm, fake):
        """require_api_key=false passes without a key with the ownership headers; a PATCH back
        requires the key at once."""
        headers, svc, _ = await provision_service(
            client, sm, fake, phone="13900000408", require_api_key=False
        )
        slug = svc["slug"]
        resp = await call_auth(client, slug=slug)
        assert resp.status_code == 200, resp.text
        assert resp.headers["x-superdl-endpoint"] == slug
        assert resp.headers["x-superdl-key-id"] == "anonymous"
        patched = await client.patch(
            f"/api/v1/services/{slug}", json={"require_api_key": True}, headers=headers
        )
        assert patched.status_code == 200, patched.text
        assert (await call_auth(client, slug=slug)).status_code == 401

    async def test_unknown_slug_denied(self, client, sm, fake):
        """Unknown service and wrong key share code and copy."""
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
        """A non-running current instance is always refused."""
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
        """Deleting the service revokes every key and stops passing, even while the slug is still in
        the DB."""
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
        """A cache hit does not hit the origin: a revocation written straight to the DB still passes
        within the window and is refused after the TTL."""
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

        services_service.clear_endpoint_auth_cache()
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
    """The auth callback is an exact route, not a catch-all."""

    async def test_suffixes_do_not_authorize(self, client, sm, fake):
        headers, svc, _ = await provision_service(client, sm, fake, phone="13900000442")
        key = await issue_key(client, headers, svc["slug"])
        for path in ("/", "/v1/chat/completions", "/健康?a=1"):  # non-ASCII path  # cjk-ok
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
        """count-then-insert under the service row lock: concurrent key creation does not exceed the
        cap."""
        import asyncio

        from app.core.errors import AppError, ErrorCode
        from app.modules.services import service as services_service

        headers, svc, user_id = await provision_service(client, sm, fake, phone="13900000460")
        monkeypatch.setattr(services_service, "MAX_API_KEYS_PER_SERVICE", 2)
        await issue_key(client, headers, svc["slug"])

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
