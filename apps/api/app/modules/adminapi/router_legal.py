"""Admin routes (legal document version management)."""

import hashlib

from fastapi import APIRouter, Query, Request, status

from app.core.audit import set_audit_target
from app.core.db import DbSession
from app.modules.adminapi.deps import require_roles
from app.modules.adminapi.models import AdminUser
from app.modules.adminapi.schemas import ReasonBody
from app.modules.legal import service as legal_service
from app.modules.legal.schemas import (
    LegalDocCellOut,
    LegalDocVersionCreate,
    LegalDocVersionOut,
    LegalDocVersionUpdate,
    Locale,
)

router = APIRouter(tags=["admin"])


class LegalDocVersionArchive(ReasonBody):
    """Request body for archiving a draft: reason required."""


@router.get("/legal-docs", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_legal_docs(session: DbSession) -> list[LegalDocCellOut]:
    """Legal document overview: doc_key × locale status grid (current published + latest draft)."""
    return await legal_service.admin_overview(session)


@router.get(
    "/legal-docs/{doc_key}/versions", dependencies=[require_roles("ops", "finance", "readonly")]
)
async def admin_list_legal_doc_versions(
    doc_key: str, session: DbSession, locale: Locale = Query(...)
) -> list[LegalDocVersionOut]:
    """Version history of a (doc_key, locale) (version descending)."""
    rows = await legal_service.admin_list_versions(session, doc_key, locale)
    return [LegalDocVersionOut.model_validate(r) for r in rows]


@router.post("/legal-docs/{doc_key}/versions", status_code=status.HTTP_201_CREATED)
async def admin_create_legal_doc_version(
    doc_key: str,
    body: LegalDocVersionCreate,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles(),
) -> LegalDocVersionOut:
    """Copy the current published version into a new draft (version=max+1); without a published row
    in that locale the fallback chain provides the base."""
    row = await legal_service.admin_create_draft(session, doc_key, body.locale, admin_id=admin.id)
    set_audit_target(
        request,
        f"legal:{row.doc_key}:{row.locale}",
        detail={"action": "create_draft", "version": row.version},
    )
    return LegalDocVersionOut.model_validate(row)


@router.put("/legal-docs/versions/{version_id}", dependencies=[require_roles()])
async def admin_update_legal_doc_version(
    version_id: int,
    body: LegalDocVersionUpdate,
    session: DbSession,
    request: Request,
) -> LegalDocVersionOut:
    """Edit a draft (only drafts may change title/content_md/effective_note; non-draft 409)."""
    row = await legal_service.admin_update_draft(session, version_id, body)
    set_audit_target(
        request,
        f"legal:{row.doc_key}:{row.locale}",
        detail={
            "action": "edit",
            "version": row.version,
            "sha256": hashlib.sha256(row.content_md.encode()).hexdigest(),
        },
    )
    return LegalDocVersionOut.model_validate(row)


@router.post("/legal-docs/versions/{version_id}/publish")
async def admin_publish_legal_doc_version(
    version_id: int,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles(),
) -> LegalDocVersionOut:
    """Publish: archives the old published row of the same (doc_key, locale) in one transaction; the
    audit detail records version + sha256."""
    row = await legal_service.admin_publish(session, version_id, admin_id=admin.id)
    set_audit_target(
        request,
        f"legal:{row.doc_key}:{row.locale}",
        detail={
            "version": row.version,
            "sha256": hashlib.sha256(row.content_md.encode()).hexdigest(),
        },
    )
    return LegalDocVersionOut.model_validate(row)


@router.post("/legal-docs/versions/{version_id}/archive", dependencies=[require_roles()])
async def admin_archive_legal_doc_version(
    version_id: int,
    body: LegalDocVersionArchive,
    session: DbSession,
    request: Request,
) -> LegalDocVersionOut:
    """Archive a draft (draft → archived, reason required); published cannot be archived directly
    (409)."""
    row = await legal_service.admin_archive(session, version_id)
    set_audit_target(
        request,
        f"legal:{row.doc_key}:{row.locale}",
        detail={"action": "archive", "version": row.version, "reason": body.reason},
    )
    return LegalDocVersionOut.model_validate(row)
