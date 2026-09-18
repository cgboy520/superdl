from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Locale = Literal["en-US", "zh-CN"]


class LegalDocOut(BaseModel):
    """Public endpoint: the current published version. fallback=true means the requested locale
    has no published version and another locale is served."""

    doc_key: str
    locale: str
    title: str
    content_md: str
    version: int
    published_at: datetime | None
    fallback: bool = False


class LegalDocVersionBrief(BaseModel):
    id: int
    version: int
    title: str
    status: str
    published_at: datetime | None = None


class LegalDocCellOut(BaseModel):
    """One cell of the admin overview: latest draft and current published of a (doc_key, locale)
    (both optional = missing)."""

    doc_key: str
    locale: str
    published: LegalDocVersionBrief | None
    draft: LegalDocVersionBrief | None


class LegalDocVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    doc_key: str
    locale: str
    version: int
    title: str
    content_md: str
    status: str
    effective_note: str | None
    created_by: int | None
    created_at: datetime
    published_by: int | None
    published_at: datetime | None


class LegalDocVersionCreate(BaseModel):
    locale: Locale


class LegalDocVersionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    content_md: str | None = Field(default=None, min_length=1)
    effective_note: str | None = Field(default=None, max_length=512)
