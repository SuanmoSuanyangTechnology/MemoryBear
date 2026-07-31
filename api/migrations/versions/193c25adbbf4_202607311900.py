"""202607311900

Revision ID: 193c25adbbf4
Revises: 0318b3a81979
Create Date: 2026-07-31 11:01:03.714552

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '193c25adbbf4'
down_revision: Union[str, None] = '0318b3a81979'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    config = op.get_context().config
    url = config.get_main_option("sqlalchemy.url")
    engine = sa.create_engine(url)
    with engine.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT").execute(
            sa.text("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_messages_conversation_id ON messages (conversation_id);")
        )

def downgrade() -> None:
    config = op.get_context().config
    url = config.get_main_option("sqlalchemy.url")
    engine = sa.create_engine(url)
    with engine.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT").execute(
            sa.text("DROP INDEX CONCURRENTLY IF EXISTS ix_messages_conversation_id;")
        )
