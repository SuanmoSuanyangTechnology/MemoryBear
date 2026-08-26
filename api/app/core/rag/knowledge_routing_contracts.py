"""Statically verify API knowledge route and caller contracts."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class ControllerSpec:
    filename: str
    prefix: str


@dataclass(frozen=True)
class RouteOperation:
    method: str
    path: str
    filename: str
    handler: str
    decorators: tuple[str, ...]
    streaming: bool
    multipart: bool


@dataclass(frozen=True)
class KnowledgeRoutingInventory:
    manager_count: int
    service_count: int
    manager_counts: dict[str, int]
    service_counts: dict[str, int]
    public_service_operations: frozenset[tuple[str, str]]
    stream_operations: frozenset[tuple[str, str]]
    multipart_operations: frozenset[tuple[str, str]]
    direct_retrieval_callers: frozenset[str]


MANAGER_SPECS = (
    ControllerSpec("knowledge_controller.py", "/knowledges"),
    ControllerSpec("knowledge_metadata_controller.py", "/knowledges"),
    ControllerSpec("file_controller.py", "/files"),
    ControllerSpec("document_controller.py", "/documents"),
    ControllerSpec("chunk_controller.py", "/chunks"),
    ControllerSpec("knowledgeshare_controller.py", "/knowledgeshares"),
)

SERVICE_SPECS = (
    ControllerSpec("rag_api_knowledge_controller.py", "/knowledges"),
    ControllerSpec("rag_api_document_controller.py", "/documents"),
    ControllerSpec("rag_api_file_controller.py", "/files"),
    ControllerSpec("rag_api_chunk_controller.py", "/chunks"),
)

HTTP_METHODS = {"delete", "get", "patch", "post", "put"}
REMOTE_DECORATOR = "route_through_knowledge_service"
AUTH_DECORATORS = {
    "check_knowledge_capacity_quota",
    "cur_workspace_access_guard",
    "cur_workspace_access_guard_async",
    "require_api_key",
    "require_api_key_self_db",
}


def _call_name(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Call):
        return _call_name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _route_from_decorator(node: ast.expr) -> tuple[str, str] | None:
    if not isinstance(node, ast.Call) or not node.args:
        return None
    target = node.func
    if not isinstance(target, ast.Attribute):
        return None
    if not isinstance(target.value, ast.Name) or target.value.id != "router":
        return None
    if target.attr not in HTTP_METHODS:
        return None
    path = node.args[0]
    if not isinstance(path, ast.Constant) or not isinstance(path.value, str):
        raise TypeError("Knowledge route paths must be string literals")
    return target.attr.upper(), path.value


def _returns_stream(node: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Return) and _call_name(child.value) == "StreamingResponse":
            return True
    return False


def _accepts_multipart(node: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    defaults: list[ast.expr | None] = [None] * (
        len(node.args.args) - len(node.args.defaults)
    ) + list(node.args.defaults)
    defaults.extend(node.args.kw_defaults)
    return any(_call_name(default) == "File" for default in defaults)


def _parse_controller(path: Path, prefix: str, mount: str) -> tuple[RouteOperation, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    operations: list[RouteOperation] = []
    for node in tree.body:
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        decorators = tuple(
            name for decorator in node.decorator_list if (name := _call_name(decorator))
        )
        for decorator in node.decorator_list:
            route = _route_from_decorator(decorator)
            if route is None:
                continue
            method, relative_path = route
            operations.append(
                RouteOperation(
                    method=method,
                    path=f"{mount}{prefix}{relative_path}",
                    filename=path.name,
                    handler=node.name,
                    decorators=decorators,
                    streaming=_returns_stream(node),
                    multipart=_accepts_multipart(node),
                )
            )
    return tuple(operations)


class _RetrievalCallVisitor(ast.NodeVisitor):
    found = False

    def visit_Call(self, node: ast.Call) -> None:
        target = node.func
        if (
            isinstance(target, ast.Attribute)
            and target.attr == "retrieve_async"
            and isinstance(target.value, ast.Name)
            and target.value.id == "KnowledgeRetrievalService"
        ):
            self.found = True
        self.generic_visit(node)


def _direct_retrieval_callers(repo_root: Path) -> frozenset[str]:
    callers: set[str] = set()
    app_root = repo_root / "api" / "app"
    for path in app_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _RetrievalCallVisitor()
        visitor.visit(tree)
        if visitor.found:
            callers.add(path.relative_to(repo_root).as_posix())
    return frozenset(callers)


def collect_inventory(repo_root: Path) -> KnowledgeRoutingInventory:
    repo_root = repo_root.resolve()
    manager_root = repo_root / "api" / "app" / "controllers"
    service_root = manager_root / "service"

    manager: list[RouteOperation] = []
    manager_counts: dict[str, int] = {}
    for spec in MANAGER_SPECS:
        operations = _parse_controller(manager_root / spec.filename, spec.prefix, "/api")
        manager.extend(operations)
        manager_counts[spec.filename] = len(operations)

    service: list[RouteOperation] = []
    service_counts: dict[str, int] = {}
    for spec in SERVICE_SPECS:
        operations = _parse_controller(service_root / spec.filename, spec.prefix, "/v1")
        service.extend(operations)
        service_counts[spec.filename] = len(operations)

    public_service = frozenset(
        (operation.method, operation.path)
        for operation in service
        if not any(name.startswith("require_api_key") for name in operation.decorators)
    )
    stream_operations = frozenset(
        (operation.method, operation.path.replace("/api", "/internal/v1", 1))
        for operation in manager
        if operation.streaming
    )
    multipart_operations = frozenset(
        (operation.method, operation.path.replace("/api", "/internal/v1", 1))
        for operation in manager
        if operation.multipart
    )

    return KnowledgeRoutingInventory(
        manager_count=len(manager),
        service_count=len(service),
        manager_counts=dict(sorted(manager_counts.items())),
        service_counts=dict(sorted(service_counts.items())),
        public_service_operations=public_service,
        stream_operations=stream_operations,
        multipart_operations=multipart_operations,
        direct_retrieval_callers=_direct_retrieval_callers(repo_root),
    )


def _all_operations(repo_root: Path) -> tuple[RouteOperation, ...]:
    manager_root = repo_root / "api" / "app" / "controllers"
    service_root = manager_root / "service"
    operations: list[RouteOperation] = []
    for spec in MANAGER_SPECS:
        operations.extend(_parse_controller(manager_root / spec.filename, spec.prefix, "/api"))
    for spec in SERVICE_SPECS:
        operations.extend(_parse_controller(service_root / spec.filename, spec.prefix, "/v1"))
    return tuple(operations)


def validate_remote_decorators(repo_root: Path) -> list[str]:
    errors: list[str] = []
    for operation in _all_operations(repo_root):
        if REMOTE_DECORATOR not in operation.decorators:
            errors.append(f"missing remote decorator: {operation.method} {operation.path}")
            continue
        remote_index = operation.decorators.index(REMOTE_DECORATOR)
        for name in AUTH_DECORATORS.intersection(operation.decorators):
            if operation.decorators.index(name) > remote_index:
                errors.append(
                    f"remote decorator bypasses {name}: {operation.method} {operation.path}"
                )
    return errors


def validate_central_retriever(repo_root: Path) -> list[str]:
    expected = frozenset({"api/app/integrations/knowledge/legacy_retriever.py"})
    actual = _direct_retrieval_callers(repo_root)
    if actual == expected:
        return []
    return [f"direct retrieval callers differ: expected={sorted(expected)} actual={sorted(actual)}"]


def _payload(inventory: KnowledgeRoutingInventory) -> dict[str, object]:
    payload = asdict(inventory)
    for key in (
        "public_service_operations",
        "stream_operations",
        "multipart_operations",
        "direct_retrieval_callers",
    ):
        payload[key] = sorted(payload[key])
    return payload


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[4])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-remote-decorators", action="store_true")
    parser.add_argument("--require-central-retriever", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    repo_root = args.repo_root.resolve()
    inventory = collect_inventory(repo_root)
    errors: list[str] = []
    if args.require_remote_decorators:
        errors.extend(validate_remote_decorators(repo_root))
    if args.require_central_retriever:
        errors.extend(validate_central_retriever(repo_root))
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    if args.json:
        json.dump(_payload(inventory), sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print(f"manager knowledge operations: {inventory.manager_count}")
        print(f"service knowledge operations: {inventory.service_count}")
        print(f"public service operations: {len(inventory.public_service_operations)}")
        print(f"stream operations: {len(inventory.stream_operations)}")
        print(f"multipart operations: {len(inventory.multipart_operations)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
