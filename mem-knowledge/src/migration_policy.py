"""Only newly introduced Wiki tables belong to the knowledge migration chain."""

WIKI_TABLE_NAMES = frozenset(
    {
        "wiki_page_versions",
        "wiki_faqs",
        "wiki_faq_generation_jobs",
        "wiki_folders",
        "wiki_pages",
        "wiki_page_sources",
        "wiki_page_links",
        "wiki_document_states",
        "wiki_document_page_tasks",
    }
)
VERSION_TABLE = "alembic_version_knowledge"


def include_name(name, type_, parent_names):
    """Avoid reflecting unrelated services and the API-owned knowledge tables."""
    if type_ == "table":
        return name in WIKI_TABLE_NAMES
    return True


def include_object(obj, name, type_, reflected, compare_to):
    """Retain reference metadata for FKs without generating its DDL."""
    if type_ == "table":
        return name in WIKI_TABLE_NAMES
    table = getattr(obj, "table", None)
    return table is None or table.name in WIKI_TABLE_NAMES
