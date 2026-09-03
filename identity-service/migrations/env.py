"""identity 独立迁移链。

- target_metadata 只挂 ServiceBase.metadata（acl_rules/audit_logs），autogenerate 看不到只读映射表
- version_table = alembic_version_identity：与老单体链（alembic_version）同一库共存互不覆盖
- 全异步：asyncpg URL，连接参数对齐 identity/db.py（UTC 时区 + 60s 语句超时）
"""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from identity.config import settings
from identity.models.base import ServiceBase

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = ServiceBase.metadata
VERSION_TABLE = "alembic_version_identity"


def include_object(obj, name, type_, reflected, compare_to):
    """autogenerate 只对比本链自有表，避免把 core/老单体表判为删除。

    本链与老单体链（alembic_version）共存于同一库，不做过滤时 autogenerate
    会把库中存在但不在 ServiceBase.metadata 的表（users/tenants/... 100+ 张）
    生成 drop_table。
    """
    if type_ == "table":
        return name in ServiceBase.metadata.tables
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=VERSION_TABLE,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table=VERSION_TABLE,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = create_async_engine(
        settings.DATABASE_URL,
        poolclass=pool.NullPool,
        connect_args={"server_settings": {"timezone": "UTC", "statement_timeout": "60000"}},
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
