"""Persist Wiki ontology decisions in page plans and published versions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "5f28d5c8ad31"
down_revision = "a418bd107f02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("wiki_pages", "wiki_page_versions"):
        op.add_column(table, sa.Column("semantic_type", sa.String(32), nullable=True))
        op.add_column(table, sa.Column("ontology_version", sa.String(32), nullable=True))
    op.add_column(
        "wiki_page_versions", sa.Column("type_evidence", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "wiki_document_page_tasks", sa.Column("planned_ontology", postgresql.JSONB(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("wiki_document_page_tasks", "planned_ontology")
    op.drop_column("wiki_page_versions", "type_evidence")
    for table in ("wiki_page_versions", "wiki_pages"):
        op.drop_column(table, "ontology_version")
        op.drop_column(table, "semantic_type")
