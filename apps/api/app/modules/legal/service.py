"""法务文档:公开读取(回落 zh-CN)+ 注册同意存证 + 管理端版本流。

版本流:新建 draft(基于当前 published 复制,version=max+1;同语言无 published 时以
zh-CN published 为翻译底稿)→ 仅 draft 可编辑/发布/归档 → 发布事务把同 (doc_key, locale)
旧 published 转 archived(部分唯一索引兜底)。
"""

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode, not_found
from app.core.logging import get_logger
from app.core.timeutil import now_utc
from app.modules.legal.models import LegalDocVersion, UserConsent
from app.modules.legal.preset import PRESET_DOCS
from app.modules.legal.schemas import (
    LegalDocCellOut,
    LegalDocVersionBrief,
    LegalDocVersionUpdate,
)

logger = get_logger(__name__)

VALID_DOC_KEYS: tuple[str, ...] = tuple(doc_key for doc_key, _, _ in PRESET_DOCS)
SUPPORTED_LOCALES: tuple[str, ...] = ("zh-CN", "en-US")
DEFAULT_LOCALE = "zh-CN"
# 注册必勾落证的两份文档
CONSENT_DOC_KEYS: tuple[str, ...] = ("terms", "privacy")


def _validate_doc_key(doc_key: str) -> None:
    if doc_key not in VALID_DOC_KEYS:
        raise not_found(key="legal.docNotFound")


def _validate_locale(locale: str) -> None:
    if locale not in SUPPORTED_LOCALES:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="legal.localeUnsupported",
            params={"locale": locale},
        )


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


async def get_public_doc(
    session: AsyncSession, doc_key: str, lang: str | None
) -> tuple[LegalDocVersion, bool]:
    """当前 published 版 + 是否回落 zh-CN。doc_key 非法/无 published 均 404。"""
    _validate_doc_key(doc_key)
    locale = lang if lang in SUPPORTED_LOCALES else DEFAULT_LOCALE
    row = await _published(session, doc_key, locale)
    if row is not None:
        return row, False
    if locale != DEFAULT_LOCALE:
        row = await _published(session, doc_key, DEFAULT_LOCALE)
        if row is not None:
            return row, True
    raise not_found(key="legal.docNotFound")


async def record_registration_consents(
    session: AsyncSession, user_id: int, client_ip: str | None
) -> None:
    """注册成功同事务落 terms/privacy 同意存证(版本=当前 zh-CN published)。

    无 published 时跳过并告警(迁移预置保证生产必有;不因此阻断注册)。
    """
    for doc_key in CONSENT_DOC_KEYS:
        row = await _published(session, doc_key, DEFAULT_LOCALE)
        if row is None:
            logger.warning("consent_published_missing", doc_key=doc_key, user_id=user_id)
            continue
        session.add(
            UserConsent(user_id=user_id, doc_key=doc_key, version=row.version, client_ip=client_ip)
        )


async def seed_preset_docs(session: AsyncSession) -> None:
    """预置 published v1(terms/privacy/deletion_notice,zh-CN)。已有任意版本即跳过。"""
    for doc_key, title, content_md in PRESET_DOCS:
        exists = (
            await session.execute(
                select(LegalDocVersion.id)
                .where(
                    LegalDocVersion.doc_key == doc_key,
                    LegalDocVersion.locale == DEFAULT_LOCALE,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if exists is not None:
            continue
        session.add(
            LegalDocVersion(
                doc_key=doc_key,
                locale=DEFAULT_LOCALE,
                version=1,
                title=title,
                content_md=content_md,
                status="published",
                effective_note="系统预置",
                published_at=now_utc(),
            )
        )
    await session.flush()


# ---------- 管理端 ----------


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
            doc_key=doc_key,
            locale=locale,
            published=_brief(published[key]) if key in published else None,
            draft=_brief(drafts[key]) if key in drafts else None,
        )
        for key in sorted(grid)
        for doc_key, locale in [key]
    ]


async def admin_list_versions(
    session: AsyncSession, doc_key: str, locale: str
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
    session: AsyncSession, doc_key: str, locale: str, *, admin_id: int
) -> LegalDocVersion:
    """基于当前 published 复制出新 draft(version=max+1);同语言无 published 时以
    zh-CN published 为底稿(翻译起点)。每 (doc_key, locale) 同时仅允许一个 draft。"""
    _validate_doc_key(doc_key)
    _validate_locale(locale)
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
        raise AppError(
            ErrorCode.CONFLICT,
            key="legal.draftExists",
            http_status=status.HTTP_409_CONFLICT,
        )
    base = await _published(session, doc_key, locale)
    if base is None and locale != DEFAULT_LOCALE:
        base = await _published(session, doc_key, DEFAULT_LOCALE)
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
        raise AppError(
            ErrorCode.CONFLICT,
            key="legal.versionNotDraft",
            params={"status": row.status},
            http_status=status.HTTP_409_CONFLICT,
        )


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
    """发布:同事务把同 (doc_key, locale) 旧 published 转 archived;部分唯一索引兜底并发。"""
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
        # 并发发布同 (doc_key, locale):部分唯一索引兜底,后手按冲突处理而非 500
        await session.rollback()
        raise AppError(
            ErrorCode.CONFLICT,
            key="common.retryableConflict",
            http_status=status.HTTP_409_CONFLICT,
        ) from exc
    await session.refresh(row)
    logger.info("legal_published", doc_key=row.doc_key, locale=row.locale, version=row.version)
    return row


async def admin_archive(session: AsyncSession, version_id: int) -> LegalDocVersion:
    """draft → archived;published 不可直接归档(409),archived 重复操作亦 409。"""
    row = await _get_version(session, version_id)
    if row.status == "published":
        raise AppError(
            ErrorCode.CONFLICT,
            key="legal.publishedNotArchivable",
            http_status=status.HTTP_409_CONFLICT,
        )
    _require_draft(row)
    row.status = "archived"
    await session.commit()
    await session.refresh(row)
    return row
