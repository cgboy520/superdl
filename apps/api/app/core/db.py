import ssl
from collections.abc import AsyncIterator
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, ClassVar
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from alembic.script import ScriptDirectory
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


def _split_db_tls(url: str) -> tuple[str, dict[str, Any]]:
    """Strip sslmode / sslrootcert from the URL and translate them into asyncpg ssl connect args;
    returns (clean url, connect_args).
    With sslrootcert an SSLContext is built: verify-full checks the hostname, verify-ca / require
    check the chain only; a missing CA file raises."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    sslmode = query.pop("sslmode", [None])[0]
    sslrootcert = query.pop("sslrootcert", [None])[0]
    if sslmode is None:
        if sslrootcert is not None:
            raise ValueError("database_url has sslrootcert but no sslmode")
        return url, {}
    clean = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
    if sslrootcert is None:
        return clean, {"ssl": sslmode}
    if sslmode not in ("require", "verify-ca", "verify-full"):
        raise ValueError(
            f"sslrootcert needs sslmode require / verify-ca / verify-full, got sslmode={sslmode}"
        )
    ctx = ssl.create_default_context(cafile=sslrootcert)
    ctx.check_hostname = sslmode == "verify-full"
    ctx.verify_mode = ssl.CERT_REQUIRED
    return clean, {"ssl": ctx}


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
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout_seconds,
            pool_pre_ping=True,
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
    """Request-scoped session; the caller commits, uncommitted work is rolled back and the session
    closed on exit."""
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
    """Alembic script directory (resolved once per process)."""
    api_root = Path(__file__).resolve().parents[2]
    return ScriptDirectory(str(api_root / "alembic"))


def code_schema_head() -> str:
    """The single Alembic head; RuntimeError with zero or several heads."""
    heads = _script_directory().get_heads()
    if len(heads) != 1:
        raise RuntimeError(
            f"alembic has several heads: {heads} - keep the history linear, merge to one head first"
        )
    return heads[0]


def schema_state(db_revisions: list[str]) -> str:
    """Schema state: multi_head when the code has no single head, never_migrated when the DB has no
    version.

    ready when the DB version list is exactly the code's single head, otherwise schema_mismatch.
    """
    try:
        head = code_schema_head()
    except RuntimeError:
        return "multi_head"
    if not db_revisions:
        return "never_migrated"
    if db_revisions == [head]:
        return "ready"
    return "schema_mismatch"
