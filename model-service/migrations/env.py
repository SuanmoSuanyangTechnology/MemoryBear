"""model-service 独立迁移链（D-M7-8）。

- target_metadata 只挂 ServiceBase.metadata，且 include_object 只放行 OWNED_TABLES
  （model_configs / model_bases / model_channels / model_usage_records）
- version_table = alembic_version_model：与老单体链（alembic_version）同库共存互不覆盖
- `import src.models` 触发四表与 tenants FK 锚点注册（锚点仅供 FK 解析，白名单外）
- 同步 engine（psycopg）：迁移是运维动作，无需异步链路
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

import src.models  # noqa: F401  注册四表 + FK 锚点
from src.bootstrap import get_settings
from src.models.base import OWNED_TABLES, ServiceBase

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = ServiceBase.metadata
VERSION_TABLE = "alembic_version_model"


def include_object(obj, name, type_, reflected, compare_to):
    """autogenerate 只对比四表：库中存在但非本链自有的表不判删除。

    与老单体链共库，不做过滤时 autogenerate 会把 model_api_keys / tenants / apps
    等上百张他域表生成 drop_table。
    """
    if type_ == "table":
        return name in OWNED_TABLES
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
