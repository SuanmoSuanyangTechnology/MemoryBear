"""Run the committed knowledge migration chain before the API accepts traffic."""

from __future__ import annotations

import asyncio
import logging

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, text

from ...application.bootstrap import get_settings, resolve_bootstrap_paths
from ...application.config import KnowledgeSettings
from ...application.runtime import ProcessRuntime
from .manager import DatabaseManager
from .migration_policy import VERSION_TABLE

logger = logging.getLogger("src.database_migrations")
_MIGRATION_LOCK_KEY = f"mem-knowledge:{VERSION_TABLE}"
_LEGACY_TABLES_BASELINE = "0ab91df45c62"


def _upgrade_schema(connection: Connection, settings: KnowledgeSettings) -> None:
    service_root = resolve_bootstrap_paths().service_root
    config = Config(str(service_root / "alembic.ini"))
    config.set_main_option("script_location", str(service_root / "migrations"))
    config.attributes["settings"] = settings
    # 两段迁移在同一个连接、事务和锁下执行。
    with connection.begin():
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": _MIGRATION_LOCK_KEY},
        )
        config.attributes["connection"] = connection
        # 空库先创建 Wiki 迁移依赖的表。已有 Wiki 版本的库仍要核对这批存量表。
        command.upgrade(config, _LEGACY_TABLES_BASELINE)
        command.upgrade(config, "head")


def _upgrade_database(runtime: ProcessRuntime) -> None:
    with runtime.database.sync_connection() as connection:
        _upgrade_schema(connection, runtime.settings)


async def upgrade_database_on_startup(runtime: ProcessRuntime) -> None:
    settings = runtime.settings
    if settings.kb_process_role != "api" or not settings.db_auto_upgrade:
        logger.info("Automatic knowledge database upgrade disabled for role=%s",
                    settings.kb_process_role)
        return
    logger.info("Automatic knowledge database upgrade started version_table=%s", VERSION_TABLE)
    try:
        await asyncio.to_thread(_upgrade_database, runtime)
    except Exception as exc:
        logger.error(
            "Automatic knowledge database upgrade failed error_type=%s", type(exc).__name__
        )
        raise
    logger.info("Automatic knowledge database upgrade completed version_table=%s", VERSION_TABLE)


def main() -> None:
    """发布任务使用和服务启动相同的迁移入口。"""
    settings = get_settings()
    database = DatabaseManager(settings)
    try:
        with database.sync_connection() as connection:
            _upgrade_schema(connection, settings)
    finally:
        database.close_sync()
