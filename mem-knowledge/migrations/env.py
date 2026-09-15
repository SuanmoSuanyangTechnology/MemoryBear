"""Knowledge-only Alembic environment. Application startup never invokes it."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from alembic.runtime.migration import StampStep

from migrations.runner import (
    VERIFIED_ADOPTION_ATTRIBUTE,
    VERSION_SCHEMA,
    MigrationSafetyError,
    prepare_connection,
    run_online,
)
from migrations.schema import (
    VERSION_TABLE,
    build_target_metadata,
    include_name,
    include_object,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

OPTIONS = {
    "target_metadata": build_target_metadata(),
    "version_table": VERSION_TABLE,
    "version_table_schema": VERSION_SCHEMA,
    "include_schemas": True,
    "include_name": include_name,
    "include_object": include_object,
    "compare_type": True,
    "compare_server_default": True,
}


def guard_stamp_steps():
    """Reject raw stamps, including programmatic calls that omit cmd_opts."""
    migration_context = context.get_context()
    if migration_context.purge:
        raise MigrationSafetyError("Purging Knowledge revision history is not supported")
    original = migration_context._migrations_fn

    def checked_steps(heads, ctx):
        steps = original(heads, ctx)
        for step in steps or ():
            if isinstance(step, StampStep):
                verified = config.attributes.get(VERIFIED_ADOPTION_ATTRIBUTE)
                if not verified or tuple(step.to_revisions) != (verified,):
                    raise MigrationSafetyError(
                        "Use scripts/knowledge_migrations.py adopt after schema verification"
                    )
            yield step

    migration_context._migrations_fn = checked_steps


def run_migrations(connection):
    prepare_connection(connection, lock=True)
    context.configure(connection=connection, **OPTIONS)
    guard_stamp_steps()
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        dialect_name="postgresql",
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **OPTIONS,
    )
    guard_stamp_steps()
    with context.begin_transaction():
        context.run_migrations()
elif config.attributes.get("connection") is not None:
    run_migrations(config.attributes["connection"])
else:
    asyncio.run(run_online(run_migrations))
