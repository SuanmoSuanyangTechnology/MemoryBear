"""add pg_trgm GIN index on messages.content

Revision ID: 6089fe16b70a
Revises: 335c292612f3
Create Date: 2026-10-29 02:37:46.177088

日志按 keyword 搜索走 messages.content ILIKE '%kw%'，无索引时全表扫描；
messages 为百万级表，因此用 CONCURRENTLY 建 pg_trgm GIN 索引，避免阻塞写入。

注意：
1. 必须先创建 pg_trgm 扩展（trusted extension，PG13+ 库 owner 即可创建）。
2. CREATE/DROP INDEX CONCURRENTLY 不能在事务内执行，需独立 autocommit 连接。
3. 关键词少于 3 个字符时该索引不生效，仍会全表扫描。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6089fe16b70a'
down_revision: Union[str, None] = '335c292612f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = 'ix_messages_content_trgm'


def _run_concurrent_sql(*statements: str) -> None:
    """独立 AUTOCOMMIT 连接执行并发 DDL。"""
    config = op.get_context().config
    db_url = config.get_main_option("sqlalchemy.url")
    engine = sa.create_engine(db_url)
    try:
        with engine.connect() as conn:
            conn = conn.execution_options(isolation_level="AUTOCOMMIT")
            conn.execute(sa.text("SET statement_timeout = 0"))
            for sql in statements:
                conn.execute(sa.text(sql))
    finally:
        engine.dispose()


def upgrade() -> None:
    _run_concurrent_sql(
        "CREATE EXTENSION IF NOT EXISTS pg_trgm",
        f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{INDEX_NAME}" '
        "ON messages USING gin (content gin_trgm_ops)",
    )


def downgrade() -> None:
    # 不删扩展，可能已被其他索引或查询使用
    _run_concurrent_sql(f'DROP INDEX CONCURRENTLY IF EXISTS "{INDEX_NAME}"')
