import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool

from app.core.config import get_settings
from app.core.db import _split_db_tls
from app.models_registry import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    # 迁移只需要数据库地址:prod 迁移 Job 只挂 superdl-db(最小凭据面),
    # 若构造完整 Settings 会被 prod 必填校验(jwt/metrics/edge/crypto)拒掉。
    # env 未设时回落 Settings(本地开发 .env 路径)
    return os.environ.get("SUPERDL_DATABASE_URL") or get_settings().database_url


def get_url_and_connect_args() -> tuple[str, dict[str, str]]:
    """asyncpg 不认 libpq 的 sslmode 参数名:与 app.core.db 同一条翻译(必须同口径,
    否则 prod 的 sslmode=require 连接串在迁移侧 TypeError)。"""
    return _split_db_tls(get_url())


def run_migrations_offline() -> None:
    url, _ = get_url_and_connect_args()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    url, tls_args = get_url_and_connect_args()
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = url
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=tls_args,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
