"""add_version_name_to_app_releases

Revision ID: 2e8146c98e3d
Revises: 6e254c5f498e
Create Date: 2025-11-25 21:05:09.304036

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2e8146c98e3d'
down_revision: Union[str, None] = '6e254c5f498e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Step 1: Add version_name column as nullable
    op.add_column('app_releases', sa.Column('version_name', sa.String(), nullable=True))
    
    # Step 2: Fill existing rows with default value based on version number
    op.execute("UPDATE app_releases SET version_name = 'v' || version::text WHERE version_name IS NULL")
    
    # Step 3: Make the column NOT NULL
    op.alter_column('app_releases', 'version_name', nullable=False)


def downgrade() -> None:
    op.drop_column('app_releases', 'version_name')
