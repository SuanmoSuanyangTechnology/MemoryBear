"""Reject old configuration storage access, while preserving wire/runtime dictionaries.

This finite AST guard is not a Python data-flow or arbitrary-reflection proof. Exceptions
identify a file, qualified function and expression; no business package is exempted.
"""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

SNAPSHOT = "legacy_parser_config_snapshot"
STORAGE_NAMES = {"parser_config", SNAPSHOT}

# These are request DTOs or detached runtime snapshots, never owned ORM objects.
# Only loads of the named receiver in the named function are allowed.
RUNTIME_READS = {
    ("api/routes/knowledge.py", "dispatch_knowledge_update", "outcome"),
    ("api/routes/knowledge.py", "get_knowledge_graph", "snapshot"),
    ("services/chunk.py", "preview_document", "snapshot"),
    ("services/document.py", "create_document", "create_data"),
    ("services/document.py", "delete_document_resources", "snapshot"),
    ("services/document_processing.py", "build_document_vision_model", "snapshot"),
    ("services/document_processing.py", "_preflight_document", "snapshot"),
    ("services/document_processing.py", "parse_document_chunks", "snapshot"),
    ("services/document_processing.py", "process_document", "snapshot"),
    ("services/file.py", "persist_uploaded_content", "plan"),
    ("services/graph.py", "get_graph", "knowledge"),
    ("services/graph.py", "rebuild_graph", "knowledge"),
    ("services/knowledge.py", "create_knowledge", "knowledge"),
    ("services/knowledge.py", "prepare_knowledge_update", "update_data"),
    ("rag/chunk/merger/block.py", "BlockMerger.merge", "ctx"),
    ("rag/chunk/merger/block.py", "BlockMerger._merge_structured_stream", "ctx"),
    ("rag/chunk/merger/naive.py", "NaiveMerger.merge", "ctx"),
    ("rag/chunk/merger/naive.py", "DocxMerger.merge", "ctx"),
    ("rag/chunk/merger/naive.py", "ImageMerger.merge", "ctx"),
    ("rag/chunk/parser/json.py", "JsonParser.parse", "ctx"),
    ("rag/chunk/parser/mineru_v3.py", "MinerUV3Parser.parse", "ctx"),
    ("rag/chunk/pipeline/base.py", "ChunkPipeline.enhance_merged_images", "ctx"),
    ("rag/chunk/pipeline/document.py", "DocxChunkPipeline.collect_docx_hyperlink_chunks", "ctx"),
    ("rag/chunk/pipeline/image.py", "ImageChunkPipeline.parse", "ctx"),
    ("rag/chunk/pipeline/image.py", "ImageChunkPipeline._image_vision_mode", "ctx"),
    ("rag/chunk/pipeline/image.py", "ImageChunkPipeline._parse_ocr_blocks", "ctx"),
    ("rag/chunk/pipeline/pdf.py", "PdfChunkPipeline.parse", "ctx"),
    ("rag/chunk/pipeline/text.py", "MarkdownChunkPipeline.parse", "ctx"),
    ("rag/chunk/postprocessor.py", "ChunkPostProcessor._serialize_parent_child_groups", "ctx"),
    ("rag/chunk/postprocessor.py", "ChunkPostProcessor._build_mapped_parent_child_groups", "ctx"),
    ("rag/chunk/preprocessor.py", "HyperlinkPreprocessor.collect_url_chunks", "ctx"),
}
SNAPSHOT_WRITE = (
    "repositories/parser_configuration.py",
    "ParserConfigurationRepository._write",
    "cast(Any, owner)",
)
# Migration reads historical Core rows. Daily services may not import this module.
MIGRATION_READS = {
    ("parser_configuration/migration.py", "inspect_record", "row"),
    ("parser_configuration/migration.py", "validate_migration", "original"),
}
MIGRATION_IMPORTERS = {
    "parser_configuration/cli.py",
}
CORE_TABLE_ACCESS = {
    ("parser_configuration/schema.py", "validate_schema", "Knowledge.__table__"),
    ("parser_configuration/migration.py", "_contexts", "Knowledge.__table__"),
    (
        "parser_configuration/migration.py",
        "_scan",
        "KnowledgeBase.metadata.tables[model.__tablename__]",
    ),
    (
        "parser_configuration/migration.py",
        "_write_page",
        "KnowledgeBase.metadata.tables['knowledges']",
    ),
    (
        "parser_configuration/migration.py",
        "validate_migration",
        "KnowledgeBase.metadata.tables[owner.__tablename__]",
    ),
    (
        "parser_configuration/migration.py",
        "migrate",
        "KnowledgeBase.metadata.tables[model.__tablename__]",
    ),
}


def _storage_key(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value in STORAGE_NAMES


def _physical_container(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute) and node.attr in {"c", "columns", "__dict__", "_mapping"}
    ) or (isinstance(node, ast.Call) and ast.unparse(node.func) == "vars")


def check_source(source: str, path: str) -> list[str]:
    """Return source-located violations; path is relative to the service source root."""
    errors: list[str] = []
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return [f"{path}:{exc.lineno}: cannot check invalid Python"]
    orm_names = {"Knowledge", "Document"}
    column_functions = {"column", "literal_column"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and "models" in (node.module or ""):
            orm_names.update(
                alias.asname or alias.name for alias in node.names if alias.name in orm_names
            )
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("sqlalchemy"):
            column_functions.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name in {"column", "literal_column"}
            )

    def visit(node: ast.AST, scope: tuple[str, ...] = ()) -> None:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = (*scope, node.name)
        function = ".".join(scope)

        def reject(reason: str) -> None:
            errors.append(f"{path}:{getattr(node, 'lineno', 1)}: {reason}")

        if isinstance(node, ast.Attribute) and node.attr in STORAGE_NAMES:
            location = (path, function, ast.unparse(node.value))
            allowed = (
                node.attr == "parser_config"
                and isinstance(node.ctx, ast.Load)
                and location in RUNTIME_READS
            ) or (
                node.attr == SNAPSHOT
                and isinstance(node.ctx, ast.Store)
                and location == SNAPSHOT_WRITE
            )
            if not allowed:
                reject("old configuration storage access; use the configuration repository")
        if isinstance(node, ast.Subscript) and _storage_key(node.slice):
            receiver = ast.unparse(node.value)
            historical = (path, function, receiver) in MIGRATION_READS
            if (
                isinstance(node.slice, ast.Constant)
                and node.slice.value == SNAPSHOT
                or _physical_container(node.value)
            ):
                reject("old configuration storage subscript")
            elif receiver in {"row", "original"} and not historical:
                reject("historical row reads belong only to migration conversion/validation")
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func).rsplit(".", 1)[-1]
            if name in column_functions and node.args:
                value = node.args[0]
                if (
                    isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                    and re.search(r"\bparser_config\b", value.value)
                ):
                    reject("SQLAlchemy column expressions must not read rollback storage")
            if name in {"getattr", "setattr", "delattr", "hasattr"}:
                if len(node.args) > 1 and _storage_key(node.args[1]):
                    reject("reflective configuration storage access")
            if name in orm_names | {"values"}:
                mappings = list(node.args)
                for keyword in node.keywords:
                    if keyword.arg in STORAGE_NAMES:
                        reject("configuration storage constructor/bulk write")
                    if keyword.arg is None:
                        mappings.append(keyword.value)
                if any(
                    isinstance(value, ast.Dict) and any(_storage_key(k) for k in value.keys)
                    for value in mappings
                ):
                    reject("configuration storage dictionary bulk write")
            if name in {"Table", "table"} and node.args:
                if isinstance(node.args[0], ast.Constant) and node.args[0].value in {
                    "knowledges",
                    "documents",
                }:
                    reject("reflecting owner tables can expose rollback storage")
            if isinstance(node.func, ast.Attribute) and _physical_container(node.func.value):
                if node.args and _storage_key(node.args[0]):
                    reject("configuration storage mapping access")
            if (
                isinstance(node.func, ast.Attribute)
                and ast.unparse(node.func.value) in {"row", "original"}
                and name in {"get", "pop", "setdefault"}
                and node.args
                and _storage_key(node.args[0])
            ):
                reject("historical row mapping reads belong only to explicit migration expressions")
        core_table = (
            isinstance(node, ast.Attribute)
            and node.attr == "__table__"
            and ast.unparse(node.value).rsplit(".", 1)[-1] in orm_names
        ) or (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "tables"
        )
        if core_table and (path, function, ast.unparse(node)) not in CORE_TABLE_ACCESS:
            reject("Core owner table access requires an explicit migration/schema exception")
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Call):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(target, ast.Name) and target.id == "parser_config" for target in targets
            ) and ast.unparse(node.value.func).rsplit(".", 1)[-1] in {
                "Column",
                "mapped_column",
                "synonym",
            }:
                reject("old ORM attribute must not be reintroduced")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            sql = node.value.lower()
            if (
                re.search(r"\b(select|update|insert|delete)\b", sql)
                and re.search(r"\b(knowledges|documents)\b", sql)
                and (
                    re.search(r"\bparser_config\b", sql)
                    or re.search(r'\bselect\s+(?:[\w"]+\.)?\*', sql)
                )
            ):
                reject("raw SQL must not use the rollback column")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names]
            )
            migration = any(
                name == "migration" or name.endswith("parser_configuration.migration")
                for name in modules
            ) or (
                isinstance(node, ast.ImportFrom)
                and (node.module or "").endswith("parser_configuration")
                and any(alias.name == "migration" for alias in node.names)
            )
            if migration and path not in MIGRATION_IMPORTERS:
                reject("historical migration reader cannot be imported by daily business")
            historical = any(
                name == "historical" or name.endswith("parser_configuration.historical")
                for name in modules
            ) or (
                isinstance(node, ast.ImportFrom)
                and (node.module or "").endswith("parser_configuration")
                and any(alias.name == "historical" for alias in node.names)
            )
            if historical and path != "parser_configuration/migration.py":
                reject("historical repair is only allowed in offline migration")
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree)
    return sorted(set(errors))


def check_tree(root: Path) -> list[str]:
    return [
        error
        for path in sorted(root.rglob("*.py"))
        for error in check_source(
            path.read_text(encoding="utf-8"), path.relative_to(root).as_posix()
        )
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root", type=Path, default=Path(__file__).resolve().parents[1] / "src"
    )
    root = parser.parse_args().source_root
    if not root.is_dir() or not any(root.rglob("*.py")):
        parser.error("source root must contain Python files")
    errors = check_tree(root)
    for error in errors:
        print(error)
    if not errors:
        print("parser configuration storage access passed")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
