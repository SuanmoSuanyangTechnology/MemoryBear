"""Register fingerprints for newly uploaded knowledge files."""

import sqlalchemy as sa
from alembic import op

revision = "a418bd107f02"
down_revision = "dee995de57c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_upload_fingerprints",
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("kb_id", sa.Uuid(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("file_id"),
    )
    op.create_index(
        "ix_knowledge_upload_fingerprints_kb_digest",
        "knowledge_upload_fingerprints",
        ["kb_id", "digest"],
    )


def downgrade() -> None:
    op.drop_table("knowledge_upload_fingerprints")
