"""Only the generic file tables belong to the file-service migration chain."""

FILE_TABLE_NAMES = frozenset(
    {
        "file_metadata",
        "generic_files",
    }
)
VERSION_TABLE = "alembic_version_file_service"


def include_name(name, type_, parent_names):
    """Avoid reflecting unrelated services and monolith-owned tables."""
    if type_ == "table":
        return name in FILE_TABLE_NAMES
    return True


def include_object(obj, name, type_, reflected, compare_to):
    """Retain reference metadata for FKs without generating its DDL."""
    if type_ == "table":
        return name in FILE_TABLE_NAMES
    table = getattr(obj, "table", None)
    return table is None or table.name in FILE_TABLE_NAMES
