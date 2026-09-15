"""Create the frozen knowledge schema in an uninitialized database only."""

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision = "kb_20260915_base"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    if not context.is_offline_mode():
        inspector = sa.inspect(op.get_bind())
        tables = set(inspector.get_table_names(schema="public"))
        owned = {
            "knowledges",
            "documents",
            "files",
            "knowledge_metadatas",
            "knowledge_metadata_bindings",
            "knowledge_shares",
        }
        if tables & owned:
            raise RuntimeError("Existing knowledge tables require verified adoption before upgrade")
        for external in ("users", "model_configs", "workspaces"):
            if external not in tables:
                raise RuntimeError(f"Missing external FK target: public.{external}")
            columns = {
                column["name"]: column
                for column in inspector.get_columns(external, schema="public")
            }
            if "id" not in columns or not isinstance(columns["id"]["type"], sa.UUID):
                raise RuntimeError(f"External FK target must expose UUID id: public.{external}")
    # Frozen, reviewed creation operations; never import runtime metadata here.
    op.create_table(
        "documents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("kb_id", sa.UUID(), nullable=False, comment="knowledges.id"),
        sa.Column("created_by", sa.UUID(), nullable=False, comment="users.id"),
        sa.Column("file_id", sa.UUID(), nullable=False, comment="files.id"),
        sa.Column("file_name", sa.String(), nullable=False, comment="file name"),
        sa.Column("file_ext", sa.String(), nullable=False, comment="file extension"),
        sa.Column("file_size", sa.Integer(), nullable=True, comment="file size(byte)"),
        sa.Column("file_meta", sa.JSON(), nullable=False),
        sa.Column("parser_id", sa.String(), nullable=False, comment="default parser ID"),
        sa.Column("parser_config", sa.JSON(), nullable=False, comment="default parser config"),
        sa.Column("chunk_num", sa.Integer(), nullable=True, comment="chunk num"),
        sa.Column("progress", sa.DOUBLE_PRECISION(), nullable=True),
        sa.Column("progress_msg", sa.String(), nullable=True, comment="process message"),
        sa.Column("process_begin_at", postgresql.TIMESTAMP(precision=6), nullable=True),
        sa.Column("process_duration", sa.DOUBLE_PRECISION(), nullable=True),
        sa.Column(
            "run",
            sa.Integer(),
            nullable=True,
            comment="start to run processing or cancel.(1: run it; 2: cancel)",
        ),
        sa.Column(
            "status", sa.Integer(), nullable=True, comment="is it validate(0: wasted, 1: validate)"
        ),
        sa.Column("created_at", postgresql.TIMESTAMP(precision=6), nullable=True),
        sa.Column("updated_at", postgresql.TIMESTAMP(precision=6), nullable=True),
        sa.Column(
            "meta_data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="{field_name: value}",
        ),
        sa.PrimaryKeyConstraint("id", name="documents_pkey"),
    )
    op.create_index("ix_documents_file_ext", "documents", ["file_ext"], unique=False)
    op.create_index("ix_documents_file_name", "documents", ["file_name"], unique=False)
    op.create_index("ix_documents_id", "documents", ["id"], unique=False)
    op.create_index("ix_documents_parser_id", "documents", ["parser_id"], unique=False)
    op.create_table(
        "files",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("kb_id", sa.UUID(), nullable=False, comment="knowledges.id"),
        sa.Column("created_by", sa.UUID(), nullable=False, comment="users.id"),
        sa.Column("parent_id", sa.UUID(), nullable=True, comment="parent folder id"),
        sa.Column(
            "file_name",
            sa.String(),
            nullable=False,
            comment="file name or folder name,default folder name is /",
        ),
        sa.Column("file_ext", sa.String(), nullable=False, comment="file extension:folder|pdf"),
        sa.Column("file_size", sa.Integer(), nullable=True, comment="file size(byte)"),
        sa.Column("created_at", postgresql.TIMESTAMP(precision=6), nullable=True),
        sa.Column("file_url", sa.String(), nullable=True, comment="file comes from a website url"),
        sa.Column(
            "file_key",
            sa.String(length=512),
            nullable=True,
            comment="storage file key for FileStorageService",
        ),
        sa.Column(
            "file_role",
            sa.String(length=32),
            server_default=sa.text("'source'::character varying"),
            nullable=False,
            comment="source or derived_image",
        ),
        sa.Column(
            "source_document_id",
            sa.UUID(),
            nullable=True,
            comment="documents.id for a derived image asset",
        ),
        sa.PrimaryKeyConstraint("id", name="files_pkey"),
    )
    op.create_index("ix_files_file_ext", "files", ["file_ext"], unique=False)
    op.create_index("ix_files_file_key", "files", ["file_key"], unique=False)
    op.create_index("ix_files_file_name", "files", ["file_name"], unique=False)
    op.create_index("ix_files_file_role", "files", ["file_role"], unique=False)
    op.create_index("ix_files_file_url", "files", ["file_url"], unique=False)
    op.create_index("ix_files_id", "files", ["id"], unique=False)
    op.create_index("ix_files_source_document_id", "files", ["source_document_id"], unique=False)
    op.create_table(
        "knowledge_metadata_bindings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False, comment="租户ID"),
        sa.Column("knowledge_id", sa.UUID(), nullable=False, comment="知识库ID"),
        sa.Column("metadata_id", sa.UUID(), nullable=False, comment="元数据定义ID"),
        sa.Column("document_id", sa.UUID(), nullable=False, comment="文档ID"),
        sa.Column("created_by", sa.UUID(), nullable=True, comment="创建人"),
        sa.Column("created_at", postgresql.TIMESTAMP(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="knowledge_metadata_bindings_pkey"),
        sa.UniqueConstraint(
            "knowledge_id", "metadata_id", "document_id", name="uq_knowledge_metadata_binding"
        ),
    )
    op.create_index(
        "ix_knowledge_metadata_bindings_id", "knowledge_metadata_bindings", ["id"], unique=False
    )
    op.create_index(
        "ix_knowledge_metadata_bindings_knowledge_id",
        "knowledge_metadata_bindings",
        ["knowledge_id"],
        unique=False,
    )
    op.create_table(
        "knowledge_metadatas",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False, comment="租户ID"),
        sa.Column("knowledge_id", sa.UUID(), nullable=False, comment="知识库ID"),
        sa.Column("type", sa.String(), nullable=False, comment="字段类型: string | number | time"),
        sa.Column("name", sa.String(length=255), nullable=False, comment="字段名"),
        sa.Column("created_by", sa.UUID(), nullable=True, comment="创建人"),
        sa.Column("updated_by", sa.UUID(), nullable=True, comment="更新人"),
        sa.Column("created_at", postgresql.TIMESTAMP(), nullable=True),
        sa.Column("updated_at", postgresql.TIMESTAMP(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="knowledge_metadatas_pkey"),
        sa.UniqueConstraint("knowledge_id", "name", name="uq_knowledge_metadata_name"),
    )
    op.create_index("ix_knowledge_metadatas_id", "knowledge_metadatas", ["id"], unique=False)
    op.create_index(
        "ix_knowledge_metadatas_knowledge_id", "knowledge_metadatas", ["knowledge_id"], unique=False
    )
    op.create_table(
        "knowledges",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False, comment="workspaces.id"),
        sa.Column("created_by", sa.UUID(), nullable=False, comment="users.id"),
        sa.Column(
            "parent_id", sa.UUID(), nullable=True, comment="parent folder id when type is Folder"
        ),
        sa.Column("name", sa.String(), nullable=False, comment="KB name"),
        sa.Column("description", sa.String(), nullable=True, comment="KB description"),
        sa.Column("avatar", sa.String(), nullable=True, comment="avatar url"),
        sa.Column(
            "type", sa.String(), nullable=True, comment="Type:General|Web|Third-party|Folder"
        ),
        sa.Column(
            "permission_id",
            sa.String(),
            nullable=True,
            comment="permission ID:Private|Share|Memory",
        ),
        sa.Column("embedding_id", sa.UUID(), nullable=True, comment="default embedding model ID"),
        sa.Column("reranker_id", sa.UUID(), nullable=True, comment="default reranker model ID"),
        sa.Column("llm_id", sa.UUID(), nullable=True, comment="default llm model ID"),
        sa.Column("image2text_id", sa.UUID(), nullable=True, comment="default image2text model ID"),
        sa.Column("doc_num", sa.Integer(), nullable=True, comment="doc num"),
        sa.Column("chunk_num", sa.Integer(), nullable=True, comment="chunk num"),
        sa.Column("parser_id", sa.String(), nullable=True, comment="default parser ID"),
        sa.Column("parser_config", sa.JSON(), nullable=False, comment="default parser config"),
        sa.Column(
            "status",
            sa.Integer(),
            nullable=True,
            comment="is it validate(0: disable, 1: enable, 2:Soft-delete)",
        ),
        sa.Column("created_at", postgresql.TIMESTAMP(precision=6), nullable=True),
        sa.Column("updated_at", postgresql.TIMESTAMP(precision=6), nullable=True),
        sa.Column(
            "external_id",
            sa.String(length=36),
            nullable=True,
            comment="user-defined external identifier, workspace-unique",
        ),
        sa.Column(
            "builtin_metadata_enabled",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="builtin metadata switch (0: disabled, 1: enabled)",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="knowledges_created_by_fkey"),
        sa.ForeignKeyConstraint(
            ["embedding_id"],
            ["model_configs.id"],
            name="knowledges_embedding_id_fkey",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["image2text_id"],
            ["model_configs.id"],
            name="knowledges_image2text_id_fkey",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["llm_id"], ["model_configs.id"], name="knowledges_llm_id_fkey", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["reranker_id"],
            ["model_configs.id"],
            name="knowledges_reranker_id_fkey",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="knowledges_pkey"),
    )
    op.create_index("ix_knowledges_external_id", "knowledges", ["external_id"], unique=False)
    op.create_index("ix_knowledges_id", "knowledges", ["id"], unique=False)
    op.create_index("ix_knowledges_name", "knowledges", ["name"], unique=False)
    op.create_index("ix_knowledges_parser_id", "knowledges", ["parser_id"], unique=False)
    op.create_index("ix_knowledges_status", "knowledges", ["status"], unique=False)
    op.create_table(
        "knowledge_shares",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source_kb_id", sa.UUID(), nullable=False, comment="source knowledges.id"),
        sa.Column("source_workspace_id", sa.UUID(), nullable=False, comment="source workspaces.id"),
        sa.Column("target_kb_id", sa.UUID(), nullable=False, comment="target knowledges.id"),
        sa.Column("target_workspace_id", sa.UUID(), nullable=False, comment="target workspaces.id"),
        sa.Column("shared_by", sa.UUID(), nullable=False, comment="shared users.id"),
        sa.Column("created_at", postgresql.TIMESTAMP(precision=6), nullable=True),
        sa.Column("updated_at", postgresql.TIMESTAMP(precision=6), nullable=True),
        sa.ForeignKeyConstraint(
            ["shared_by"], ["users.id"], name="knowledge_shares_shared_by_fkey"
        ),
        sa.ForeignKeyConstraint(
            ["target_kb_id"], ["knowledges.id"], name="knowledge_shares_target_kb_id_fkey"
        ),
        sa.ForeignKeyConstraint(
            ["target_workspace_id"],
            ["workspaces.id"],
            name="knowledge_shares_target_workspace_id_fkey",
        ),
        sa.PrimaryKeyConstraint("id", name="knowledge_shares_pkey"),
    )
    op.create_index("ix_knowledge_shares_id", "knowledge_shares", ["id"], unique=False)


def downgrade():
    raise RuntimeError(
        "Knowledge baseline downgrade is disabled; use an explicitly reviewed forward migration"
    )
