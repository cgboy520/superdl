"""Legal documents: public reads with a locale fallback chain, registration consent records and
the admin version flow (draft → published → archived)."""

from typing import get_args

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.compliance import current_profile
from app.core.errors import conflict, not_found
from app.core.logging import get_logger
from app.core.timeutil import now_utc
from app.modules.legal.models import LegalDocVersion, UserConsent
from app.modules.legal.schemas import (
    LegalDocCellOut,
    LegalDocVersionBrief,
    LegalDocVersionUpdate,
    Locale,
)

logger = get_logger(__name__)

VALID_DOC_KEYS: tuple[str, ...] = ("terms", "privacy", "deletion_notice")
SUPPORTED_LOCALES: tuple[str, ...] = get_args(Locale)
DEFAULT_LOCALE = "en-US"
CONSENT_DOC_KEYS: tuple[str, ...] = ("terms", "privacy")


def preferred_locale() -> str:
    """The compliance profile's default locale; fallbacks and consents start here."""
    locale = current_profile().default_locale
    return locale if locale in SUPPORTED_LOCALES else DEFAULT_LOCALE


def locale_chain(first: str | None = None) -> tuple[str, ...]:
    """Lookup order: the requested locale, the profile default, then every other locale."""
    head = [loc for loc in (first, preferred_locale()) if loc]
    return tuple(dict.fromkeys([*head, *SUPPORTED_LOCALES]))


def _validate_doc_key(doc_key: str) -> None:
    if doc_key not in VALID_DOC_KEYS:
        raise not_found(key="legal.docNotFound")


async def _published(session: AsyncSession, doc_key: str, locale: str) -> LegalDocVersion | None:
    return (
        await session.execute(
            select(LegalDocVersion).where(
                LegalDocVersion.doc_key == doc_key,
                LegalDocVersion.locale == locale,
                LegalDocVersion.status == "published",
            )
        )
    ).scalar_one_or_none()


async def _first_published(
    session: AsyncSession, doc_key: str, chain: tuple[str, ...]
) -> LegalDocVersion | None:
    for locale in chain:
        row = await _published(session, doc_key, locale)
        if row is not None:
            return row
    return None


async def get_public_doc(
    session: AsyncSession, doc_key: str, lang: str | None
) -> tuple[LegalDocVersion, bool]:
    """The published document for `lang` (unsupported or missing → the profile default), walking
    the locale chain; `fallback` is True when the served locale differs from the requested one.
    Unknown doc_key or nothing published → 404."""
    _validate_doc_key(doc_key)
    requested = lang if lang in SUPPORTED_LOCALES else preferred_locale()
    row = await _first_published(session, doc_key, locale_chain(requested))
    if row is None:
        raise not_found(key="legal.docNotFound")
    return row, row.locale != requested


async def record_registration_consents(
    session: AsyncSession, user_id: int, client_ip: str | None
) -> None:
    """Record terms/privacy consents in the registration transaction against the first published
    version along the locale chain; nothing published → skip with a warning."""
    for doc_key in CONSENT_DOC_KEYS:
        row = await _first_published(session, doc_key, locale_chain())
        if row is None:
            logger.warning("consent_published_missing", doc_key=doc_key, user_id=user_id)
            continue
        session.add(
            UserConsent(user_id=user_id, doc_key=doc_key, version=row.version, client_ip=client_ip)
        )


def _brief(row: LegalDocVersion) -> LegalDocVersionBrief:
    return LegalDocVersionBrief(
        id=row.id,
        version=row.version,
        title=row.title,
        status=row.status,
        published_at=row.published_at,
    )


async def admin_overview(session: AsyncSession) -> list[LegalDocCellOut]:
    """doc_key × locale 状态格:每格取当前 published 与最新 draft(均可空=缺失)。"""
    rows = list((await session.execute(select(LegalDocVersion))).scalars())
    published: dict[tuple[str, str], LegalDocVersion] = {}
    drafts: dict[tuple[str, str], LegalDocVersion] = {}
    pairs: set[tuple[str, str]] = set()
    for row in rows:
        key = (row.doc_key, row.locale)
        pairs.add(key)
        if row.status == "published":
            published[key] = row
        elif row.status == "draft" and (key not in drafts or row.version > drafts[key].version):
            drafts[key] = row
    grid = {(k, loc) for k in VALID_DOC_KEYS for loc in SUPPORTED_LOCALES} | pairs
    return [
        LegalDocCellOut(
            doc_key=key[0],
            locale=key[1],
            published=_brief(published[key]) if key in published else None,
            draft=_brief(drafts[key]) if key in drafts else None,
        )
        for key in sorted(grid)
    ]


async def admin_list_versions(
    session: AsyncSession, doc_key: str, locale: Locale
) -> list[LegalDocVersion]:
    _validate_doc_key(doc_key)
    return list(
        (
            await session.execute(
                select(LegalDocVersion)
                .where(
                    LegalDocVersion.doc_key == doc_key,
                    LegalDocVersion.locale == locale,
                )
                .order_by(LegalDocVersion.version.desc())
            )
        ).scalars()
    )


async def admin_create_draft(
    session: AsyncSession, doc_key: str, locale: Locale, *, admin_id: int
) -> LegalDocVersion:
    """Create a draft (version = max+1) when none exists for (doc_key, locale). The body copies the
    first published version along the locale chain; with none, title and body start empty."""
    _validate_doc_key(doc_key)
    existing_draft = (
        await session.execute(
            select(LegalDocVersion.id).where(
                LegalDocVersion.doc_key == doc_key,
                LegalDocVersion.locale == locale,
                LegalDocVersion.status == "draft",
            )
        )
    ).scalar_one_or_none()
    if existing_draft is not None:
        raise conflict(key="legal.draftExists")
    base = await _first_published(session, doc_key, locale_chain(locale))
    max_version = (
        await session.execute(
            select(func.max(LegalDocVersion.version)).where(
                LegalDocVersion.doc_key == doc_key,
                LegalDocVersion.locale == locale,
            )
        )
    ).scalar_one()
    row = LegalDocVersion(
        doc_key=doc_key,
        locale=locale,
        version=(max_version or 0) + 1,
        title=base.title if base else "",
        content_md=base.content_md if base else "",
        status="draft",
        created_by=admin_id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    logger.info("legal_draft_created", doc_key=doc_key, locale=locale, version=row.version)
    return row


async def _get_version(session: AsyncSession, version_id: int) -> LegalDocVersion:
    row = await session.get(LegalDocVersion, version_id)
    if row is None:
        raise not_found(key="legal.docNotFound")
    return row


def _require_draft(row: LegalDocVersion) -> None:
    if row.status != "draft":
        raise conflict(key="legal.versionNotDraft", params={"status": row.status})


async def admin_update_draft(
    session: AsyncSession, version_id: int, body: LegalDocVersionUpdate
) -> LegalDocVersion:
    """仅 draft 可改 title/content_md/effective_note;非 draft 409。"""
    row = await _get_version(session, version_id)
    _require_draft(row)
    if body.title is not None:
        row.title = body.title
    if body.content_md is not None:
        row.content_md = body.content_md
    if body.effective_note is not None:
        row.effective_note = body.effective_note
    await session.commit()
    await session.refresh(row)
    return row


async def admin_publish(
    session: AsyncSession, version_id: int, *, admin_id: int
) -> LegalDocVersion:
    """锁定草稿与旧 published,同事务发布新版本并归档旧版本;唯一冲突回滚后回 409。"""
    row = await session.get(LegalDocVersion, version_id, with_for_update=True)
    if row is None:
        raise not_found(key="legal.docNotFound")
    _require_draft(row)
    old = (
        await session.execute(
            select(LegalDocVersion)
            .where(
                LegalDocVersion.doc_key == row.doc_key,
                LegalDocVersion.locale == row.locale,
                LegalDocVersion.status == "published",
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if old is not None:
        old.status = "archived"
    row.status = "published"
    row.published_by = admin_id
    row.published_at = now_utc()
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise conflict(key="common.retryableConflict") from exc
    await session.refresh(row)
    logger.info("legal_published", doc_key=row.doc_key, locale=row.locale, version=row.version)
    return row


async def admin_archive(session: AsyncSession, version_id: int) -> LegalDocVersion:
    """draft → archived;published 不可直接归档(409),archived 重复操作亦 409。"""
    row = await _get_version(session, version_id)
    if row.status == "published":
        raise conflict(key="legal.publishedNotArchivable")
    _require_draft(row)
    row.status = "archived"
    await session.commit()
    await session.refresh(row)
    return row
