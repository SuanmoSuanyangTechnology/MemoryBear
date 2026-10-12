"""model-service standalone migration chain.

- target_metadata carries ServiceBase.metadata; include_object only admits
  tables registered there and absent from HOST_OWNED_TABLES — the four model
  tables (host-managed again) and the shared read-only entities never take
  part in this chain's comparison or DDL. The chain only ever manages tables
  born in it.
- version_table = alembic_version_model: coexists with the monolith chain
  (alembic_version) in the same database without overwriting it
- `import src.models` registers the four tables and the tenants FK anchor
  (anchor serves FK resolution only, excluded by include_object)
- sync engine (psycopg): migrations are operational actions, no async needed
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

import src.models  # noqa: F401  注册四表 + FK 锚点
from src.bootstrap import get_settings
from src.models.base import HOST_OWNED_TABLES, ServiceBase

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = ServiceBase.metadata
VERSION_TABLE = "alembic_version_model"


def include_object(obj, name, type_, reflected, compare_to):
    """Compare only tables this chain owns; other chains' tables are never judged for drop.

    Shares the database with the monolith chain — without filtering, autogenerate
    would emit drop_table for the hundreds of tables it does not own
    (model_api_keys, tenants, apps, ...).
    """
    if type_ == "table":
        return name in ServiceBase.metadata.tables and name not in HOST_OWNED_TABLES
    return True


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        version_table=VERSION_TABLE,
        include_object=include_object,
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(
        url=get_settings().database_url_sync,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(
        get_settings().database_url_sync,
        poolclass=pool.NullPool,
        connect_args={"options": "-c timezone=UTC"},
    )
    try:
        with engine.connect() as connection:
            _configure(connection=connection)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
