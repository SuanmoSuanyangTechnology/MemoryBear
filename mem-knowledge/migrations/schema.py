"""Knowledge migration ownership, future targets, and immutable adoption checks."""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.dialects import postgresql

from .baseline import build_metadata

OWNED_TABLES = frozenset(
    {
        "knowledges",
        "documents",
        "files",
        "knowledge_metadatas",
        "knowledge_metadata_bindings",
        "knowledge_shares",
    }
)
BASELINE_REVISION = "kb_20260915_base"
MEDIA_REVISION = "kb_20260915_media"
VERSION_TABLE = "alembic_version_knowledge"


def include_name(name, type_, parent_names):
    """Restrict reflection before unrelated tables are loaded."""
    if type_ == "schema":
        return name in (None, "public")
    if type_ == "table":
        return name in OWNED_TABLES and parent_names.get("schema_name") in (None, "public")
    return True


def include_object(object_, name, type_, reflected, compare_to):
    table = object_ if type_ == "table" else getattr(object_, "table", None)
    return table is None or (table.name in OWNED_TABLES and table.schema in (None, "public"))


def metadata_at_revision(revision: str) -> sa.MetaData:
    """Return fresh historical metadata, independent of future ORM changes."""
    if revision not in (BASELINE_REVISION, MEDIA_REVISION):
        raise ValueError(f"Unsupported adoption revision: {revision}")
    metadata = build_metadata()
    if revision == MEDIA_REVISION:
        for name, comment in (
            ("audio2text_id", "audio transcription model ID"),
            ("video2text_id", "video understanding model ID"),
        ):
            metadata.tables["knowledges"].append_column(
                sa.Column(
                    name,
                    postgresql.UUID(),
                    sa.ForeignKey(
                        "model_configs.id", name=f"knowledges_{name}_fkey", ondelete="SET NULL"
                    ),
                    nullable=True,
                    comment=comment,
                )
            )
    return metadata


def build_target_metadata() -> sa.MetaData:
    """Clone current owned models while retaining audited legacy annotations/FKs."""
    from src.db import KnowledgeBase
    from src.models import owned  # noqa: F401 - register writable tables only

    metadata = sa.MetaData()
    historical = metadata_at_revision(MEDIA_REVISION)
    for name in ("users", "model_configs", "workspaces"):
        historical.tables[name].to_metadata(metadata)
    for name in sorted(OWNED_TABLES):
        KnowledgeBase.metadata.tables[name].to_metadata(metadata)
    for name in OWNED_TABLES:
        table = metadata.tables[name]
        legacy = historical.tables[name]
        table.comment = legacy.comment
        if table.primary_key.name is None:
            table.primary_key.name = legacy.primary_key.name
        for column in table.c:
            if column.name not in legacy.c:
                continue
            old = legacy.c[column.name]
            # Preserve observed comments and explicit historical timestamp precision.
            column.comment = old.comment
            if type(column.type) is sa.DateTime and isinstance(old.type, postgresql.TIMESTAMP):
                column.type = old.type.copy()
        existing = {tuple(fk.column_keys) for fk in table.foreign_key_constraints}
        for fk in legacy.foreign_key_constraints:
            if tuple(fk.column_keys) not in existing:
                table.append_constraint(
                    sa.ForeignKeyConstraint(
                        fk.column_keys,
                        [element.target_fullname for element in fk.elements],
                        name=fk.name,
                        ondelete=fk.ondelete,
                        onupdate=fk.onupdate,
                    )
                )
    return metadata


def _normalized_check(sql):
    return re.sub(r"\s+", " ", str(sql).strip()).strip("() ")


def schema_differences(connection: sa.Connection, revision: str) -> list[str]:
    """Inspect schema only; adoption never reads business rows or repairs drift."""
    expected = metadata_at_revision(revision)
    inspector = sa.inspect(connection)
    present = set(inspector.get_table_names(schema="public"))
    differences = [f"{name}: missing owned table" for name in sorted(OWNED_TABLES - present)]
    unvalidated = connection.execute(
        sa.text(
            "SELECT t.relname, c.conname FROM pg_constraint c "
            "JOIN pg_class t ON t.oid = c.conrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname = 'public' AND c.contype IN ('f', 'c') "
            "AND NOT c.convalidated"
        )
    )
    for table_name, constraint_name in unvalidated:
        if table_name in OWNED_TABLES:
            differences.append(f"{table_name}.{constraint_name}: constraint is not validated")
    invalid_indexes = connection.execute(
        sa.text(
            "SELECT t.relname, i.relname FROM pg_index x "
            "JOIN pg_class t ON t.oid = x.indrelid "
            "JOIN pg_class i ON i.oid = x.indexrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname = 'public' AND (NOT x.indisvalid OR NOT x.indisready)"
        )
    )
    for table_name, index_name in invalid_indexes:
        if table_name in OWNED_TABLES:
            differences.append(f"{table_name}.{index_name}: index is not valid or ready")
    context = MigrationContext.configure(
        connection,
        opts={
            "target_metadata": expected,
            "include_name": include_name,
            "include_object": include_object,
            "include_schemas": False,
            "compare_type": True,
            "compare_server_default": True,
            "version_table": VERSION_TABLE,
            "version_table_schema": "public",
        },
    )
    for diff in compare_metadata(context, expected):
        # Constraint repr omits its name; keep actionable schema identifiers.
        objects = diff if isinstance(diff, tuple) else ()
        labels = [
            f"{getattr(getattr(item, 'table', None), 'name', '')}.{item.name}"
            for item in objects
            if getattr(item, "name", None)
        ]
        differences.append(f"{', '.join(labels)}: {diff}")
    for name in sorted(OWNED_TABLES & present):
        table = expected.tables[name]
        pk = inspector.get_pk_constraint(name, schema="public")
        if tuple(pk["constrained_columns"]) != tuple(table.primary_key.columns.keys()):
            differences.append(f"{name}: primary key columns differ")
        checks = {
            (_normalized_check(item["sqltext"]), item.get("name"))
            for item in inspector.get_check_constraints(name, schema="public")
        }
        wanted = {
            (_normalized_check(item.sqltext), item.name)
            for item in table.constraints
            if isinstance(item, sa.CheckConstraint)
        }
        if checks != wanted:
            differences.append(
                f"{name}: check constraints differ: actual={sorted(checks)!r}, "
                f"expected={sorted(wanted)!r}"
            )
        # Alembic's ordinary index comparison can miss predicates, INCLUDE,
        # access methods, and PostgreSQL's NULLS NOT DISTINCT semantics.
        actual_indexes = {
            item["name"]: item
            for item in inspector.get_indexes(name, schema="public")
            if not item.get("duplicates_constraint")
        }
        for index in table.indexes:
            actual = actual_indexes.get(index.name)
            if actual is None:
                continue  # Already reported by Alembic.
            options = actual.get("dialect_options", {})
            desired = index.dialect_options["postgresql"]
            actual_signature = (
                tuple(actual.get("column_names", [])),
                actual["unique"],
                tuple(options.get("postgresql_include", [])),
                str(options.get("postgresql_where", "")),
                options.get("postgresql_using", "btree"),
                bool(options.get("postgresql_nulls_not_distinct", False)),
                options.get("postgresql_ops", {}),
            )
            desired_signature = (
                tuple(column.name for column in index.columns),
                index.unique,
                tuple(desired.get("include") or []),
                str(desired.get("where") if desired.get("where") is not None else ""),
                desired.get("using") or "btree",
                bool(desired.get("nulls_not_distinct", False)),
                desired.get("ops") or {},
            )
            if actual_signature != desired_signature:
                differences.append(f"{name}.{index.name}: index definition differs")
        # Alembic does not compare PostgreSQL timestamp precision consistently.
        for column in inspector.get_columns(name, schema="public"):
            if column["name"] not in table.c:
                continue
            target = table.c[column["name"]]
            if column.get("computed") or column.get("identity"):
                differences.append(f"{name}.{column['name']}: unexpected generated column")
            if isinstance(target.type, postgresql.TIMESTAMP) and isinstance(
                column["type"], postgresql.TIMESTAMP
            ):
                if (target.type.precision, target.type.timezone) != (
                    column["type"].precision,
                    column["type"].timezone,
                ):
                    differences.append(
                        f"{name}.{column['name']}: timestamp precision/timezone differ"
                    )
    return differences
