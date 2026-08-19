"""测试基建:testcontainers 起真 PG18,create_all 建表,函数级 TRUNCATE 隔离。

迁移与模型的一致性由 CI 的 `alembic upgrade head && alembic check` 把关,
单测走 create_all 换速度。
"""

import os
from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from testcontainers.community.postgres import PostgresContainer


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    with PostgresContainer("postgres:18", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        os.environ["SUPERDL_DATABASE_URL"] = url
        os.environ["SUPERDL_ENVIRONMENT"] = "test"
        # 环境变量就位后再清缓存,让所有 get_settings() 读到测试库
        from app.core.config import get_settings

        get_settings.cache_clear()
        yield url
        get_settings.cache_clear()


@pytest.fixture(scope="session")
async def engine(pg_url: str) -> AsyncIterator[AsyncEngine]:
    from app.core.db import dispose_engine, get_engine
    from app.models_registry import Base

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await dispose_engine()


@pytest.fixture
async def sm(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """函数级 sessionmaker;测试结束 TRUNCATE 全部表保证隔离。"""
    from app.core.db import get_sessionmaker
    from app.models_registry import Base

    yield get_sessionmaker()

    async with engine.begin() as conn:
        tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        if tables:
            await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def db(sm: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with sm() as session:
        yield session


@pytest.fixture(autouse=True)
def _reset_ratelimit() -> Iterator[None]:
    from app.core import ratelimit

    ratelimit.reset()
    yield
    ratelimit.reset()


@pytest.fixture
async def client(sm: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncClient]:
    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
