"""Add nullable audio transcription and video understanding model references."""

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision = "kb_20260915_media"
down_revision = "kb_20260915_base"
branch_labels = None
depends_on = None


def upgrade():
    if not context.is_offline_mode():
        from migrations.schema import BASELINE_REVISION, schema_differences

        differences = schema_differences(op.get_bind(), BASELINE_REVISION)
        if differences:
            raise RuntimeError(
                "Knowledge baseline differs before media upgrade: " + "; ".join(differences)
            )
    op.add_column(
        "knowledges",
        sa.Column(
            "audio2text_id",
            postgresql.UUID(),
            nullable=True,
            comment="audio transcription model ID",
        ),
    )
    op.create_foreign_key(
        "knowledges_audio2text_id_fkey",
        "knowledges",
        "model_configs",
        ["audio2text_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "knowledges",
        sa.Column(
            "video2text_id",
            postgresql.UUID(),
            nullable=True,
            comment="video understanding model ID",
        ),
    )
    op.create_foreign_key(
        "knowledges_video2text_id_fkey",
        "knowledges",
        "model_configs",
        ["video2text_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    raise RuntimeError(
        "Media column removal is disabled; "
        "retain nullable columns when rolling back application code"
    )
