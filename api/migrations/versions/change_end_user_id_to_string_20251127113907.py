"""change end_user_id to string

Revision ID: change_end_user_id_to_string
Revises: f210d1844b07
Create Date: 2025-11-27 11:39:07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'change_end_user_id_to_string'
down_revision: Union[str, None] = 'f210d1844b07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Change end_user_id from UUID to String
    op.alter_column('end_users', 'end_user_id',
                    existing_type=postgresql.UUID(),
                    type_=sa.String(),
                    existing_nullable=False)
    
    # Change other_id from UUID to String and make it nullable
    op.alter_column('end_users', 'other_id',
                    existing_type=postgresql.UUID(),
                    type_=sa.String(),
                    nullable=True)
    
    # Add index on end_user_id for better query performance
    op.create_index(op.f('ix_end_users_end_user_id'), 'end_users', ['end_user_id'], unique=False)


def downgrade() -> None:
    # Remove index
    op.drop_index(op.f('ix_end_users_end_user_id'), table_name='end_users')
    
    # Revert other_id back to UUID
    # Note: This will fail if there are non-UUID strings in the column
    op.alter_column('end_users', 'other_id',
                    existing_type=sa.String(),
                    type_=postgresql.UUID(),
                    nullable=False,
                    postgresql_using='other_id::uuid')
    
    # Revert end_user_id back to UUID
    # Note: This will fail if there are non-UUID strings in the column
    op.alter_column('end_users', 'end_user_id',
                    existing_type=sa.String(),
                    type_=postgresql.UUID(),
                    existing_nullable=False,
                    postgresql_using='end_user_id::uuid')
