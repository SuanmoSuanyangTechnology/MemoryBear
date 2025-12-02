"""merge heads

Revision ID: 8642a073ce2a
Revises: change_end_user_id_to_string, fbab88219447
Create Date: 2025-11-27 12:25:15.839201

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8642a073ce2a'
down_revision: Union[str, None] = ('change_end_user_id_to_string', 'fbab88219447')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
