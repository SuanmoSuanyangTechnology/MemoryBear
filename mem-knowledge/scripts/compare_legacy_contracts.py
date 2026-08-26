"""Compare legacy knowledge routes with the internal Knowledge OpenAPI."""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, order=True)
class OperationContract:
    """One HTTP operation and its caller-visible transport contract."""

    method: str
    path: str
    handler: str
    parameters: tuple[tuple[str, str, bool], ...]
    request_content_types: tuple[str, ...]
    response_content_types: tuple[str, ...]
    streaming: bool


@dataclass(frozen=True)
class ControllerSpec:
    """Legacy controller file and its mounted path prefix."""

    filename: str
    prefix: str


CONTROLLERS = (
    ControllerSpec("knowledge_controller.py", "/knowledges"),
    ControllerSpec("knowledge_metadata_controller.py", "/knowledges"),
    ControllerSpec("file_controller.py", "/files"),
    ControllerSpec("document_controller.py", "/documents"),
    ControllerSpec("chunk_controller.py", "/chunks"),
    ControllerSpec("knowledgeshare_controller.py", "/knowledgeshares"),
)

EXPECTED_COUNTS = {
    "chunk_controller.py": 11,
    "document_controller.py": 10,
    "file_controller.py": 8,
    "knowledge_controller.py": 18,
    "knowledge_metadata_controller.py": 7,
    "knowledgeshare_controller.py": 4,
}

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
LEGACY_RESPONSE_FIELDS = {"code", "msg", "data", "error", "time"}
LEGACY_ERROR_STATUSES = ("400", "404", "409", "500")
STREAM_OPERATIONS = {
    ("GET", "/internal/v1/knowledges/{kb_id}/qa/export"),
    ("POST", "/internal/v1/knowledges/{kb_id}/batch-download"),
    ("GET", "/internal/v1/files/{file_id}"),
    ("POST", "/internal/v1/files/batch-download"),
}
LEGACY_ORACLE_REF = "fbd5da5604a114f9861986b19ea7ff481eaffaba"


def normalize_internal_operation(method: str, legacy_path: str) -> tuple[str, str]:
    """Map a legacy controller operation to its internal service path."""

    return method.upper(), f"/internal/v1{legacy_path}"


def _route_from_decorator(decorator: ast.expr) -> tuple[str, str] | None:
    if not isinstance(decorator, ast.Call):
        return None
    target = decorator.func
    if not isinstance(target, ast.Attribute):
        return None
    if not isinstance(target.value, ast.Name) or target.value.id != "router":
        return None
    if target.attr not in HTTP_METHODS or not decorator.args:
        return None
    path = decorator.args[0]
    if not isinstance(path, ast.Constant) or not isinstance(path.value, str):
        raise ValueError("Knowledge route paths must be string literals")
    return target.attr, path.value


def _call_name(candidate: ast.expr | None) -> str | None:
    if not isinstance(candidate, ast.Call):
        return None
    target = candidate.func
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _is_required(argument: ast.arg, default: ast.expr | None) -> bool:
    del argument
    if default is None:
        return True
    if not isinstance(default, ast.Call) or not default.args:
        return False
    first_arg = default.args[0]
    return isinstance(first_arg, ast.Constant) and first_arg.value is Ellipsis


def _is_scalar_annotation(annotation: ast.expr | None) -> bool:
    if annotation is None:
        return True
    if isinstance(annotation, ast.Name):
        return annotation.id in {"Any", "UUID", "bool", "bytes", "float", "int", "str"}
    if isinstance(annotation, ast.Attribute):
        return (
            isinstance(annotation.value, ast.Name)
            and annotation.value.id == "uuid"
            and annotation.attr == "UUID"
        )
    if isinstance(annotation, ast.Subscript):
        wrapper = annotation.value
        wrapper_name = wrapper.id if isinstance(wrapper, ast.Name) else ""
        if wrapper_name in {"Annotated", "Optional"}:
            element = annotation.slice
            if isinstance(element, ast.Tuple):
                element = element.elts[0]
            return _is_scalar_annotation(element)
        return False
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        operands = (annotation.left, annotation.right)
        return all(
            isinstance(item, ast.Constant) and item.value is None
            or _is_scalar_annotation(item)
            for item in operands
        )
    if isinstance(annotation, ast.Constant) and annotation.value is None:
        return True
    return False


def _is_implicit_json_body(
    argument: ast.arg,
    default: ast.expr | None,
    method: str,
) -> bool:
    del default
    if method not in {"post", "put", "patch"}:
        return False
    return not _is_scalar_annotation(argument.annotation)


def _function_contract(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    method: str,
    internal_path: str,
) -> tuple[
    tuple[tuple[str, str, bool], ...],
    tuple[str, ...],
    tuple[str, ...],
    bool,
]:
    positional = [*node.args.posonlyargs, *node.args.args]
    defaults: list[ast.expr | None] = [None] * (
        len(positional) - len(node.args.defaults)
    ) + list(node.args.defaults)
    keyword_only = list(zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True))
    arguments = [*zip(positional, defaults, strict=True), *keyword_only]

    parameters: list[tuple[str, str, bool]] = []
    body_kinds: set[str] = set()
    for argument, default in arguments:
        call_name = _call_name(default)
        if call_name == "Depends":
            continue
        if call_name == "File":
            body_kinds.add("file")
            continue
        if call_name == "Form":
            body_kinds.add("form")
            continue
        if call_name == "Body" or _is_implicit_json_body(argument, default, method):
            body_kinds.add("json")
            continue

        if f"{{{argument.arg}}}" in internal_path:
            location = "path"
            required = True
        elif call_name in {"Header", "Cookie", "Query"}:
            location = call_name.lower()
            required = _is_required(argument, default)
        else:
            location = "query"
            required = _is_required(argument, default)
        parameters.append((argument.arg, location, required))

    if "file" in body_kinds:
        request_content_types = ("multipart/form-data",)
    elif "form" in body_kinds:
        request_content_types = ("application/x-www-form-urlencoded",)
    elif "json" in body_kinds:
        request_content_types = ("application/json",)
    else:
        request_content_types = ()

    operation_key = (method.upper(), internal_path)
    streaming = operation_key in STREAM_OPERATIONS
    response_content_types = () if streaming else ("application/json",)
    return (
        tuple(parameters),
        request_content_types,
        response_content_types,
        streaming,
    )


def _controller_operations_from_source(
    source: str,
    *,
    filename: str,
    prefix: str,
) -> list[OperationContract]:
    tree = ast.parse(source, filename=filename)
    operations: list[OperationContract] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            route = _route_from_decorator(decorator)
            if route is None:
                continue
            method, relative_path = route
            normalized_method, internal_path = normalize_internal_operation(
                method,
                f"{prefix}{relative_path}",
            )
            parameters, request_types, response_types, streaming = _function_contract(
                node,
                method=method,
                internal_path=internal_path,
            )
            operations.append(
                OperationContract(
                    method=normalized_method,
                    path=internal_path,
                    handler=node.name,
                    parameters=parameters,
                    request_content_types=request_types,
                    response_content_types=response_types,
                    streaming=streaming,
                )
            )
    return operations


def _controller_operations(path: Path, prefix: str) -> list[OperationContract]:
    return _controller_operations_from_source(
        path.read_text(encoding="utf-8"),
        filename=str(path),
        prefix=prefix,
    )


def _validate_legacy_inventory(
    operations: list[OperationContract],
    counts: Mapping[str, int],
) -> tuple[tuple[OperationContract, ...], dict[str, int]]:
    if counts != EXPECTED_COUNTS:
        raise ValueError(
            f"Legacy controller counts changed: expected={EXPECTED_COUNTS} actual={counts}"
        )
    if len(operations) != 58:
        raise ValueError(f"Expected 58 legacy operations, found {len(operations)}")

    operation_keys = {(item.method, item.path) for item in operations}
    if len(operation_keys) != len(operations):
        raise ValueError("Legacy knowledge controllers contain duplicate operations")
    return tuple(sorted(operations)), dict(sorted(counts.items()))


def collect_legacy_operations(
    legacy_root: Path,
) -> tuple[tuple[OperationContract, ...], dict[str, int]]:
    """Load and validate the fixed six-controller migration inventory."""

    controller_root = legacy_root / "app" / "controllers"
    operations: list[OperationContract] = []
    counts: dict[str, int] = {}
    for spec in CONTROLLERS:
        controller_path = controller_root / spec.filename
        if not controller_path.is_file():
            raise FileNotFoundError(f"Legacy controller is missing: {controller_path}")
        controller_operations = _controller_operations(controller_path, spec.prefix)
        counts[spec.filename] = len(controller_operations)
        operations.extend(controller_operations)

    return _validate_legacy_inventory(operations, counts)


def collect_legacy_operations_from_git(
    repo_root: Path,
    legacy_root: Path,
    legacy_ref: str = LEGACY_ORACLE_REF,
) -> tuple[tuple[OperationContract, ...], dict[str, int]]:
    """Load the behavior oracle from immutable Git objects."""

    root_in_repo = (
        legacy_root.relative_to(repo_root) if legacy_root.is_absolute() else legacy_root
    )
    operations: list[OperationContract] = []
    counts: dict[str, int] = {}
    for spec in CONTROLLERS:
        relative_path = root_in_repo / "app" / "controllers" / spec.filename
        try:
            completed = subprocess.run(
                ["git", "show", f"{legacy_ref}:{relative_path.as_posix()}"],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.strip() or exc.stdout.strip() or "git show failed"
            raise FileNotFoundError(
                f"Legacy controller is missing at {legacy_ref}:{relative_path}: {detail}"
            ) from exc
        controller_operations = _controller_operations_from_source(
            completed.stdout,
            filename=f"{legacy_ref}:{relative_path}",
            prefix=spec.prefix,
        )
        counts[spec.filename] = len(controller_operations)
        operations.extend(controller_operations)
    return _validate_legacy_inventory(operations, counts)


def compare_openapi_operations(
    schema: Mapping[str, Any],
    expected: Sequence[OperationContract],
) -> list[str]:
    """Return deterministic method/path parity errors."""

    expected_keys = {(item.method, item.path) for item in expected}
    actual_keys: set[tuple[str, str]] = set()
    for path, path_item in schema.get("paths", {}).items():
        if path.startswith("/internal/v1/health/"):
            continue
        if not isinstance(path_item, Mapping):
            continue
        for method in HTTP_METHODS:
            if method in path_item:
                actual_keys.add((method.upper(), path))

    errors: list[str] = []
    for method, path in sorted(expected_keys - actual_keys):
        errors.append(f"missing internal operation: {method} {path}")
    for method, path in sorted(actual_keys - expected_keys):
        errors.append(f"unexpected internal operation: {method} {path}")
    return errors


def compare_openapi_transport_contracts(
    schema: Mapping[str, Any],
    expected: Sequence[OperationContract],
) -> list[str]:
    """Return caller-visible parameter and media-type parity errors."""

    errors: list[str] = []
    for operation in expected:
        operation_schema = (
            schema.get("paths", {})
            .get(operation.path, {})
            .get(operation.method.lower(), {})
        )
        raw_parameters = operation_schema.get("parameters", [])
        actual_parameters = sorted(
            (
                str(parameter.get("name", "")),
                str(parameter.get("in", "")),
                bool(parameter.get("required", False)),
            )
            for parameter in raw_parameters
            if isinstance(parameter, Mapping)
            and not (
                parameter.get("in") == "header"
                and str(parameter.get("name", "")).startswith("X-KB-")
            )
        )
        expected_parameters = sorted(operation.parameters)
        if actual_parameters != expected_parameters:
            errors.append(
                "incompatible parameters: "
                f"{operation.method} {operation.path} "
                f"expected={expected_parameters} actual={actual_parameters}"
            )

        request_body = operation_schema.get("requestBody", {})
        request_content = (
            request_body.get("content", {}) if isinstance(request_body, Mapping) else {}
        )
        actual_request_types = sorted(request_content)
        expected_request_types = sorted(operation.request_content_types)
        if actual_request_types != expected_request_types:
            errors.append(
                "incompatible request content types: "
                f"{operation.method} {operation.path} "
                f"expected={expected_request_types} actual={actual_request_types}"
            )

        if operation.streaming:
            continue
        response = operation_schema.get("responses", {}).get("200", {})
        response_content = (
            response.get("content", {}) if isinstance(response, Mapping) else {}
        )
        actual_response_types = sorted(response_content)
        expected_response_types = sorted(operation.response_content_types)
        if actual_response_types != expected_response_types:
            errors.append(
                "incompatible response content types: "
                f"{operation.method} {operation.path} "
                f"expected={expected_response_types} actual={actual_response_types}"
            )
    return errors


def _resolve_schema(
    schema: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> Mapping[str, Any]:
    reference = candidate.get("$ref")
    if not isinstance(reference, str):
        return candidate
    prefix = "#/components/schemas/"
    if not reference.startswith(prefix):
        return {}
    name = reference.removeprefix(prefix)
    resolved = schema.get("components", {}).get("schemas", {}).get(name, {})
    return resolved if isinstance(resolved, Mapping) else {}


def _operation_response_schema(
    schema: Mapping[str, Any],
    operation: OperationContract,
    status_code: str,
) -> Mapping[str, Any]:
    operation_schema = (
        schema.get("paths", {}).get(operation.path, {}).get(operation.method.lower(), {})
    )
    response_schema = (
        operation_schema.get("responses", {})
        .get(status_code, {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    return _resolve_schema(schema, response_schema)


def compare_openapi_responses(
    schema: Mapping[str, Any],
    expected: Sequence[OperationContract],
) -> list[str]:
    """Return response-envelope errors for the 54 JSON business operations."""

    errors: list[str] = []
    json_count = 0
    for operation in expected:
        for status_code in LEGACY_ERROR_STATUSES:
            error_schema = _operation_response_schema(schema, operation, status_code)
            error_properties = error_schema.get("properties", {})
            if set(error_properties) != LEGACY_RESPONSE_FIELDS:
                errors.append(
                    "incompatible error envelope: "
                    f"{operation.method} {operation.path} status={status_code} "
                    f"fields={sorted(error_properties)}"
                )
        key = (operation.method, operation.path)
        if key in STREAM_OPERATIONS:
            continue
        resolved = _operation_response_schema(schema, operation, "200")
        properties = resolved.get("properties", {})
        if set(properties) != LEGACY_RESPONSE_FIELDS:
            errors.append(
                "incompatible success envelope: "
                f"{operation.method} {operation.path} fields={sorted(properties)}"
            )
            continue
        if properties["code"].get("type") != "integer":
            errors.append(f"response code is not numeric: {operation.method} {operation.path}")
        if properties["time"].get("type") != "integer":
            errors.append(f"response time is not numeric: {operation.method} {operation.path}")
        json_count += 1
    if json_count != 54:
        errors.append(f"expected 54 legacy JSON envelopes, found {json_count}")
    return errors


def _inventory_payload(
    operations: Sequence[OperationContract],
    counts: Mapping[str, int],
) -> dict[str, Any]:
    return {
        "counts": dict(counts),
        "operation_count": len(operations),
        "operations": [asdict(item) for item in operations],
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-root", required=True, type=Path)
    parser.add_argument("--legacy-ref", default=LEGACY_ORACLE_REF)
    parser.add_argument("--repo-root", default=Path.cwd(), type=Path)
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        operations, counts = collect_legacy_operations_from_git(
            args.repo_root,
            args.legacy_root,
            args.legacy_ref,
        )
    except (FileNotFoundError, SyntaxError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    payload = _inventory_payload(operations, counts)
    if not args.inventory_only:
        from src.main import create_app

        schema = create_app().openapi()
        contract_errors = compare_openapi_operations(
            schema,
            operations,
        )
        response_errors = compare_openapi_responses(
            schema,
            operations,
        )
        contract_errors.extend(response_errors)
        contract_errors.extend(compare_openapi_transport_contracts(schema, operations))
        if contract_errors:
            for error in contract_errors:
                print(error, file=sys.stderr)
            return 1
        payload["openapi_operation_count"] = len(operations)
        payload["openapi_json_envelope_count"] = 54
        payload["openapi_error_envelope_operation_count"] = 58
        payload["openapi_stream_operation_count"] = len(STREAM_OPERATIONS)
        payload["openapi_transport_contract_count"] = len(operations)
        payload["openapi_parity"] = True
    if args.json:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print(f"legacy knowledge operations: {len(operations)}")
        for filename, count in counts.items():
            print(f"{filename}: {count}")
        if not args.inventory_only:
            print(
                "internal OpenAPI parity: 58/58 operations, 54/54 JSON envelopes, "
                "58/58 error envelopes"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
