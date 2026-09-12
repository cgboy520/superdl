"""测试基建:testcontainers 起真 PG18(关持久化),create_all 建表,会话级 app,
函数级隔离 = 只 TRUNCATE 非空表 + 序列全量归位。"""

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

# 静态测试环境变量:导入即就位(不挂 fixture),强制赋值(非 setdefault)
os.environ["SUPERDL_ENVIRONMENT"] = "test"
os.environ["SUPERDL_K8S_BACKEND"] = "fake"  # 单测一律 FakeOrchestrator,隔离本地 .env 的 real 配置
# 超时钉死 5 分钟
os.environ["SUPERDL_CREATING_TIMEOUT_SECONDS"] = "300"
# 测试签名密钥 ≥32 字节
os.environ["SUPERDL_JWT_SECRET"] = "test-jwt-secret-32-bytes-minimum!!"
# bcrypt 取最低 cost:一次管理员建号+登录+TOTP 绑定要跑 12 次哈希,cost 12 下约 3s
os.environ["SUPERDL_BCRYPT_ROUNDS"] = "4"
# Alertmanager webhook 固定 token(端点无 token 一律 401,测试用例带 AM_HEADERS)
os.environ["SUPERDL_ALERTMANAGER_TOKEN"] = "test-alertmanager-token"

# 测试库不要持久化保证:每次 commit / TRUNCATE 都免 fsync
_PG_TEST_CMD = "postgres -c fsync=off -c synchronous_commit=off -c full_page_writes=off"


@pytest.fixture(scope="session", autouse=True)
def _logging_pipeline() -> None:
    """测试不跑 lifespan,在此装上与运行期同一套日志管道;structlog 未配置时的默认渲染器会用 rich
    带局部变量渲染异常栈,一条 500 的深栈要几十秒。"""
    from app.core.logging import setup_logging

    setup_logging()


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    container = PostgresContainer("postgres:18", driver="asyncpg").with_command(_PG_TEST_CMD)
    with container as pg:
        # Windows 上 localhost 先解析到 ::1,Docker 端口转发对 IPv6 不应答:连接池每条新连接
        # 先白等 21s 超时才回落 IPv4;直接给 IPv4 字面量
        host = pg.get_container_host_ip()
        url = pg.get_connection_url(host="127.0.0.1" if host == "localhost" else host)
        os.environ["SUPERDL_DATABASE_URL"] = url
        # 环境变量就位后再清缓存
        from app.core.config import get_settings

        get_settings.cache_clear()
        yield url
        get_settings.cache_clear()
    # docker-py 客户端显式关闭
    pg.get_docker_client().client.close()


@pytest.fixture(scope="session")
async def engine(pg_url: str) -> AsyncIterator[AsyncEngine]:
    from app.core.db import code_schema_head, dispose_engine, get_engine
    from app.models_registry import Base

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # alembic_version 盖章到代码 head(/readyz 比对用;随会话存活)
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


@pytest.fixture
async def sm(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """函数级 sessionmaker;测试结束清空非空表并归位序列保证隔离。"""
    from app.core.db import get_sessionmaker
    from app.models_registry import Base

    smaker = get_sessionmaker()
    # 集群能力缓存预置健康态(等价 worker 已跑过一轮巡检)
    from app.core.k8s.base import (
        INSTANCE_DISK_STORAGE_CLASS,
        JUICEFS_STORAGE_CLASS,
        ClusterProbe,
    )
    from app.modules.nodes.service import save_cluster_probe

    async with smaker() as session:
        await save_cluster_probe(
            session,
            ClusterProbe(
                api_reachable=True,
                k8s_version="v1.36.2+rke2r1",
                distro="rke2",
                hami_ready=True,
                kata_runtimeclass=True,  # dedicated 档门禁的正例基线
                storage_classes=(INSTANCE_DISK_STORAGE_CLASS, JUICEFS_STORAGE_CLASS),
            ),
        )
        await session.commit()
    # 法务文档预置(等价迁移已跑)
    from tests.legal_preset import seed_preset_docs

    async with smaker() as session:
        await seed_preset_docs(session)
        await session.commit()
    # extAuth 鉴权缓存是进程内态,逐用例清空
    from app.modules.services import service as services_service

    services_service.clear_endpoint_auth_cache()
    # 审计 fail-closed 闸门同样是进程内态
    from app.core import audit as audit_mod

    audit_mod.reset_audit_gate()
    yield smaker

    async with engine.begin() as conn:
        # TRUNCATE 成本随表数(relfilenode 数)而非行数:一次往返探出非空表只截它们;
        # 序列用 setval 全量归 1(不走 RESTART IDENTITY 的 relfilenode 改写),覆盖表已空但序列已走
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
    """会话级 app:路由树约 200 条,只建一次;需要换 settings 重建的用例自行 create_app()。"""
    from app.main import create_app

    return create_app()


@pytest.fixture
async def client(
    sm: async_sessionmaker[AsyncSession], asgi_app: FastAPI
) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=asgi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def fake() -> Iterator["FakeOrchestrator"]:
    """注入 FakeOrchestrator(auto_ready=False,时序由用例驱动),收尾恢复默认。"""
    from app.core.k8s import set_orchestrator
    from app.core.k8s.fake import FakeOrchestrator

    orch = FakeOrchestrator(auto_ready=False)
    set_orchestrator(orch)
    yield orch
    set_orchestrator(None)


@pytest.fixture
def fake_auto_ready() -> Iterator["FakeOrchestrator"]:
    """auto_ready=True 的 FakeOrchestrator;与基线 fake 互斥,同一用例只取其一。"""
    from app.core.k8s import set_orchestrator
    from app.core.k8s.fake import FakeOrchestrator

    orch = FakeOrchestrator(auto_ready=True)
    set_orchestrator(orch)
    yield orch
    set_orchestrator(None)


@pytest.fixture(autouse=True)
def _reset_prom_client() -> Iterator[None]:
    """逐用例清空进程内 Prometheus 客户端。"""
    yield
    from app.modules.metering import prom

    prom.set_client(None)
