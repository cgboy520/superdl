from collections.abc import AsyncIterator
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, ClassVar

from fastapi import Depends
from sqlalchemy import TIMESTAMP, MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

if TYPE_CHECKING:
    from alembic.script import ScriptDirectory


def _split_db_tls(url: str) -> tuple[str, dict[str, str]]:
    """摘下 URL 里的 sslmode 翻译成 asyncpg ssl 连接参;返回 (干净 url, connect_args 增补)。"""
    from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    sslmode = query.pop("sslmode", [None])[0]
    if sslmode is None:
        return url, {}
    clean = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
    return clean, {"ssl": sslmode}


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[type, Any]] = {datetime: TIMESTAMP(timezone=True)}


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        url, tls_args = _split_db_tls(settings.database_url)
        _engine = create_async_engine(
            url,
            pool_size=settings.db_pool_size,
            pool_pre_ping=True,
            # 三个 timeout 都要有;迁移 Job 另有更严的 PGOPTIONS
            connect_args={
                **tls_args,
                "server_settings": {
                    "statement_timeout": "30000",
                    "lock_timeout": "5000",
                    "idle_in_transaction_session_timeout": "60000",
                },
            },
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


async def get_db() -> AsyncIterator[AsyncSession]:
    """请求级 session;调用方自行 commit,异常自动 rollback。"""
    async with get_sessionmaker()() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db)]


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


@cache
def _script_directory() -> "ScriptDirectory":
    """alembic 脚本目录(进程内解析一次)。"""
    from alembic.script import ScriptDirectory

    api_root = Path(__file__).resolve().parents[2]  # app/core/db.py → apps/api
    return ScriptDirectory(str(api_root / "alembic"))


def code_schema_head() -> str:
    """代码侧 schema head(alembic/versions);多 head 视为仓库事故。"""
    heads = _script_directory().get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"alembic 多 head:{heads}——须保持线性历史,先 merge 出单 head")
    return heads[0]


def schema_state(db_revisions: list[str]) -> str:
    """比对 DB alembic_version 与代码 head:单行且相等 → ready;否则 never_migrated / multi_head /
    schema_mismatch → 503。"""
    try:
        head = code_schema_head()
    except RuntimeError:
        return "multi_head"
    if not db_revisions:
        return "never_migrated"
    if db_revisions == [head]:
        return "ready"
    return "schema_mismatch"
