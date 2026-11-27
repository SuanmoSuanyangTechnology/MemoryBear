"""建立与生产 72e42a8c6d19 一致的知识库基线。"""

from typing import NamedTuple

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "72e42a8c6d19"
down_revision = None
branch_labels = None
depends_on = None


class Column(NamedTuple):
    name: str
    type: str
    not_null: bool
    default: str | None
    comment: str | None


# 固定社区和企业存量库共用的 PostgreSQL 结构，避免未来 ORM 修改历史迁移。
_COLUMNS = {
    "knowledges": (
        Column("id", "uuid", True, None, None),
        Column("workspace_id", "uuid", True, None, "workspaces.id"),
        Column("created_by", "uuid", True, None, "users.id"),
        Column("parent_id", "uuid", False, None, "parent folder id when type is Folder"),
        Column("name", "character varying", True, None, "KB name"),
        Column("description", "character varying", False, None, "KB description"),
        Column("avatar", "character varying", False, None, "avatar url"),
        Column("type", "character varying", False, None, "Type:General|Web|Third-party|Folder"),
        Column(
            "permission_id", "character varying", False, None, "permission ID:Private|Share|Memory"
        ),
        Column("embedding_id", "uuid", False, None, "default embedding model ID"),
        Column("reranker_id", "uuid", False, None, "default reranker model ID"),
        Column("llm_id", "uuid", False, None, "default llm model ID"),
        Column("image2text_id", "uuid", False, None, "default image2text model ID"),
        Column("doc_num", "integer", False, None, "doc num"),
        Column("chunk_num", "integer", False, None, "chunk num"),
        Column("parser_id", "character varying", False, None, "default parser ID"),
        Column("parser_config", "json", True, None, "default parser config"),
        Column(
            "status", "integer", False, None, "is it validate(0: disable, 1: enable, 2:Soft-delete)"
        ),
        Column("created_at", "timestamp(6) without time zone", False, None, None),
        Column("updated_at", "timestamp(6) without time zone", False, None, None),
        Column(
            "external_id",
            "character varying(36)",
            False,
            None,
            "user-defined external identifier, workspace-unique",
        ),
        Column(
            "builtin_metadata_enabled",
            "integer",
            True,
            "0",
            "builtin metadata switch (0: disabled, 1: enabled)",
        ),
        Column("audio2text_id", "uuid", False, None, "audio transcription model ID"),
        Column("video2text_id", "uuid", False, None, "video understanding model ID"),
    ),
    "documents": (
        Column("id", "uuid", True, None, None),
        Column("kb_id", "uuid", True, None, "knowledges.id"),
        Column("created_by", "uuid", True, None, "users.id"),
        Column("file_id", "uuid", True, None, "files.id"),
        Column("file_name", "character varying", True, None, "file name"),
        Column("file_ext", "character varying", True, None, "file extension"),
        Column("file_size", "integer", False, None, "file size(byte)"),
        Column("file_meta", "json", True, None, None),
        Column("parser_id", "character varying", True, None, "default parser ID"),
        Column("parser_config", "json", True, None, "default parser config"),
        Column("chunk_num", "integer", False, None, "chunk num"),
        Column("progress", "double precision", False, None, None),
        Column("progress_msg", "character varying", False, None, "process message"),
        Column("process_begin_at", "timestamp(6) without time zone", False, None, None),
        Column("process_duration", "double precision", False, None, None),
        Column(
            "run",
            "integer",
            False,
            None,
            "start to run processing or cancel.(1: run it; 2: cancel)",
        ),
        Column("status", "integer", False, None, "is it validate(0: wasted, 1: validate)"),
        Column("created_at", "timestamp(6) without time zone", False, None, None),
        Column("updated_at", "timestamp(6) without time zone", False, None, None),
        Column("meta_data", "jsonb", True, "'{}'::jsonb", "{field_name: value}"),
    ),
    "files": (
        Column("id", "uuid", True, None, None),
        Column("kb_id", "uuid", True, None, "knowledges.id"),
        Column("created_by", "uuid", True, None, "users.id"),
        Column("parent_id", "uuid", False, None, "parent folder id"),
        Column(
            "file_name",
            "character varying",
            True,
            None,
            "file name or folder name,default folder name is /",
        ),
        Column("file_ext", "character varying", True, None, "file extension:folder|pdf"),
        Column("file_size", "integer", False, None, "file size(byte)"),
        Column("created_at", "timestamp(6) without time zone", False, None, None),
        Column("file_url", "character varying", False, None, "file comes from a website url"),
        Column(
            "file_key",
            "character varying(512)",
            False,
            None,
            "storage file key for FileStorageService",
        ),
        Column(
            "file_role",
            "character varying(32)",
            True,
            "'source'::character varying",
            "source or derived_image",
        ),
        Column("source_document_id", "uuid", False, None, "documents.id for a derived image asset"),
    ),
    "knowledge_metadatas": (
        Column("id", "uuid", True, None, None),
        Column("tenant_id", "uuid", True, None, "租户ID"),
        Column("knowledge_id", "uuid", True, None, "知识库ID"),
        Column("type", "character varying", True, None, "字段类型: string | number | time"),
        Column("name", "character varying(255)", True, None, "字段名"),
        Column("created_by", "uuid", False, None, "创建人"),
        Column("updated_by", "uuid", False, None, "更新人"),
        Column("created_at", "timestamp without time zone", False, None, None),
        Column("updated_at", "timestamp without time zone", False, None, None),
    ),
    "knowledge_metadata_bindings": (
        Column("id", "uuid", True, None, None),
        Column("tenant_id", "uuid", True, None, "租户ID"),
        Column("knowledge_id", "uuid", True, None, "知识库ID"),
        Column("metadata_id", "uuid", True, None, "元数据定义ID"),
        Column("document_id", "uuid", True, None, "文档ID"),
        Column("created_by", "uuid", False, None, "创建人"),
        Column("created_at", "timestamp without time zone", False, None, None),
    ),
    "knowledge_shares": (
        Column("id", "uuid", True, None, None),
        Column("source_kb_id", "uuid", True, None, "source knowledges.id"),
        Column("source_workspace_id", "uuid", True, None, "source workspaces.id"),
        Column("target_kb_id", "uuid", True, None, "target knowledges.id"),
        Column("target_workspace_id", "uuid", True, None, "target workspaces.id"),
        Column("shared_by", "uuid", True, None, "shared users.id"),
        Column("created_at", "timestamp(6) without time zone", False, None, None),
        Column("updated_at", "timestamp(6) without time zone", False, None, None),
    ),
}
_CONSTRAINTS = {
    ("documents", "documents_pkey"): "PRIMARY KEY (id)",
    ("files", "files_pkey"): "PRIMARY KEY (id)",
    ("knowledge_metadata_bindings", "knowledge_metadata_bindings_pkey"): "PRIMARY KEY (id)",
    ("knowledge_metadata_bindings", "uq_knowledge_metadata_binding"): (
        "UNIQUE (knowledge_id, metadata_id, document_id)"
    ),
    ("knowledge_metadatas", "knowledge_metadatas_pkey"): "PRIMARY KEY (id)",
    ("knowledge_metadatas", "uq_knowledge_metadata_name"): "UNIQUE (knowledge_id, name)",
    ("knowledge_shares", "knowledge_shares_pkey"): "PRIMARY KEY (id)",
    ("knowledge_shares", "knowledge_shares_shared_by_fkey"): (
        "FOREIGN KEY (shared_by) REFERENCES users(id)"
    ),
    ("knowledge_shares", "knowledge_shares_target_kb_id_fkey"): (
        "FOREIGN KEY (target_kb_id) REFERENCES knowledges(id)"
    ),
    ("knowledge_shares", "knowledge_shares_target_workspace_id_fkey"): (
        "FOREIGN KEY (target_workspace_id) REFERENCES workspaces(id)"
    ),
    ("knowledges", "knowledges_audio2text_id_fkey"): (
        "FOREIGN KEY (audio2text_id) REFERENCES model_configs(id) ON DELETE SET NULL"
    ),
    ("knowledges", "knowledges_created_by_fkey"): "FOREIGN KEY (created_by) REFERENCES users(id)",
    ("knowledges", "knowledges_embedding_id_fkey"): (
        "FOREIGN KEY (embedding_id) REFERENCES model_configs(id) ON DELETE SET NULL"
    ),
    ("knowledges", "knowledges_image2text_id_fkey"): (
        "FOREIGN KEY (image2text_id) REFERENCES model_configs(id) ON DELETE SET NULL"
    ),
    ("knowledges", "knowledges_llm_id_fkey"): (
        "FOREIGN KEY (llm_id) REFERENCES model_configs(id) ON DELETE SET NULL"
    ),
    ("knowledges", "knowledges_pkey"): "PRIMARY KEY (id)",
    ("knowledges", "knowledges_reranker_id_fkey"): (
        "FOREIGN KEY (reranker_id) REFERENCES model_configs(id) ON DELETE SET NULL"
    ),
    ("knowledges", "knowledges_video2text_id_fkey"): (
        "FOREIGN KEY (video2text_id) REFERENCES model_configs(id) ON DELETE SET NULL"
    ),
}
_INDEX_COLUMNS = {
    "documents": ("file_ext", "file_name", "id", "parser_id"),
    "files": (
        "file_ext",
        "file_key",
        "file_name",
        "file_role",
        "file_url",
        "id",
        "source_document_id",
    ),
    "knowledge_metadata_bindings": ("id", "knowledge_id"),
    "knowledge_metadatas": ("id", "knowledge_id"),
    "knowledge_shares": ("id",),
    "knowledges": ("external_id", "id", "name", "parser_id", "status"),
}
_TABLE_NAMES = ", ".join(f"'{name}'" for name in _COLUMNS)
_PLATFORM_TABLES = {"users", "workspaces", "model_configs"}
_STATS_FUNCTION = """
CREATE FUNCTION public.update_knowledge_stats()
RETURNS TRIGGER AS $$
DECLARE
    -- 声明变量用于存储当前处理的知识库ID
    current_kb_id UUID;
    -- 声明变量用于存储文件夹知识库ID（如果存在）
    folder_kb_id UUID;
    -- 声明变量用于存储递归查询结果
    folder_ids UUID[];
BEGIN
    -- 处理 documents 表的插入、更新或删除
    IF TG_TABLE_NAME = 'documents' THEN
        -- 1. 更新 knowledges 表的 doc_num
        UPDATE knowledges SET doc_num = (
            SELECT COUNT(*) FROM documents
            WHERE kb_id = knowledges.id AND status = 1
        )
        WHERE id = NEW.kb_id OR id = OLD.kb_id;

        -- 2. 更新 knowledges 表的 chunk_num
        UPDATE knowledges SET chunk_num = (
            SELECT COALESCE(SUM(chunk_num), 0) FROM documents
            WHERE kb_id = knowledges.id AND status = 1
        )
        WHERE id = NEW.kb_id OR id = OLD.kb_id;

        -- 通过 knowledge_shares 表同步统计信息
        -- 1. 使用 source_kb_id 的 doc_num 更新 target_kb_id 的 doc_num
        UPDATE knowledges AS target
        SET doc_num = source.doc_num
        FROM knowledge_shares ks
        JOIN knowledges AS source ON source.id = ks.source_kb_id
        WHERE ks.target_kb_id = target.id
          AND (source.id = NEW.kb_id OR source.id = OLD.kb_id);

        -- 2. 使用 source_kb_id 的 chunk_num 更新 target_kb_id 的 chunk_num
        UPDATE knowledges AS target
        SET chunk_num = source.chunk_num
        FROM knowledge_shares ks
        JOIN knowledges AS source ON source.id = ks.source_kb_id
        WHERE ks.target_kb_id = target.id
          AND (source.id = NEW.kb_id OR source.id = OLD.kb_id);

        -- 处理文件夹知识库的统计更新
        -- 获取当前处理的知识库ID（可能是NEW或OLD中的kb_id）
        IF NEW.kb_id IS NOT NULL THEN
            current_kb_id := NEW.kb_id;
        ELSIF OLD.kb_id IS NOT NULL THEN
            current_kb_id := OLD.kb_id;
        ELSE
            RETURN NULL;
        END IF;

        -- 查找当前知识库的父文件夹（如果有）
        SELECT id INTO folder_kb_id FROM knowledges
        WHERE id IN (
            SELECT parent_id FROM knowledges WHERE id = current_kb_id
        ) AND type = 'Folder';

        -- 如果存在父文件夹，递归处理所有父文件夹
        IF folder_kb_id IS NOT NULL THEN
            -- 使用递归CTE获取所有父文件夹ID（包括多级嵌套）
            WITH RECURSIVE folder_hierarchy AS (
                -- 基础查询：获取直接父文件夹
                SELECT id FROM knowledges
                WHERE id = folder_kb_id AND type = 'Folder'
                UNION ALL
                -- 递归查询：获取父文件夹的父文件夹
                SELECT k.id FROM knowledges k
                JOIN folder_hierarchy fh ON k.id = k.parent_id
                WHERE k.type = 'Folder'
            )
            -- 将结果存入数组以便处理
            SELECT array_agg(id) INTO folder_ids FROM folder_hierarchy;

            -- 遍历所有父文件夹并更新统计信息
            FOR i IN 1..array_length(folder_ids, 1) LOOP
                -- 更新文件夹的doc_num（汇总所有子知识库的doc_num）
                UPDATE knowledges SET doc_num = (
                    -- 汇总直接子知识库的doc_num
                    SELECT COALESCE(SUM(child.doc_num), 0)
                    FROM knowledges child
                    WHERE child.parent_id = folder_ids[i] AND child.status = 1
                    -- 加上直接属于该文件夹的文档数（如果有）
                    UNION ALL
                    SELECT COALESCE(COUNT(*), 0)
                    FROM documents
                    WHERE kb_id = folder_ids[i] AND status = 1
                    LIMIT 1
                )
                WHERE id = folder_ids[i];

                -- 更新文件夹的chunk_num（汇总所有子知识库的chunk_num）
                UPDATE knowledges SET chunk_num = (
                    -- 汇总直接子知识库的chunk_num
                    SELECT COALESCE(SUM(child.chunk_num), 0)
                    FROM knowledges child
                    WHERE child.parent_id = folder_ids[i] AND child.status = 1
                    -- 加上直接属于该文件夹的文档的chunk_num（如果有）
                    UNION ALL
                    SELECT COALESCE(SUM(d.chunk_num), 0)
                    FROM documents d
                    WHERE d.kb_id = folder_ids[i] AND d.status = 1
                    LIMIT 1
                )
                WHERE id = folder_ids[i];
            END LOOP;
        END IF;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""
_STATS_TRIGGER = (
    "CREATE TRIGGER tr_documents_update_stats AFTER INSERT OR DELETE OR UPDATE ON public.documents "
    "FOR EACH ROW EXECUTE FUNCTION update_knowledge_stats()"
)
# 已发布脚本的函数体只存在空白行差异，按非空行校验并保留数据库中的函数。
_STATS_BODY_HASH = "b558a921bb2bd805740765d76b6a198a"


def _secondary_indexes() -> dict[tuple[str, str], str]:
    indexes = {}
    for table, columns in _INDEX_COLUMNS.items():
        for column in columns:
            name = f"ix_{table}_{column}"
            indexes[table, name] = f"CREATE INDEX {name} ON public.{table} USING btree ({column})"
    return indexes


def _create_tables(connection: sa.Connection) -> None:
    for table, columns in _COLUMNS.items():
        declarations = []
        for column in columns:
            declaration = f"{column.name} {column.type}"
            if column.default is not None:
                declaration += f" DEFAULT {column.default}"
            if column.not_null:
                declaration += " NOT NULL"
            declarations.append(declaration)
        declarations.extend(
            f"CONSTRAINT {name} {definition}"
            for (owner, name), definition in _CONSTRAINTS.items()
            if owner == table
        )
        connection.exec_driver_sql(f"CREATE TABLE public.{table} ({', '.join(declarations)})")
        for column in columns:
            if column.comment is not None:
                comment = column.comment.replace("'", "''")
                connection.exec_driver_sql(
                    f"COMMENT ON COLUMN public.{table}.{column.name} IS '{comment}'"
                )
    for definition in _secondary_indexes().values():
        connection.exec_driver_sql(definition)
    connection.exec_driver_sql(_STATS_FUNCTION)
    connection.exec_driver_sql(_STATS_TRIGGER)


def _compare(expected: dict, actual: dict, kind: str) -> None:
    missing = sorted(expected.keys() - actual.keys())
    extra = sorted(actual.keys() - expected.keys())
    changed = sorted(key for key in expected.keys() & actual.keys() if expected[key] != actual[key])
    if missing or extra or changed:
        raise RuntimeError(
            f"知识库接管基线的{kind}不匹配。缺少={missing}，多出={extra}，属性不同={changed}"
        )


def verify_owned_tables(connection: sa.Connection) -> None:
    """校验六张基础表和文档统计触发器。"""
    columns = connection.exec_driver_sql(f"""
        SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod),
               a.attnotnull, pg_get_expr(d.adbin, d.adrelid)
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
        WHERE n.nspname = 'public' AND c.relname IN ({_TABLE_NAMES})
          AND a.attnum > 0 AND NOT a.attisdropped
    """)
    # timestamp 和 timestamp(6) 都使用 PostgreSQL 默认的微秒精度。
    _compare(
        {
            (table, col.name): (
                col.type.replace("timestamp(6)", "timestamp"),
                col.not_null,
                col.default,
            )
            for table, definitions in _COLUMNS.items()
            for col in definitions
        },
        {
            (table, name): (type_.replace("timestamp(6)", "timestamp"), required, default)
            for table, name, type_, required, default in columns
        },
        "列",
    )
    constraints = connection.exec_driver_sql(f"""
        SELECT c.relname, x.conname, pg_get_constraintdef(x.oid)
        FROM pg_constraint x
        JOIN pg_class c ON c.oid = x.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname IN ({_TABLE_NAMES})
          AND x.convalidated AND x.contype <> 'n'
    """)
    _compare(
        _CONSTRAINTS, {(table, name): definition for table, name, definition in constraints}, "约束"
    )
    indexes = connection.exec_driver_sql(f"""
        SELECT c.relname, idx.relname, pg_get_indexdef(idx.oid)
        FROM pg_index i
        JOIN pg_class idx ON idx.oid = i.indexrelid
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname IN ({_TABLE_NAMES})
          AND i.indisready AND i.indisvalid
    """)
    expected_indexes = _secondary_indexes()
    for (table, name), constraint in _CONSTRAINTS.items():
        if constraint.startswith(("PRIMARY KEY (", "UNIQUE (")):
            columns = constraint[constraint.index("(") :]
            expected_indexes[table, name] = (
                f"CREATE UNIQUE INDEX {name} ON public.{table} USING btree {columns}"
            )
    _compare(
        expected_indexes, {(table, name): definition for table, name, definition in indexes}, "索引"
    )
    triggers = connection.exec_driver_sql(f"""
        SELECT c.relname, t.tgname, t.tgenabled::text, pg_get_triggerdef(t.oid),
               md5((
                   SELECT string_agg(line, chr(10) ORDER BY line_number)
                   FROM string_to_table(p.prosrc, chr(10))
                       WITH ORDINALITY AS lines(line, line_number)
                   WHERE btrim(line, E' \\t\\r') <> ''
               ))
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE n.nspname = 'public' AND c.relname IN ({_TABLE_NAMES}) AND NOT t.tgisinternal
    """)
    _compare(
        {("documents", "tr_documents_update_stats"): ("O", _STATS_TRIGGER, _STATS_BODY_HASH)},
        {
            (table, name): (enabled, definition, body_hash)
            for table, name, enabled, definition, body_hash in triggers
        },
        "触发器",
    )


def _adopt_legacy_knowledge_tables() -> None:
    if op.get_context().as_sql:
        raise RuntimeError("知识库表接管需要在线核对 PostgreSQL 结构")
    connection = op.get_bind()
    if connection.exec_driver_sql("SELECT current_schema()").scalar_one() != "public":
        raise RuntimeError("知识库表接管要求默认 schema 为 public")
    present = set(sa.inspect(connection).get_table_names(schema="public"))
    missing_platform = _PLATFORM_TABLES - present
    if missing_platform:
        raise RuntimeError(f"请先初始化平台基础表：{sorted(missing_platform)}")
    existing = present & _COLUMNS.keys()
    if existing and existing != _COLUMNS.keys():
        raise RuntimeError(f"知识库表部分存在，缺少：{sorted(_COLUMNS.keys() - existing)}")
    if not existing:
        _create_tables(connection)
    verify_owned_tables(connection)


def _create_wiki_navigation() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "wiki_document_states",
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("requested_version", sa.BigInteger(), nullable=False),
        sa.Column("mapped_version", sa.BigInteger(), nullable=False),
        sa.Column("source_ready", sa.Boolean(), nullable=False),
        sa.Column("plan_ready", sa.Boolean(), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("processing_mode", sa.String(length=16), nullable=False),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("source_task_id", sa.String(length=64), nullable=True),
        sa.Column("admission_id", sa.String(length=64), nullable=True),
        sa.Column("publish_confirmed", sa.Boolean(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("config_hash", sa.String(length=64), nullable=True),
        sa.Column("map_progress", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("failure_attempt", sa.Integer(), nullable=False),
        sa.Column("defer_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_stage", sa.String(length=32), nullable=True),
        sa.Column("last_progress", sa.Float(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("kb_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "requested_version > 0 AND mapped_version >= 0", name="ck_wiki_document_version"
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledges.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("document_id"),
        sa.UniqueConstraint(
            "tenant_id", "workspace_id", "kb_id", "document_id", name="uq_wiki_document_scope"
        ),
    )
    op.create_index(
        "ix_wiki_document_admission",
        "wiki_document_states",
        ["kb_id", "stage", "status", "document_id"],
        unique=False,
    )
    op.create_index(
        "ix_wiki_document_handoff",
        "wiki_document_states",
        ["admission_id", "publish_confirmed"],
        unique=False,
    )
    op.create_table(
        "wiki_folders",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("kb_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledges.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "kb_id", "parent_id"],
            [
                "wiki_folders.tenant_id",
                "wiki_folders.workspace_id",
                "wiki_folders.kb_id",
                "wiki_folders.id",
            ],
            name="fk_wiki_folder_parent",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "workspace_id", "kb_id", "id", name="uq_wiki_folder_scope"
        ),
    )
    op.create_index(
        "uq_wiki_folder_child",
        "wiki_folders",
        ["tenant_id", "workspace_id", "kb_id", "parent_id", "normalized_name"],
        unique=True,
        postgresql_where=sa.text("parent_id IS NOT NULL"),
    )
    op.create_index(
        "uq_wiki_folder_root",
        "wiki_folders",
        ["tenant_id", "workspace_id", "kb_id", "normalized_name"],
        unique=True,
        postgresql_where=sa.text("parent_id IS NULL"),
    )
    op.create_table(
        "wiki_pages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("page_type", sa.String(length=16), nullable=False),
        sa.Column("canonical_key", sa.String(length=80), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.String(length=400), nullable=False),
        sa.Column("topic_scope", sa.Text(), nullable=False),
        sa.Column("aliases", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("folder_id", sa.UUID(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("published_sources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("published_content_hash", sa.String(length=64), nullable=True),
        sa.Column("defer_started_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("row_version", sa.BigInteger(), nullable=False),
        sa.Column("failure_attempt", sa.Integer(), nullable=False),
        sa.Column("defer_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("kb_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "page_type IN ('summary', 'entity', 'concept')", name="ck_wiki_page_type"
        ),
        sa.CheckConstraint("version >= 0 AND row_version > 0", name="ck_wiki_page_version"),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledges.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "kb_id", "folder_id"],
            [
                "wiki_folders.tenant_id",
                "wiki_folders.workspace_id",
                "wiki_folders.kb_id",
                "wiki_folders.id",
            ],
            name="fk_wiki_page_folder",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kb_id", "canonical_key", name="uq_wiki_page_key"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "kb_id", "id", name="uq_wiki_page_scope"),
    )
    op.create_index(
        "ix_wiki_page_browse", "wiki_pages", ["kb_id", "status", "page_type", "id"], unique=False
    )
    op.create_index(
        "ix_wiki_page_identity",
        "wiki_pages",
        ["kb_id", "normalized_name", "page_type"],
        unique=False,
    )
    op.create_table(
        "wiki_document_page_tasks",
        sa.Column("subtask_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("requested_version", sa.BigInteger(), nullable=False),
        sa.Column("page_id", sa.UUID(), nullable=False),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("failure_attempt", sa.Integer(), nullable=False),
        sa.Column("defer_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_stage", sa.String(length=32), nullable=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("kb_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("operation IN ('upsert', 'retract')", name="ck_wiki_task_operation"),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledges.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "kb_id", "document_id"],
            [
                "wiki_document_states.tenant_id",
                "wiki_document_states.workspace_id",
                "wiki_document_states.kb_id",
                "wiki_document_states.document_id",
            ],
            name="fk_wiki_task_document",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "kb_id", "page_id"],
            [
                "wiki_pages.tenant_id",
                "wiki_pages.workspace_id",
                "wiki_pages.kb_id",
                "wiki_pages.id",
            ],
            name="fk_wiki_task_page",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("subtask_id"),
        sa.UniqueConstraint(
            "document_id", "requested_version", "page_id", name="uq_wiki_document_page_task"
        ),
    )
    op.create_index(
        "ix_wiki_task_document",
        "wiki_document_page_tasks",
        ["document_id", "requested_version", "status"],
        unique=False,
    )
    op.create_index(
        "ix_wiki_task_page",
        "wiki_document_page_tasks",
        ["kb_id", "page_id", "status"],
        unique=False,
    )
    op.create_table(
        "wiki_page_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source_page_id", sa.UUID(), nullable=False),
        sa.Column("target_page_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("kb_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("source_page_id <> target_page_id", name="ck_wiki_link_not_self"),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledges.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "kb_id", "source_page_id"],
            [
                "wiki_pages.tenant_id",
                "wiki_pages.workspace_id",
                "wiki_pages.kb_id",
                "wiki_pages.id",
            ],
            name="fk_wiki_link_source",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "kb_id", "target_page_id"],
            [
                "wiki_pages.tenant_id",
                "wiki_pages.workspace_id",
                "wiki_pages.kb_id",
                "wiki_pages.id",
            ],
            name="fk_wiki_link_target",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kb_id", "source_page_id", "target_page_id", name="uq_wiki_link"),
    )
    op.create_index(
        "ix_wiki_link_reverse", "wiki_page_links", ["kb_id", "target_page_id"], unique=False
    )
    op.create_table(
        "wiki_page_sources",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("page_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("document_wiki_version", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("facts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("chunk_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("kb_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledges.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "kb_id", "document_id"],
            [
                "wiki_document_states.tenant_id",
                "wiki_document_states.workspace_id",
                "wiki_document_states.kb_id",
                "wiki_document_states.document_id",
            ],
            name="fk_wiki_source_document",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "kb_id", "page_id"],
            [
                "wiki_pages.tenant_id",
                "wiki_pages.workspace_id",
                "wiki_pages.kb_id",
                "wiki_pages.id",
            ],
            name="fk_wiki_source_page",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kb_id", "page_id", "document_id", name="uq_wiki_source"),
    )
    op.create_index(
        "ix_wiki_source_document", "wiki_page_sources", ["document_id", "page_id"], unique=False
    )


def _create_wiki_faq_tables() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "wiki_page_versions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("wiki_page_id", sa.UUID(), nullable=False),
        sa.Column("page_version", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("page_type", sa.String(length=16), nullable=False),
        sa.Column("aliases", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("published_sources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("kb_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("page_version > 0", name="ck_wiki_page_version_positive"),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledges.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "kb_id", "wiki_page_id"],
            [
                "wiki_pages.tenant_id",
                "wiki_pages.workspace_id",
                "wiki_pages.kb_id",
                "wiki_pages.id",
            ],
            name="fk_wiki_page_version_page",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "workspace_id", "kb_id", "id", name="uq_wiki_page_version_scope"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "kb_id",
            "wiki_page_id",
            "page_version",
            name="uq_wiki_page_version_number",
        ),
    )
    op.create_table(
        "wiki_faq_generation_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("wiki_page_id", sa.UUID(), nullable=False),
        sa.Column("trigger_kind", sa.String(length=24), nullable=False),
        sa.Column("source_snapshot_id", sa.UUID(), nullable=False),
        sa.Column("config_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("publish_confirmed", sa.Boolean(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("execution_token", sa.UUID(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("failure_attempt", sa.Integer(), nullable=False),
        sa.Column("dispatch_attempt", sa.Integer(), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_stage", sa.String(length=80), nullable=True),
        sa.Column("row_version", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("kb_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','running','retry_wait','succeeded','failed','cancelled')",
            name="ck_wiki_faq_job_status",
        ),
        sa.CheckConstraint("trigger_kind = 'first_publish'", name="ck_wiki_faq_job_trigger"),
        sa.CheckConstraint(
            "failure_attempt >= 0 AND dispatch_attempt >= 0 AND row_version > 0",
            name="ck_wiki_faq_job_counters",
        ),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledges.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "kb_id", "source_snapshot_id"],
            [
                "wiki_page_versions.tenant_id",
                "wiki_page_versions.workspace_id",
                "wiki_page_versions.kb_id",
                "wiki_page_versions.id",
            ],
            name="fk_wiki_faq_job_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "kb_id", "wiki_page_id"],
            [
                "wiki_pages.tenant_id",
                "wiki_pages.workspace_id",
                "wiki_pages.kb_id",
                "wiki_pages.id",
            ],
            name="fk_wiki_faq_job_page",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "workspace_id", "kb_id", "id", name="uq_wiki_faq_job_scope"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "kb_id",
            "wiki_page_id",
            "trigger_kind",
            name="uq_wiki_faq_first_publish",
        ),
    )
    op.create_index(
        "ix_wiki_faq_job_dispatch",
        "wiki_faq_generation_jobs",
        ["kb_id", "status", "publish_confirmed"],
        unique=False,
    )
    op.create_table(
        "wiki_faqs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("wiki_page_id", sa.UUID(), nullable=True),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("generation_job_id", sa.UUID(), nullable=True),
        sa.Column("generated_wiki_snapshot_id", sa.UUID(), nullable=True),
        sa.Column("standard_question", sa.Text(), nullable=False),
        sa.Column("similar_questions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("negative_questions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("answers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("answer_strategy", sa.String(length=16), nullable=False),
        sa.Column("content_revision", sa.BigInteger(), nullable=False),
        sa.Column("review_state", sa.String(length=16), nullable=False),
        sa.Column("approved_wiki_snapshot_id", sa.UUID(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("row_version", sa.BigInteger(), nullable=False),
        sa.Column("reviewed_by", sa.UUID(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("last_review_request_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("kb_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("answer_strategy IN ('all','random')", name="ck_wiki_faq_strategy"),
        sa.CheckConstraint(
            "jsonb_typeof(answers) = 'array' AND jsonb_array_length(answers) > 0",
            name="ck_wiki_faq_answers",
        ),
        sa.CheckConstraint("origin IN ('auto','manual')", name="ck_wiki_faq_origin"),
        sa.CheckConstraint(
            "review_state != 'pending' OR NOT is_enabled", name="ck_wiki_faq_pending_disabled"
        ),
        sa.CheckConstraint("review_state IN ('pending','approved')", name="ck_wiki_faq_review"),
        sa.CheckConstraint("content_revision > 0 AND row_version > 0", name="ck_wiki_faq_revision"),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledges.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "kb_id", "approved_wiki_snapshot_id"],
            [
                "wiki_page_versions.tenant_id",
                "wiki_page_versions.workspace_id",
                "wiki_page_versions.kb_id",
                "wiki_page_versions.id",
            ],
            name="fk_wiki_faq_approved_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "kb_id", "generated_wiki_snapshot_id"],
            [
                "wiki_page_versions.tenant_id",
                "wiki_page_versions.workspace_id",
                "wiki_page_versions.kb_id",
                "wiki_page_versions.id",
            ],
            name="fk_wiki_faq_generated_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "kb_id", "generation_job_id"],
            [
                "wiki_faq_generation_jobs.tenant_id",
                "wiki_faq_generation_jobs.workspace_id",
                "wiki_faq_generation_jobs.kb_id",
                "wiki_faq_generation_jobs.id",
            ],
            name="fk_wiki_faq_job",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "kb_id", "wiki_page_id"],
            [
                "wiki_pages.tenant_id",
                "wiki_pages.workspace_id",
                "wiki_pages.kb_id",
                "wiki_pages.id",
            ],
            name="fk_wiki_faq_page",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "kb_id", "id", name="uq_wiki_faq_scope"),
    )
    op.create_index(
        "ix_wiki_faq_list", "wiki_faqs", ["kb_id", "deleted_at", "updated_at", "id"], unique=False
    )
    op.create_index("ix_wiki_faq_page", "wiki_faqs", ["wiki_page_id"], unique=False)


def _create_upload_fingerprints() -> None:
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


def _create_faq_import_jobs() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('wiki_faq_import_jobs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('actor_id', sa.UUID(), nullable=False),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('stage', sa.String(length=16), nullable=False),
    sa.Column('payload_key', sa.Text(), nullable=False),
    sa.Column('total_count', sa.BigInteger(), nullable=False),
    sa.Column('processed_count', sa.BigInteger(), nullable=False),
    sa.Column('candidates', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('failed_entries', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('cleanup_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('summary', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('failure', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('model_key', sa.String(length=64), nullable=True),
    sa.Column('execution_token', sa.UUID(), nullable=True),
    sa.Column('lease_expires_at', sa.DateTime(), nullable=True),
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('kb_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("mode IN ('append','replace')", name='ck_faq_import_mode'),
    sa.CheckConstraint("stage IN ('queued','validating','indexing','writing','finished')", name='ck_faq_import_stage'),
    sa.CheckConstraint("status IN ('pending','running','succeeded','partially_succeeded','failed')", name='ck_faq_import_status'),
    sa.CheckConstraint('total_count > 0 AND processed_count >= 0 AND processed_count <= total_count', name='ck_faq_import_counts'),
    sa.ForeignKeyConstraint(['kb_id'], ['knowledges.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )


def _add_wiki_ontology_metadata() -> None:
    for table in ("wiki_pages", "wiki_page_versions"):
        op.add_column(table, sa.Column("semantic_type", sa.String(32), nullable=True))
        op.add_column(table, sa.Column("ontology_version", sa.String(32), nullable=True))
    op.add_column(
        "wiki_page_versions", sa.Column("type_evidence", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "wiki_document_page_tasks", sa.Column("planned_ontology", postgresql.JSONB(), nullable=True)
    )


def _index_faq_import_job_scope_status() -> None:
    op.create_index(
        "ix_wiki_faq_import_job_scope_status",
        "wiki_faq_import_jobs",
        ["kb_id", "tenant_id", "workspace_id", "status"],
    )


def upgrade() -> None:
    """按生产基线的顺序初始化表、列和索引。"""
    _adopt_legacy_knowledge_tables()
    _create_wiki_navigation()
    _create_wiki_faq_tables()
    _create_upload_fingerprints()
    _create_faq_import_jobs()
    _add_wiki_ontology_metadata()
    _index_faq_import_job_scope_status()


def downgrade() -> None:
    raise RuntimeError("生产知识库基线不可降级，避免删除已有数据")
