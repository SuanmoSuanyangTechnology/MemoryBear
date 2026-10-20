"""Fail when service ownership, migration, or import boundaries are violated."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_IMPORT_ROOTS = {"api", "app"}
READ_ONLY_PG_REPOSITORIES = {
    SERVICE_ROOT / "src/modules/analytics/repository/end_user_profile.py",
    SERVICE_ROOT / "src/modules/analytics/repository/memory_statistics.py",
    SERVICE_ROOT / "src/modules/display/repository/memory_display.py",
    SERVICE_ROOT / "src/modules/implicit_memory/repository/implicit_memory.py",
    SERVICE_ROOT / "src/modules/perceptual/repository/memory_perceptual.py",
}
FORBIDDEN_SQL_BUILDERS = {"text", "insert", "update", "delete"}
FORBIDDEN_SESSION_METHODS = {"add", "add_all", "commit", "delete", "flush", "merge"}
MIGRATION_ENV = SERVICE_ROOT / "migrations/env.py"
OWNED_MODELS_ROOT = SERVICE_ROOT / "src/models/owned"
POSTGRES_MANAGER = SERVICE_ROOT / "src/infrastructure/postgres.py"
MIGRATION_PATHS = (
    SERVICE_ROOT / "alembic.ini",
    MIGRATION_ENV,
    SERVICE_ROOT / "migrations/script.py.mako",
    SERVICE_ROOT / "migrations/versions",
)
OBSOLETE_PATHS = (
    SERVICE_ROOT / "src/api",
    SERVICE_ROOT / "src/main.py",
    SERVICE_ROOT / "src/repositories",
    SERVICE_ROOT / "src/services",
    SERVICE_ROOT / "src/infrastructure/celery.py",
    SERVICE_ROOT / "src/models/owned/base.py",
    SERVICE_ROOT / "src/modules/analytics/repository/cache.py",
    SERVICE_ROOT / "src/modules/analytics/repository/neo4j.py",
    SERVICE_ROOT / "src/modules/analytics/repository/postgres.py",
)


def check_python(path: Path) -> list[str]:
    failures: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                failures.append(f"{path}:{node.lineno}: relative imports are forbidden")
            elif node.module:
                names.append(node.module)
        for name in names:
            if name.split(".", 1)[0] in FORBIDDEN_IMPORT_ROOTS:
                failures.append(f"{path}:{node.lineno}: forbidden import {name}")
    return failures


def check_owned_model_boundary(path: Path) -> list[str]:
    """Keep host-owned mappings out of ProfileBase and Alembic metadata."""

    failures: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "src.models.references" or alias.name.startswith(
                    "src.models.references."
                ):
                    failures.append(
                        f"{path}:{node.lineno}: owned migrations cannot import {alias.name}"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module and (
                node.module == "src.models.references"
                or node.module.startswith("src.models.references.")
            ):
                failures.append(
                    f"{path}:{node.lineno}: owned migrations cannot import {node.module}"
                )
            if any(alias.name == "ReadOnlyBase" for alias in node.names):
                failures.append(f"{path}:{node.lineno}: owned migrations cannot use ReadOnlyBase")
        elif isinstance(node, ast.Name) and node.id == "ReadOnlyBase":
            failures.append(f"{path}:{node.lineno}: owned migrations cannot use ReadOnlyBase")
    return failures


def check_migration_scaffold() -> list[str]:
    failures: list[str] = []
    for path in MIGRATION_PATHS:
        if not path.exists():
            failures.append(f"{path}: required Alembic scaffold path is missing")
    if not MIGRATION_ENV.exists():
        return failures

    env_source = MIGRATION_ENV.read_text(encoding="utf-8")
    required_contracts = (
        "target_metadata = ProfileBase.metadata",
        'VERSION_TABLE = "alembic_version_profile"',
        "poolclass=NullPool",
    )
    for contract in required_contracts:
        if contract not in env_source:
            failures.append(f"{MIGRATION_ENV}: missing migration contract {contract!r}")
    return failures


def check_pool_safety() -> list[str]:
    """Keep transaction reset and dirty-connection rejection non-optional."""

    source = POSTGRES_MANAGER.read_text(encoding="utf-8")
    failures: list[str] = []
    required_contracts = (
        'pool_reset_on_return="rollback"',
        'event.listen(engine.sync_engine, "checkin", _reject_dirty_checkin)',
        'event.listen(engine.sync_engine, "checkout", _reject_dirty_checkout)',
        "connection_record.invalidate(error)",
        "raise DisconnectionError",
    )
    for contract in required_contracts:
        if contract not in source:
            failures.append(f"{POSTGRES_MANAGER}: missing pool safety contract {contract!r}")
    return failures


def check_read_only_repository(path: Path) -> list[str]:
    """Reject native SQL and ORM/Core writes in PostgreSQL reference repositories."""

    failures: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "sqlalchemy":
            forbidden = FORBIDDEN_SQL_BUILDERS.intersection(alias.name for alias in node.names)
            for name in sorted(forbidden):
                failures.append(f"{path}:{node.lineno}: forbidden SQL builder {name}")
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_SQL_BUILDERS:
            failures.append(f"{path}:{node.lineno}: forbidden SQL builder {node.func.id}")
        if isinstance(node.func, ast.Attribute) and node.func.attr in FORBIDDEN_SESSION_METHODS:
            failures.append(f"{path}:{node.lineno}: forbidden ORM write method {node.func.attr}")
    return failures


def check_docker_assets() -> list[str]:
    failures: list[str] = []
    dockerfile = SERVICE_ROOT / "Dockerfile"
    docker_source = dockerfile.read_text(encoding="utf-8")
    for number, line in enumerate(docker_source.splitlines(), 1):
        normalized = " ".join(line.lower().split())
        if normalized.startswith(("copy ", "add ")) and any(
            token in normalized for token in (" api/", " api ", "./api", "../api")
        ):
            failures.append(f"{dockerfile}:{number}: Docker build must not copy api")
    for asset in ("memory-profile/alembic.ini", "memory-profile/migrations"):
        if asset not in docker_source:
            failures.append(f"{dockerfile}: Docker image must copy {asset}")
    if '"--http", "zttp", "--http2"' not in docker_source:
        failures.append(f"{dockerfile}: Uvicorn must enable zttp HTTP/2")

    dockerignore = SERVICE_ROOT / "Dockerfile.dockerignore"
    ignore_source = dockerignore.read_text(encoding="utf-8")
    for rule in (
        "!memory-profile/alembic.ini",
        "!memory-profile/migrations/**",
        "!packages/auth-sdk/src/**",
    ):
        if rule not in ignore_source:
            failures.append(f"{dockerignore}: missing allow rule {rule}")
    return failures


def main() -> int:
    failures: list[str] = []
    for path in OBSOLETE_PATHS:
        if path.exists():
            failures.append(f"{path}: obsolete service layout must not exist")
    failures.extend(check_migration_scaffold())
    failures.extend(check_pool_safety())

    service_python = list((SERVICE_ROOT / "src").rglob("*.py"))
    migration_python = list((SERVICE_ROOT / "migrations").rglob("*.py"))
    for path in (*service_python, *migration_python):
        failures.extend(check_python(path))
    for path in (MIGRATION_ENV, *OWNED_MODELS_ROOT.rglob("*.py")):
        if path.exists():
            failures.extend(check_owned_model_boundary(path))
    for path in READ_ONLY_PG_REPOSITORIES:
        failures.extend(check_read_only_repository(path))
    failures.extend(check_docker_assets())
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("memory-profile import and migration boundaries: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
