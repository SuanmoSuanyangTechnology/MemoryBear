"""知识库迁移链拥有的表及 Alembic 自动生成范围。"""

WIKI_TABLE_NAMES = frozenset(
    {
        "wiki_page_versions",
        "wiki_faqs",
        "wiki_faq_generation_jobs",
        "wiki_folders",
        "wiki_pages",
        "wiki_page_sources",
        "wiki_page_links",
        "wiki_reduce_deliveries",
        "wiki_document_states",
        "wiki_document_page_tasks",
    }
)
LEGACY_KNOWLEDGE_TABLE_NAMES = frozenset(
    {
        "knowledges",
        "documents",
        "files",
        "knowledge_metadatas",
        "knowledge_metadata_bindings",
        "knowledge_shares",
    }
)
KNOWLEDGE_TABLE_NAMES = (
    WIKI_TABLE_NAMES
    | LEGACY_KNOWLEDGE_TABLE_NAMES
    | {
        "knowledge_upload_fingerprints", "wiki_faq_import_jobs",
        "knowledge_parser_configs", "document_parser_configs",
    }
)

VERSION_TABLE = "alembic_version_knowledge"


def include_name(name, type_, parent_names):
    """只反射知识库自身管理的表。"""
    if type_ == "table":
        return name in KNOWLEDGE_TABLE_NAMES
    return True


def include_object(obj, name, type_, reflected, compare_to):
    """Retain reference metadata for FKs without generating its DDL."""
    if type_ == "table":
        return name in KNOWLEDGE_TABLE_NAMES
    table = getattr(obj, "table", None)
    return table is None or table.name in KNOWLEDGE_TABLE_NAMES
