"""测试基建:testcontainers 起真 PG18,create_all 建表,函数级 TRUNCATE 隔离。

迁移与模型的一致性由 CI 的 `alembic upgrade head && alembic check` 把关,单测走 create_all。
"""

import os
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

if TYPE_CHECKING:
    from app.core.k8s.fake import FakeOrchestrator

# 静态测试环境变量:conftest 导入即就位,不挂在 pg_url fixture 上 —— 不消费数据库 fixture
# 的用例单独运行时也需要它们,挂 fixture 上会让窄选集的顺序组合炸 Settings 校验。
# 一律强制赋值(非 setdefault):shell 里残留的同名变量不得渗进测试会话
os.environ["SUPERDL_ENVIRONMENT"] = "test"
os.environ["SUPERDL_K8S_BACKEND"] = "fake"  # 单测一律 FakeOrchestrator,隔离本地 .env 的 real 配置
# 超时用例按默认 5 分钟断言,钉死不受 .env 影响
os.environ["SUPERDL_CREATING_TIMEOUT_SECONDS"] = "300"
# 测试签名密钥 ≥32 字节(与 prod 校验同线;PyJWT 对短 HMAC 键打 InsecureKeyLengthWarning)
os.environ["SUPERDL_JWT_SECRET"] = "test-jwt-secret-32-bytes-minimum!!"


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    with PostgresContainer("postgres:18", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        os.environ["SUPERDL_DATABASE_URL"] = url
        # 环境变量就位后再清缓存,让所有 get_settings() 读到测试库
        from app.core.config import get_settings

        get_settings.cache_clear()
        yield url
        get_settings.cache_clear()
    # docker-py 客户端显式关闭(容器已停):其 urllib3 连接/socket 在 GC 终结时会打
    # PytestUnraisableExceptionWarning(第三方 __del__ 兜底,主动关闭即不再触发)
    pg.get_docker_client().client.close()


@pytest.fixture(scope="session")
async def engine(pg_url: str) -> AsyncIterator[AsyncEngine]:
    from app.core.db import code_schema_head, dispose_engine, get_engine
    from app.models_registry import Base

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all 不含 alembic_version:/readyz 的 schema 版本比对需要盖章到代码 head
        # (不在 Base.metadata 内,sm fixture 的 TRUNCATE 清不到,随会话存活)
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
    """函数级 sessionmaker;测试结束 TRUNCATE 全部表保证隔离。"""
    from app.core.db import get_sessionmaker
    from app.models_registry import Base

    smaker = get_sessionmaker()
    # 集群能力缓存预置健康态(等价"worker 已跑过一轮巡检"):shared 档下发门禁读它。
    # 门禁负例测试自行 UPDATE/DELETE 该行。
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
    # 法务文档预置:单测走 create_all 不含迁移数据,等价「迁移已跑」显式播种
    from tests.legal_preset import seed_preset_docs

    async with smaker() as session:
        await seed_preset_docs(session)
        await session.commit()
    yield smaker

    async with engine.begin() as conn:
        tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        if tables:
            await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def client(sm: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncClient]:
    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def fake() -> Iterator["FakeOrchestrator"]:
    """注入 FakeOrchestrator,收尾恢复默认(不注入时 get_orchestrator 走 lru_cache 的共享实例,
    用例之间会串状态)。

    auto_ready=False 是全局基线:Pod 不自动就绪,creating → running 的时序由用例自己驱动。
    需要「建出来即 Ready」的模块(集群巡检、节点台账)在本文件外就地覆盖同名 fixture。
    """
    from app.core.k8s import set_orchestrator
    from app.core.k8s.fake import FakeOrchestrator

    orch = FakeOrchestrator(auto_ready=False)
    set_orchestrator(orch)
    yield orch
    set_orchestrator(None)
