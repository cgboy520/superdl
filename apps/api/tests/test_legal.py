"""Legal documents: public endpoint (profile default, fallback chain, 404), registration consents,
version flow (draft → edit → publish → archive) including the seeded en-US drafts."""

import hashlib

from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditLog
from app.core.config import get_settings
from app.modules.legal.models import LegalDocVersion, UserConsent
from tests.helpers import admin_headers, register

NEW_CONTENT = "# 标题\n\n第一行\n第二行(改)\n第三行\n"


async def _create_draft(
    client: AsyncClient, headers: dict[str, str], doc_key: str = "terms", locale: str = "zh-CN"
) -> dict:
    resp = await client.post(
        f"/api/admin/v1/legal-docs/{doc_key}/versions", json={"locale": locale}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestPublicEndpoint:
    async def test_published_returned(self, client: AsyncClient):
        resp = await client.get("/api/v1/legal/terms", params={"lang": "zh-CN"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["doc_key"] == "terms"
        assert body["locale"] == "zh-CN"
        assert body["version"] == 1
        assert body["fallback"] is False
        assert body["title"]
        assert "服务说明" in body["content_md"]
        assert body["published_at"]

    async def test_en_fallback_to_zh(self, client: AsyncClient):
        """The en-US seed is a draft, so en-US (explicit or implied by the `none` profile) gets the
        zh-CN publication with fallback=true."""
        resp = await client.get("/api/v1/legal/privacy", params={"lang": "en-US"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["locale"] == "zh-CN"
        assert body["fallback"] is True
        implicit = (await client.get("/api/v1/legal/privacy")).json()
        assert implicit["locale"] == "zh-CN" and implicit["fallback"] is True

    async def test_profile_default_locale(self, client: AsyncClient, monkeypatch):
        """compliance_profile=cn defaults to zh-CN: no lang → zh-CN with fallback=false."""
        monkeypatch.setattr(get_settings(), "compliance_profile", "cn")
        body = (await client.get("/api/v1/legal/terms")).json()
        assert body["locale"] == "zh-CN" and body["fallback"] is False

    async def test_no_published_404(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        async with sm() as session:
            await session.execute(delete(LegalDocVersion).where(LegalDocVersion.doc_key == "terms"))
            await session.commit()
        resp = await client.get("/api/v1/legal/terms")
        assert resp.status_code == 404
        assert resp.json()["code"] == "NOT_FOUND"

    async def test_invalid_doc_key_404(self, client: AsyncClient):
        resp = await client.get("/api/v1/legal/not-a-doc")
        assert resp.status_code == 404


class TestRegistrationConsents:
    async def test_consents_recorded(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        data = await register(client, "13800000031")
        user_id = data["user"]["id"]
        async with sm() as session:
            rows = list(
                (
                    await session.execute(select(UserConsent).where(UserConsent.user_id == user_id))
                ).scalars()
            )
        assert {r.doc_key for r in rows} == {"terms", "privacy"}
        for row in rows:
            assert row.version == 1
            assert row.client_ip
            assert row.accepted_at is not None


class TestVersionFlow:
    async def test_draft_edit_publish_cycle(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        headers = await admin_headers(sm, client)

        draft = await _create_draft(client, headers)
        assert draft["version"] == 2
        assert draft["status"] == "draft"
        assert draft["content_md"]
        dup = await client.post(
            "/api/admin/v1/legal-docs/terms/versions", json={"locale": "zh-CN"}, headers=headers
        )
        assert dup.status_code == 409
        assert dup.json()["code"] == "CONFLICT"

        resp = await client.put(
            f"/api/admin/v1/legal-docs/versions/{draft['id']}",
            json={"title": "SuperDL 用户协议(修订)", "content_md": NEW_CONTENT},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["content_md"] == NEW_CONTENT

        async with sm() as session:
            published_v1 = (
                await session.execute(
                    select(LegalDocVersion).where(
                        LegalDocVersion.doc_key == "terms",
                        LegalDocVersion.locale == "zh-CN",
                        LegalDocVersion.status == "published",
                    )
                )
            ).scalar_one()
        resp = await client.put(
            f"/api/admin/v1/legal-docs/versions/{published_v1.id}",
            json={"title": "x"},
            headers=headers,
        )
        assert resp.status_code == 409

        resp = await client.post(
            f"/api/admin/v1/legal-docs/versions/{draft['id']}/publish", headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "published"
        async with sm() as session:
            rows = list(
                (
                    await session.execute(
                        select(LegalDocVersion).where(
                            LegalDocVersion.doc_key == "terms",
                            LegalDocVersion.locale == "zh-CN",
                        )
                    )
                ).scalars()
            )
            by_status = {(r.version, r.status) for r in rows}
            assert (1, "archived") in by_status
            assert (2, "published") in by_status
            assert sum(1 for r in rows if r.status == "published") == 1
            audit = (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.target == "legal:terms:zh-CN")
                    .order_by(AuditLog.id.desc())
                    .limit(1)
                )
            ).scalar_one()
        assert audit.detail is not None
        assert audit.detail["version"] == 2
        assert audit.detail["sha256"] == hashlib.sha256(NEW_CONTENT.encode()).hexdigest()

        resp = await client.get("/api/v1/legal/terms")
        assert resp.json()["version"] == 2
        assert resp.json()["content_md"] == NEW_CONTENT

        draft3 = await _create_draft(client, headers)
        assert draft3["version"] == 3
        resp = await client.put(
            f"/api/admin/v1/legal-docs/versions/{draft3['id']}",
            json={"content_md": "# 第三版\n"},
            headers=headers,
        )
        assert resp.status_code == 200
        resp = await client.post(
            f"/api/admin/v1/legal-docs/versions/{draft3['id']}/publish", headers=headers
        )
        assert resp.status_code == 200
        resp = await client.get("/api/v1/legal/terms")
        assert resp.json()["version"] == 3
        async with sm() as session:
            v2 = await session.get(LegalDocVersion, draft["id"])
            assert v2 is not None and v2.status == "archived"

    async def test_archive_rules(self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]):
        headers = await admin_headers(sm, client)
        draft = await _create_draft(client, headers)
        async with sm() as session:
            published = (
                (
                    await session.execute(
                        select(LegalDocVersion).where(LegalDocVersion.status == "published")
                    )
                )
                .scalars()
                .first()
            )
        assert published is not None
        resp = await client.post(
            f"/api/admin/v1/legal-docs/versions/{published.id}/archive",
            headers=headers,
            json={"reason": "清理废弃草稿"},
        )
        assert resp.status_code == 409
        resp = await client.post(
            f"/api/admin/v1/legal-docs/versions/{draft['id']}/archive", headers=headers
        )
        assert resp.status_code == 422
        resp = await client.post(
            f"/api/admin/v1/legal-docs/versions/{draft['id']}/archive",
            headers=headers,
            json={"reason": "内容已合并到 v3"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "archived"
        resp = await client.post(
            f"/api/admin/v1/legal-docs/versions/{draft['id']}/archive",
            headers=headers,
            json={"reason": "重复操作"},
        )
        assert resp.status_code == 409

    async def test_overview_and_history(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        headers = await admin_headers(sm, client)
        resp = await client.get("/api/admin/v1/legal-docs", headers=headers)
        assert resp.status_code == 200, resp.text
        cells = {(c["doc_key"], c["locale"]): c for c in resp.json()}
        terms_zh = cells[("terms", "zh-CN")]
        assert terms_zh["published"]["version"] == 1
        assert terms_zh["draft"] is None
        terms_en = cells[("terms", "en-US")]
        assert terms_en["published"] is None
        assert terms_en["draft"]["version"] == 1

        resp = await client.get(
            "/api/admin/v1/legal-docs/terms/versions",
            params={"locale": "zh-CN"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert [v["version"] for v in resp.json()] == [1]

        dup = await client.post(
            "/api/admin/v1/legal-docs/terms/versions", json={"locale": "en-US"}, headers=headers
        )
        assert dup.status_code == 409

    async def test_seeded_en_draft_publishes_and_serves_by_default(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """Publishing the seeded en-US template makes it the document served to the `none` profile
        without a fallback; a later en-US draft copies the en-US text, not the zh-CN one."""
        headers = await admin_headers(sm, client)
        resp = await client.get(
            "/api/admin/v1/legal-docs/terms/versions", params={"locale": "en-US"}, headers=headers
        )
        (draft,) = resp.json()
        assert draft["status"] == "draft" and draft["version"] == 1
        resp = await client.post(
            f"/api/admin/v1/legal-docs/versions/{draft['id']}/publish", headers=headers
        )
        assert resp.status_code == 200, resp.text
        body = (await client.get("/api/v1/legal/terms")).json()
        assert body["locale"] == "en-US" and body["fallback"] is False
        assert body["title"] == "Terms of Service"
        next_draft = await _create_draft(client, headers, locale="en-US")
        assert next_draft["version"] == 2
        assert "Draft template seeded by the platform" in next_draft["content_md"]
