from fastapi import APIRouter, Query, Request

from app.core.db import DbSession
from app.core.http import client_ip
from app.core.ratelimit import check_rate_limit
from app.modules.legal import service
from app.modules.legal.schemas import LegalDocOut

router = APIRouter(tags=["legal"])


@router.get("/legal/{doc_key}")
async def get_legal_doc(
    doc_key: str,
    session: DbSession,
    request: Request,
    lang: str | None = Query(default=None),
) -> LegalDocOut:
    """当前 published 版(免鉴权)。en-US 缺失回落 zh-CN 且 fallback=true;
    doc_key 非法或无 published 均 404。"""
    ip = client_ip(request)
    await check_rate_limit(f"legal-doc:{ip or '-'}", max_attempts=120, window_seconds=60.0)
    row, fallback = await service.get_public_doc(session, doc_key, lang)
    return LegalDocOut(
        doc_key=row.doc_key,
        locale=row.locale,
        title=row.title,
        content_md=row.content_md,
        version=row.version,
        published_at=row.published_at,
        fallback=fallback,
    )
