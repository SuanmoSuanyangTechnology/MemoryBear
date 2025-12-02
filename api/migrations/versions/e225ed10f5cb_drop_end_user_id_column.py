"""drop_end_user_id_column

Revision ID: e225ed10f5cb
Revises: 8642a073ce2a
Create Date: 2025-11-29 12:27:30.459770

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e225ed10f5cb'
down_revision: Union[str, None] = '8642a073ce2a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the index first
    op.drop_index('ix_end_users_end_user_id', table_name='end_users')
    
    # Drop the end_user_id column
    op.drop_column('end_users', 'end_user_id')


def downgrade() -> None:
    # Re-add the end_user_id column
    op.add_column('end_users', sa.Column('end_user_id', sa.String(), nullable=False))
    
    # Re-create the index
    op.create_index('ix_end_users_end_user_id', 'end_users', ['end_user_id'], unique=False)
