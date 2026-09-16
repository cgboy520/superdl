from fastapi import APIRouter, Query

from app.core.db import DbSession
from app.modules.legal import service
from app.modules.legal.schemas import LegalDocOut

router = APIRouter(tags=["legal"])


@router.get("/legal/{doc_key}")
async def get_legal_doc(
    doc_key: str,
    session: DbSession,
    lang: str | None = Query(default=None),
) -> LegalDocOut:
    """Current published version (no auth). Missing locales fall back along the chain (requested →
    profile default → others) with fallback=true; unknown doc_key or nothing published → 404."""
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
