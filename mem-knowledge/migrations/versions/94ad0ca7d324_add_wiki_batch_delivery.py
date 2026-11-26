"""Wiki document batch ownership and single active page delivery."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "94ad0ca7d324"
down_revision = "72e42a8c6d19"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("wiki_document_states", sa.Column("batch_id", UUID()))
    op.add_column("wiki_document_states", sa.Column("claimed_at", sa.DateTime()))
    scope = ["tenant_id", "workspace_id", "kb_id"]
    op.create_table(
        "wiki_reduce_deliveries",
        *(sa.Column(key, UUID(), nullable=False) for key in scope),
        sa.Column("page_id", UUID(), primary_key=True),
        sa.Column("delivery_id", UUID(), nullable=False),
        sa.Column("attempt_id", UUID(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("input_revision", sa.BigInteger(), nullable=False),
        sa.Column("cas_used", sa.Integer(), nullable=False),
        sa.Column("compact_used", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("deadline_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledges.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            [*scope, "page_id"],
            [f"wiki_pages.{k}" for k in [*scope, "id"]],
            name="fk_wiki_delivery_page",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','retry_wait')", name="ck_wiki_delivery_status"
        ),
    )


def downgrade():
    op.drop_table("wiki_reduce_deliveries")
    op.drop_column("wiki_document_states", "claimed_at")
    op.drop_column("wiki_document_states", "batch_id")
