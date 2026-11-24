"""baseline: adopt pre-existing file tables (idempotent)

Revision ID: 0001_baseline
Revises:
"""
from alembic import op

from src.models import ServiceBase

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # create_all(checkfirst=True): existing tables (monolith-owned) are adopted
    # untouched; fresh dev/test databases get the exact model columns + indexes.
    ServiceBase.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("expand-only: no downgrade")
