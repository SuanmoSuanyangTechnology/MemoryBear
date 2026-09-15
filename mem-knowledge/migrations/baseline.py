"""Frozen schema observed on 2026-09-15 before the media increment.

Historical declarations must not depend on the changing runtime models.
External tables are FK-resolution stubs and never migration-owned.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


def build_metadata() -> sa.MetaData:
    metadata = sa.MetaData()
    for name in ("users", "model_configs", "workspaces"):
        sa.Table(name, metadata, sa.Column("id", postgresql.UUID(), primary_key=True))
    sa.Table(
        "knowledges",
        metadata,
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(), nullable=False, comment="workspaces.id"),
        sa.Column("created_by", postgresql.UUID(), nullable=False, comment="users.id"),
        sa.Column(
            "parent_id",
            postgresql.UUID(),
            nullable=True,
            comment="parent folder id when type is Folder",
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
        sa.Column(
            "embedding_id", postgresql.UUID(), nullable=True, comment="default embedding model ID"
        ),
        sa.Column(
            "reranker_id", postgresql.UUID(), nullable=True, comment="default reranker model ID"
        ),
        sa.Column("llm_id", postgresql.UUID(), nullable=True, comment="default llm model ID"),
        sa.Column(
            "image2text_id", postgresql.UUID(), nullable=True, comment="default image2text model ID"
        ),
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
            sa.String(36),
            nullable=True,
            comment="user-defined external identifier, workspace-unique",
        ),
        sa.Column(
            "builtin_metadata_enabled",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="builtin metadata switch (0: disabled, 1: enabled)",
        ),
        sa.PrimaryKeyConstraint("id", name="knowledges_pkey"),
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
    )
    sa.Index(
        "ix_knowledges_external_id", metadata.tables["knowledges"].c["external_id"], unique=False
    )
    sa.Index("ix_knowledges_id", metadata.tables["knowledges"].c["id"], unique=False)
    sa.Index("ix_knowledges_name", metadata.tables["knowledges"].c["name"], unique=False)
    sa.Index("ix_knowledges_parser_id", metadata.tables["knowledges"].c["parser_id"], unique=False)
    sa.Index("ix_knowledges_status", metadata.tables["knowledges"].c["status"], unique=False)
    sa.Table(
        "documents",
        metadata,
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("kb_id", postgresql.UUID(), nullable=False, comment="knowledges.id"),
        sa.Column("created_by", postgresql.UUID(), nullable=False, comment="users.id"),
        sa.Column("file_id", postgresql.UUID(), nullable=False, comment="files.id"),
        sa.Column("file_name", sa.String(), nullable=False, comment="file name"),
        sa.Column("file_ext", sa.String(), nullable=False, comment="file extension"),
        sa.Column("file_size", sa.Integer(), nullable=True, comment="file size(byte)"),
        sa.Column("file_meta", sa.JSON(), nullable=False),
        sa.Column("parser_id", sa.String(), nullable=False, comment="default parser ID"),
        sa.Column("parser_config", sa.JSON(), nullable=False, comment="default parser config"),
        sa.Column("chunk_num", sa.Integer(), nullable=True, comment="chunk num"),
        sa.Column("progress", postgresql.DOUBLE_PRECISION(), nullable=True),
        sa.Column("progress_msg", sa.String(), nullable=True, comment="process message"),
        sa.Column("process_begin_at", postgresql.TIMESTAMP(precision=6), nullable=True),
        sa.Column("process_duration", postgresql.DOUBLE_PRECISION(), nullable=True),
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
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="{field_name: value}",
        ),
        sa.PrimaryKeyConstraint("id", name="documents_pkey"),
    )
    sa.Index("ix_documents_file_ext", metadata.tables["documents"].c["file_ext"], unique=False)
    sa.Index("ix_documents_file_name", metadata.tables["documents"].c["file_name"], unique=False)
    sa.Index("ix_documents_id", metadata.tables["documents"].c["id"], unique=False)
    sa.Index("ix_documents_parser_id", metadata.tables["documents"].c["parser_id"], unique=False)
    sa.Table(
        "files",
        metadata,
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("kb_id", postgresql.UUID(), nullable=False, comment="knowledges.id"),
        sa.Column("created_by", postgresql.UUID(), nullable=False, comment="users.id"),
        sa.Column("parent_id", postgresql.UUID(), nullable=True, comment="parent folder id"),
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
            sa.String(512),
            nullable=True,
            comment="storage file key for FileStorageService",
        ),
        sa.Column(
            "file_role",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'source'::character varying"),
            comment="source or derived_image",
        ),
        sa.Column(
            "source_document_id",
            postgresql.UUID(),
            nullable=True,
            comment="documents.id for a derived image asset",
        ),
        sa.PrimaryKeyConstraint("id", name="files_pkey"),
    )
    sa.Index("ix_files_file_ext", metadata.tables["files"].c["file_ext"], unique=False)
    sa.Index("ix_files_file_key", metadata.tables["files"].c["file_key"], unique=False)
    sa.Index("ix_files_file_name", metadata.tables["files"].c["file_name"], unique=False)
    sa.Index("ix_files_file_role", metadata.tables["files"].c["file_role"], unique=False)
    sa.Index("ix_files_file_url", metadata.tables["files"].c["file_url"], unique=False)
    sa.Index("ix_files_id", metadata.tables["files"].c["id"], unique=False)
    sa.Index(
        "ix_files_source_document_id",
        metadata.tables["files"].c["source_document_id"],
        unique=False,
    )
    sa.Table(
        "knowledge_metadatas",
        metadata,
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(), nullable=False, comment="租户ID"),
        sa.Column("knowledge_id", postgresql.UUID(), nullable=False, comment="知识库ID"),
        sa.Column("type", sa.String(), nullable=False, comment="字段类型: string | number | time"),
        sa.Column("name", sa.String(255), nullable=False, comment="字段名"),
        sa.Column("created_by", postgresql.UUID(), nullable=True, comment="创建人"),
        sa.Column("updated_by", postgresql.UUID(), nullable=True, comment="更新人"),
        sa.Column("created_at", postgresql.TIMESTAMP(), nullable=True),
        sa.Column("updated_at", postgresql.TIMESTAMP(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="knowledge_metadatas_pkey"),
        sa.UniqueConstraint("knowledge_id", "name", name="uq_knowledge_metadata_name"),
    )
    sa.Index(
        "ix_knowledge_metadatas_id", metadata.tables["knowledge_metadatas"].c["id"], unique=False
    )
    sa.Index(
        "ix_knowledge_metadatas_knowledge_id",
        metadata.tables["knowledge_metadatas"].c["knowledge_id"],
        unique=False,
    )
    sa.Table(
        "knowledge_metadata_bindings",
        metadata,
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(), nullable=False, comment="租户ID"),
        sa.Column("knowledge_id", postgresql.UUID(), nullable=False, comment="知识库ID"),
        sa.Column("metadata_id", postgresql.UUID(), nullable=False, comment="元数据定义ID"),
        sa.Column("document_id", postgresql.UUID(), nullable=False, comment="文档ID"),
        sa.Column("created_by", postgresql.UUID(), nullable=True, comment="创建人"),
        sa.Column("created_at", postgresql.TIMESTAMP(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="knowledge_metadata_bindings_pkey"),
        sa.UniqueConstraint(
            "knowledge_id", "metadata_id", "document_id", name="uq_knowledge_metadata_binding"
        ),
    )
    sa.Index(
        "ix_knowledge_metadata_bindings_id",
        metadata.tables["knowledge_metadata_bindings"].c["id"],
        unique=False,
    )
    sa.Index(
        "ix_knowledge_metadata_bindings_knowledge_id",
        metadata.tables["knowledge_metadata_bindings"].c["knowledge_id"],
        unique=False,
    )
    sa.Table(
        "knowledge_shares",
        metadata,
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column(
            "source_kb_id", postgresql.UUID(), nullable=False, comment="source knowledges.id"
        ),
        sa.Column(
            "source_workspace_id", postgresql.UUID(), nullable=False, comment="source workspaces.id"
        ),
        sa.Column(
            "target_kb_id", postgresql.UUID(), nullable=False, comment="target knowledges.id"
        ),
        sa.Column(
            "target_workspace_id", postgresql.UUID(), nullable=False, comment="target workspaces.id"
        ),
        sa.Column("shared_by", postgresql.UUID(), nullable=False, comment="shared users.id"),
        sa.Column("created_at", postgresql.TIMESTAMP(precision=6), nullable=True),
        sa.Column("updated_at", postgresql.TIMESTAMP(precision=6), nullable=True),
        sa.PrimaryKeyConstraint("id", name="knowledge_shares_pkey"),
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
    )
    sa.Index("ix_knowledge_shares_id", metadata.tables["knowledge_shares"].c["id"], unique=False)
    return metadata
