from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LegalDocOut(BaseModel):
    """公开端点:当前 published 版。fallback=true 表示请求语言缺失、回落 zh-CN。"""

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
    """管理端总览一格:某 (doc_key, locale) 的最新 draft 与当前 published(均可空=缺失)。"""

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
    locale: str = Field(min_length=2, max_length=16)


class LegalDocVersionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    content_md: str | None = Field(default=None, min_length=1)
    effective_note: str | None = Field(default=None, max_length=512)
