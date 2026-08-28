"""管理端路由(法务文档版本管理)。"""

import hashlib

from fastapi import APIRouter, Query, Request, status

from app.core.audit import set_audit_target
from app.core.db import DbSession
from app.modules.adminapi.deps import require_roles
from app.modules.adminapi.models import AdminUser
from app.modules.legal.schemas import (
    LegalDocCellOut,
    LegalDocVersionCreate,
    LegalDocVersionOut,
    LegalDocVersionUpdate,
)

router = APIRouter(tags=["admin"])


# ---------- 法务文档(读全角色,写仅 admin) ----------


@router.get("/legal-docs", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_legal_docs(session: DbSession) -> list[LegalDocCellOut]:
    """法务文档总览:doc_key × locale 状态格(当前 published + 最新 draft;均空 = 缺失)。"""
    from app.modules.legal import service as legal_service

    return await legal_service.admin_overview(session)


@router.get(
    "/legal-docs/{doc_key}/versions", dependencies=[require_roles("ops", "finance", "readonly")]
)
async def admin_list_legal_doc_versions(
    doc_key: str, session: DbSession, locale: str = Query(...)
) -> list[LegalDocVersionOut]:
    """某 (doc_key, locale) 的版本历史(version 倒序)。"""
    from app.modules.legal import service as legal_service

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
    """基于当前 published 复制出新 draft(version=max+1);同语言无 published 以 zh-CN 为底稿。"""
    from app.modules.legal import service as legal_service

    row = await legal_service.admin_create_draft(session, doc_key, body.locale, admin_id=admin.id)
    set_audit_target(
        request,
        f"legal:{row.doc_key}:{row.locale}",
        detail={"action": "create_draft", "version": row.version},
    )
    return LegalDocVersionOut.model_validate(row)


@router.put("/legal-docs/versions/{version_id}")
async def admin_update_legal_doc_version(
    version_id: int,
    body: LegalDocVersionUpdate,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles(),
) -> LegalDocVersionOut:
    """编辑草稿(仅 draft 可改 title/content_md/effective_note;非 draft 409)。"""
    from app.modules.legal import service as legal_service

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
    """发布:同事务把同 (doc_key, locale) 旧 published 转 archived;审计 detail 记版本 + sha256。"""
    from app.modules.legal import service as legal_service

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


@router.post("/legal-docs/versions/{version_id}/archive")
async def admin_archive_legal_doc_version(
    version_id: int,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles(),
) -> LegalDocVersionOut:
    """归档草稿(draft → archived);published 不可直接归档(409)。"""
    from app.modules.legal import service as legal_service

    row = await legal_service.admin_archive(session, version_id)
    set_audit_target(
        request,
        f"legal:{row.doc_key}:{row.locale}",
        detail={"action": "archive", "version": row.version},
    )
    return LegalDocVersionOut.model_validate(row)
