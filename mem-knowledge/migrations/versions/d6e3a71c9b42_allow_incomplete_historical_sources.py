"""Allow migrated sources to be repaired before their next synchronization.

Revision ID: d6e3a71c9b42
Revises: c7d9a2e5f831
"""

from alembic import op

revision = "d6e3a71c9b42"
down_revision = "c7d9a2e5f831"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Source completeness is checked for new submissions and before synchronization.
    op.drop_constraint("ck_kpc_yuque_fields", "knowledge_parser_configs", type_="check")
    op.drop_constraint("ck_kpc_feishu_fields", "knowledge_parser_configs", type_="check")


def downgrade() -> None:
    # PostgreSQL refuses the downgrade if pending sources have not been repaired.
    op.create_check_constraint(
        "ck_kpc_yuque_fields",
        "knowledge_parser_configs",
        "third_party_platform IS DISTINCT FROM 'yuque' OR "
        "(yuque_user_id IS NOT NULL AND yuque_token IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_kpc_feishu_fields",
        "knowledge_parser_configs",
        "third_party_platform IS DISTINCT FROM 'feishu' OR "
        "(feishu_app_id IS NOT NULL AND feishu_app_secret IS NOT NULL "
        "AND feishu_folder_token IS NOT NULL)",
    )
