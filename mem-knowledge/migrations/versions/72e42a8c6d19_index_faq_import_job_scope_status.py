"""Index scoped FAQ import job lookups."""

from alembic import op

revision = "72e42a8c6d19"
down_revision = "5f28d5c8ad31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_wiki_faq_import_job_scope_status",
        "wiki_faq_import_jobs",
        ["kb_id", "tenant_id", "workspace_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_wiki_faq_import_job_scope_status", table_name="wiki_faq_import_jobs")
