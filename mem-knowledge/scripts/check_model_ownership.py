"""Validate Knowledge-owned models and read-only Platform projections."""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

EXPECTED_OWNED_TABLES = {
    "knowledges",
    "files",
    "documents",
    "knowledge_shares",
    "knowledge_metadatas",
    "knowledge_metadata_bindings",
}

EXPECTED_REFERENCE_TABLES = {
    "workspaces",
    "users",
    "model_configs",
    "model_bases",
    "model_api_keys",
    "model_config_api_key_association",
}

OWNED_MODEL_NAMES = {
    "Knowledge",
    "File",
    "Document",
    "KnowledgeShare",
    "KnowledgeMetadata",
    "KnowledgeMetadataBinding",
}

FORBIDDEN_REFERENCE_WRITES = {"add", "delete", "update", "commit"}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _base_name(base: ast.expr) -> str | None:
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return None


def _table_names(root: Path, expected_base: str) -> tuple[set[str], list[str]]:
    tables: set[str] = set()
    errors: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = _parse(path)
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                table_name: str | None = None
                for statement in node.body:
                    if not isinstance(statement, ast.Assign):
                        continue
                    if not any(
                        isinstance(target, ast.Name) and target.id == "__tablename__"
                        for target in statement.targets
                    ):
                        continue
                    if isinstance(statement.value, ast.Constant) and isinstance(
                        statement.value.value,
                        str,
                    ):
                        table_name = statement.value.value
                if table_name is None:
                    continue
                tables.add(table_name)
                if expected_base not in {
                    name for base in node.bases if (name := _base_name(base)) is not None
                }:
                    errors.append(
                        f"{path}: class {node.name} maps {table_name} without {expected_base}"
                    )
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                if not isinstance(value, ast.Call):
                    continue
                function_name = _base_name(value.func)
                if function_name != "Table" or not value.args:
                    continue
                first_argument = value.args[0]
                if isinstance(first_argument, ast.Constant) and isinstance(
                    first_argument.value,
                    str,
                ):
                    tables.add(first_argument.value)
    return tables, errors


def _defines_class(path: Path, class_name: str) -> bool:
    if not path.is_file():
        return False
    return any(
        isinstance(node, ast.ClassDef) and node.name == class_name
        for node in _parse(path).body
    )


def _relationship_errors(source_root: Path) -> list[str]:
    errors: list[str] = []
    owned_root = source_root / "models" / "owned"
    for path in sorted(owned_root.rglob("*.py")):
        for node in ast.walk(_parse(path)):
            if not isinstance(node, ast.Call) or _base_name(node.func) != "relationship":
                continue
            if not node.args:
                continue
            target = node.args[0]
            if not isinstance(target, ast.Constant) or not isinstance(target.value, str):
                continue
            if target.value not in OWNED_MODEL_NAMES:
                errors.append(
                    f"{path.relative_to(source_root)}:{node.lineno} "
                    f"relates owned model to Platform target: {target.value}"
                )
    return errors


def _reference_write_errors(source_root: Path) -> list[str]:
    errors: list[str] = []
    repository_root = source_root / "repositories"
    candidates = (
        repository_root / "reference.py",
        repository_root / "model_registry.py",
    )
    for path in candidates:
        if not path.is_file():
            continue
        for node in ast.walk(_parse(path)):
            if not isinstance(node, ast.Call):
                continue
            method_name = _base_name(node.func)
            if method_name not in FORBIDDEN_REFERENCE_WRITES:
                continue
            errors.append(
                f"{path.relative_to(source_root)}:{node.lineno} "
                f"calls forbidden reference write: {method_name}"
            )
    return errors


def validate_model_ownership(source_root: Path) -> list[str]:
    """Return all ownership violations under a service source tree."""

    errors: list[str] = []
    knowledge_base_path = source_root / "db.py"
    reference_base_path = source_root / "models" / "references" / "base.py"
    if not _defines_class(knowledge_base_path, "KnowledgeBase"):
        errors.append("db.py does not define KnowledgeBase")
    if not _defines_class(reference_base_path, "ReferenceBase"):
        errors.append("models/references/base.py does not define ReferenceBase")

    owned_tables, owned_errors = _table_names(
        source_root / "models" / "owned",
        "KnowledgeBase",
    )
    reference_tables, reference_errors = _table_names(
        source_root / "models" / "references",
        "ReferenceBase",
    )
    errors.extend(owned_errors)
    errors.extend(reference_errors)
    if owned_tables != EXPECTED_OWNED_TABLES:
        errors.append(
            "owned tables differ: "
            f"expected={sorted(EXPECTED_OWNED_TABLES)} actual={sorted(owned_tables)}"
        )
    if reference_tables != EXPECTED_REFERENCE_TABLES:
        errors.append(
            "reference tables differ: "
            f"expected={sorted(EXPECTED_REFERENCE_TABLES)} "
            f"actual={sorted(reference_tables)}"
        )
    errors.extend(_relationship_errors(source_root))
    errors.extend(_reference_write_errors(source_root))
    return sorted(errors)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "src",
    )
    return parser.parse_args()


def main() -> int:
    errors = validate_model_ownership(_parse_args().source_root)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("knowledge model ownership passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
