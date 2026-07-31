"""202607291456

Revision ID: 038bd8a9bab7
Revises: 0cbae4a19048
Create Date: 2026-07-29 06:57:37.181444

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '038bd8a9bab7'
down_revision: Union[str, None] = '0cbae4a19048'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _run_concurrent_sql(sql: str) -> None:
    """通用工具函数：独立连接 AUTOCOMMIT 执行并发DDL"""
    config = op.get_context().config
    db_url = config.get_main_option("sqlalchemy.url")
    engine = sa.create_engine(db_url)
    with engine.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT").execute(sa.text(sql))


def upgrade() -> None:
    _run_concurrent_sql(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_conversations_app_user "
        "ON conversations (app_id, user_id, is_draft) WHERE is_active = TRUE;"
    )
    _run_concurrent_sql(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_end_users_ws_other "
        "ON end_users (workspace_id, other_id);"
    )
    _run_concurrent_sql(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_messages_conv_created "
        "ON messages (conversation_id, created_at DESC) WHERE is_deleted = FALSE AND is_current = TRUE;"
    )
    _run_concurrent_sql(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_messages_parent "
        "ON messages (parent_message_id) WHERE parent_message_id IS NOT NULL AND is_deleted = FALSE;"
    )


def downgrade() -> None:
    # DROP INDEX CONCURRENTLY 同样不能在事务内，也要独立连接执行
    _run_concurrent_sql("DROP INDEX CONCURRENTLY IF EXISTS idx_messages_parent;")
    _run_concurrent_sql("DROP INDEX CONCURRENTLY IF EXISTS idx_messages_conv_created;")
    _run_concurrent_sql("DROP INDEX CONCURRENTLY IF EXISTS idx_end_users_ws_other;")
    _run_concurrent_sql("DROP INDEX CONCURRENTLY IF EXISTS idx_conversations_app_user;")