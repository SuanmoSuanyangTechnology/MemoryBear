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


def upgrade() -> None:
    # CREATE INDEX CONCURRENTLY must run outside a transaction.
    # Each index is wrapped in COMMIT / BEGIN to exit the Alembic DDL transaction.
    op.execute("COMMIT")
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_conversations_app_user "
        "ON conversations (app_id, user_id, is_draft) WHERE is_active = TRUE"
    )
    op.execute("BEGIN")
    op.execute("COMMIT")
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_end_users_ws_other "
        "ON end_users (workspace_id, other_id)"
    )
    op.execute("BEGIN")
    op.execute("COMMIT")
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_messages_conv_created "
        "ON messages (conversation_id, created_at DESC) WHERE is_deleted = FALSE AND is_current = TRUE"
    )
    op.execute("BEGIN")
    op.execute("COMMIT")
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_messages_parent "
        "ON messages (parent_message_id) WHERE parent_message_id IS NOT NULL AND is_deleted = FALSE"
    )
    op.execute("BEGIN")


def downgrade() -> None:
    op.drop_index('idx_messages_parent', table_name='messages', postgresql_where=sa.text('parent_message_id IS NOT NULL AND is_deleted = FALSE'))
    op.drop_index('idx_messages_conv_created', table_name='messages', postgresql_where=sa.text('is_deleted = FALSE AND is_current = TRUE'))
    op.drop_index('idx_end_users_ws_other', table_name='end_users')
    op.drop_index('idx_conversations_app_user', table_name='conversations', postgresql_where=sa.text('is_active = TRUE'))
