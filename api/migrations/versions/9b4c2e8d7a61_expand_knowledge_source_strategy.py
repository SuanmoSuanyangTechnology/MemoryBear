"""Expand knowledge source and immutable index strategy; backfill is a separate CLI.

Revision ID: 9b4c2e8d7a61
Revises: 804699ccc3f4
"""

import sqlalchemy as sa
from alembic import op

revision = "9b4c2e8d7a61"
down_revision = "804699ccc3f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledges",
        sa.Column("source", sa.String(16), nullable=True, comment="知识库来源；Folder 为空"),
    )
    op.add_column(
        "knowledges",
        sa.Column("index_strategy", sa.String(16), nullable=True, comment="索引策略；Folder 为空"),
    )
    op.create_check_constraint(
        "ck_knowledges_source_type", "knowledges",
        "type IS NOT NULL AND ((type = 'Folder' AND source IS NULL) OR "
        "(source IS NOT NULL AND ((type = 'General' AND source = 'space') OR "
        "(type = 'Web' AND source = 'website') OR "
        "(type = 'Third-party' AND source = 'third_party'))))",
        postgresql_not_valid=True,
    )
    op.create_check_constraint(
        "ck_knowledges_index_strategy", "knowledges",
        "type IS NOT NULL AND ((type = 'Folder' AND index_strategy IS NULL) OR "
        "(type <> 'Folder' AND index_strategy IS NOT NULL "
        "AND index_strategy IN ('rag', 'wiki', 'rag_wiki')))",
        postgresql_not_valid=True,
    )


def downgrade() -> None:
    op.drop_constraint("ck_knowledges_index_strategy", "knowledges", type_="check")
    op.drop_constraint("ck_knowledges_source_type", "knowledges", type_="check")
    op.drop_column("knowledges", "index_strategy")
    op.drop_column("knowledges", "source")
