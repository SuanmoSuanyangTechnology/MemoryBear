"""Alembic environment for memory-profile-owned PostgreSQL tables only."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from src.bootstrap import get_settings
from src.models.owned import ProfileBase

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = ProfileBase.metadata
OWNED_TABLE_KEYS = frozenset(target_metadata.tables)
VERSION_TABLE = "alembic_version_profile"


def _table_key(obj: Any, name: str) -> str:
    schema = getattr(obj, "schema", None)
    return f"{schema}.{name}" if schema else name


def include_name(
    name: str | None,
    type_: str,
    parent_names: dict[str, str | None],
) -> bool:
    """Avoid reflecting tables outside memory-profile ownership."""

    if type_ != "table" or name is None:
        return True
    qualified_name = parent_names.get("schema_qualified_table_name") or name
    return qualified_name in OWNED_TABLE_KEYS


def include_object(
    obj: Any,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: Any,
) -> bool:
    """Keep autogenerate scoped to ProfileBase metadata."""

    del reflected, compare_to
    if type_ != "table" or name is None:
        return True
    return _table_key(obj, name) in OWNED_TABLE_KEYS


def configure_context(*, connection: Connection | None = None, url: str | None = None) -> None:
    options: dict[str, Any] = {
        "target_metadata": target_metadata,
        "include_name": include_name,
        "include_object": include_object,
        "include_schemas": True,
        "compare_type": True,
        "compare_server_default": True,
        "transaction_per_migration": True,
        "version_table": VERSION_TABLE,
    }
    if connection is not None:
        options["connection"] = connection
    if url is not None:
        options.update(
            url=url,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
        )
    context.configure(**options)


def run_migrations_offline() -> None:
    configure_context(url=get_settings().alembic_database_url)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    configure_context(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(
        get_settings().alembic_database_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"timezone": "UTC"}},
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
