"""Run the committed knowledge migration chain before the API accepts traffic."""

from __future__ import annotations

import asyncio
import logging

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, inspect, text

from ...application.bootstrap import get_settings, resolve_bootstrap_paths
from ...application.config import KnowledgeSettings
from ...application.runtime import ProcessRuntime
from .manager import DatabaseManager
from .migration_policy import VERSION_TABLE

logger = logging.getLogger("src.database_migrations")
_MIGRATION_LOCK_KEY = f"mem-knowledge:{VERSION_TABLE}"
_LEGACY_TABLES_BASELINE = "0ab91df45c62"
_WIKI_FAQ_INDEX = "72e42a8c6d19"
_LEGACY_WIKI_BATCH = "94ad0ca7d324"
_LEGACY_OWNERSHIP_MERGE = "ef2da68b4c13"
_WIKI_BATCH_WITH_OWNERSHIP = "a73cfb6893d0"


def _reconcile_legacy_revisions(connection: Connection, config: Config) -> None:
    context = MigrationContext.configure(connection, opts={"version_table": VERSION_TABLE})
    current = set(context.get_current_heads())
    if not current or current in ({_LEGACY_TABLES_BASELINE}, {_WIKI_BATCH_WITH_OWNERSHIP}):
        return

    scripts = ScriptDirectory.from_config(config)
    wiki_revisions = {
        script.revision
        for script in scripts.iterate_revisions(_WIKI_FAQ_INDEX, _LEGACY_TABLES_BASELINE)
    }
    if current == {_LEGACY_OWNERSHIP_MERGE}:
        resume_at = _WIKI_FAQ_INDEX
    elif current == {_LEGACY_OWNERSHIP_MERGE, _LEGACY_WIKI_BATCH}:
        resume_at = _WIKI_BATCH_WITH_OWNERSHIP
    else:
        wiki_heads = current & (wiki_revisions | {_LEGACY_WIKI_BATCH})
        if not wiki_heads and len(current) == 1:
            return
        if len(wiki_heads) != 1 or current - wiki_heads - {_LEGACY_TABLES_BASELINE}:
            raise RuntimeError(f"知识库历史迁移版本组合无法接续：{sorted(current)}")
        (resume_at,) = wiki_heads
        if _LEGACY_TABLES_BASELINE not in current:
            # 旧 Wiki 链仍需执行六表接管校验，再从已经完成的 Wiki 版本继续。
            with Operations.context(context):
                scripts.get_revision(_LEGACY_TABLES_BASELINE).module.upgrade()
        if resume_at == _LEGACY_WIKI_BATCH:
            resume_at = _WIKI_BATCH_WITH_OWNERSHIP

    if resume_at == _WIKI_BATCH_WITH_OWNERSHIP:
        inspector = inspect(connection)
        has_delivery_table = inspector.has_table("wiki_reduce_deliveries", schema="public")
        state_columns = {
            column["name"]
            for column in inspector.get_columns("wiki_document_states", schema="public")
        }
        if not has_delivery_table or not {"batch_id", "claimed_at"} <= state_columns:
            raise RuntimeError("知识库历史版本缺少 Wiki 批量投递结构")

    # 逐条移除已归并的版本记录，并保留已经登记且仍有效的版本。
    for revision in sorted(current - {resume_at}):
        connection.execute(
            text(f"DELETE FROM {VERSION_TABLE} WHERE version_num = :revision"),
            {"revision": revision},
        )
    if resume_at not in current:
        connection.execute(
            text(f"INSERT INTO {VERSION_TABLE} (version_num) VALUES (:revision)"),
            {"revision": resume_at},
        )


def _upgrade_schema(connection: Connection, settings: KnowledgeSettings) -> None:
    service_root = resolve_bootstrap_paths().service_root
    config = Config(str(service_root / "alembic.ini"))
    config.set_main_option("script_location", str(service_root / "migrations"))
    config.attributes["settings"] = settings
    # 历史版本接续与后续迁移在同一个连接、事务和锁下执行。
    with connection.begin():
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": _MIGRATION_LOCK_KEY},
        )
        config.attributes["connection"] = connection
        _reconcile_legacy_revisions(connection, config)
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
