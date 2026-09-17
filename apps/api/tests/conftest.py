"""Shared PostgreSQL 18 and ASGI app with database isolation."""

import os
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

if TYPE_CHECKING:
    from app.core.k8s.fake import FakeOrchestrator

os.environ["SUPERDL_ENVIRONMENT"] = "test"
os.environ["SUPERDL_K8S_BACKEND"] = "fake"
os.environ["SUPERDL_CREATING_TIMEOUT_SECONDS"] = "300"
os.environ["SUPERDL_JWT_SECRET"] = "test-jwt-secret-32-bytes-minimum!!"
os.environ["SUPERDL_BCRYPT_ROUNDS"] = "4"
os.environ["SUPERDL_ALERTMANAGER_TOKEN"] = "test-alertmanager-token"

_PG_TEST_CMD = "postgres -c fsync=off -c synchronous_commit=off -c full_page_writes=off"


@pytest.fixture(scope="session", autouse=True)
def _logging_pipeline() -> None:
    from app.core.logging import setup_logging

    setup_logging()


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    container = PostgresContainer("postgres:18", driver="asyncpg").with_command(_PG_TEST_CMD)
    with container as pg:
        host = pg.get_container_host_ip()
        url = pg.get_connection_url(host="127.0.0.1" if host == "localhost" else host)
        os.environ["SUPERDL_DATABASE_URL"] = url
        from app.core.config import get_settings

        get_settings.cache_clear()
        yield url
        get_settings.cache_clear()
    pg.get_docker_client().client.close()


@pytest.fixture(scope="session")
async def engine(pg_url: str) -> AsyncIterator[AsyncEngine]:
    from app.core.db import code_schema_head, dispose_engine, get_engine
    from app.models_registry import Base

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(_audit_log_prune_ddl()))
        await conn.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        await conn.execute(text("DELETE FROM alembic_version"))
        await conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
            {"v": code_schema_head()},
        )
    yield engine
    await dispose_engine()


def _audit_log_prune_ddl() -> str:
    """create_all runs no migrations; the audit cleanup function DDL is taken straight from its
    migration file, the same as production."""
    import importlib.util
    from pathlib import Path

    path = next(Path(__file__).parent.parent.glob("alembic/versions/*_audit_log_prune_fn.py"))
    spec = importlib.util.spec_from_file_location("audit_log_prune_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module._FN)


async def _seed_baseline(smaker: async_sessionmaker[AsyncSession]) -> None:
    """Write the cluster capability cache and the preset legal documents."""
    from app.core.k8s.base import (
        DATA_DISK_STORAGE_CLASS,
        INSTANCE_DISK_STORAGE_CLASS,
        ClusterProbe,
    )
    from app.modules.nodes.service import save_cluster_probe
    from tests.legal_preset import seed_preset_docs

    async with smaker() as session:
        await save_cluster_probe(
            session,
            ClusterProbe(
                api_reachable=True,
                k8s_version="v1.36.2+rke2r1",
                distro="rke2",
                hami_ready=True,
                kata_runtimeclass=True,
                storage_classes=(INSTANCE_DISK_STORAGE_CLASS, DATA_DISK_STORAGE_CLASS),
            ),
        )
        await seed_preset_docs(session)
        await session.commit()


def _reset_process_state() -> None:
    from app.core import audit as audit_mod
    from app.modules.services import service as services_service

    services_service.clear_endpoint_auth_cache()
    audit_mod.reset_audit_gate()


@pytest.fixture
async def sm(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Initialise the database and process state; cleanup empties the tables and resets
    sequences."""
    from app.core.db import get_sessionmaker
    from app.models_registry import Base

    smaker = get_sessionmaker()
    await _seed_baseline(smaker)
    _reset_process_state()
    yield smaker

    async with engine.begin() as conn:
        probe = " UNION ALL ".join(
            f"SELECT '{t.name}' WHERE EXISTS (SELECT 1 FROM \"{t.name}\")"
            for t in Base.metadata.sorted_tables
        )
        dirty = (await conn.execute(text(probe))).scalars().all()
        if dirty:
            names = ", ".join(f'"{n}"' for n in dirty)
            await conn.execute(text(f"TRUNCATE {names} CASCADE"))
        await conn.execute(
            text(
                "SELECT setval(c.oid::regclass, 1, false) FROM pg_class c "
                "WHERE c.relkind = 'S' AND c.relnamespace = 'public'::regnamespace"
            )
        )


@pytest.fixture(scope="session")
def asgi_app(pg_url: str) -> FastAPI:
    """Session-level app without lifespan."""
    from app.main import create_app

    return create_app()


@pytest.fixture
async def client(
    sm: async_sessionmaker[AsyncSession], asgi_app: FastAPI
) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=asgi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _inject_fake(auto_ready: bool) -> Iterator["FakeOrchestrator"]:
    from app.core.k8s import set_orchestrator
    from app.core.k8s.fake import FakeOrchestrator

    orch = FakeOrchestrator(auto_ready=auto_ready)
    set_orchestrator(orch)
    yield orch
    set_orchestrator(None)


@pytest.fixture
def fake() -> Iterator["FakeOrchestrator"]:
    """FakeOrchestrator that needs an explicit mark_ready."""
    yield from _inject_fake(auto_ready=False)


@pytest.fixture
def fake_auto_ready() -> Iterator["FakeOrchestrator"]:
    """FakeOrchestrator whose Pods become ready automatically."""
    yield from _inject_fake(auto_ready=True)


@pytest.fixture(autouse=True)
def _reset_prom_client() -> Iterator[None]:
    """Reset the Prometheus client on cleanup."""
    yield
    from app.modules.metering import prom

    prom.set_client(None)
