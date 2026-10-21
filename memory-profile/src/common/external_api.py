"""Import-time marker for endpoints the API-key face may reach.

``@external_api`` is metadata only: it sets one attribute on the endpoint and returns
the very same function, so a marked route runs exactly the code an unmarked one
runs — no wrapper, no dependency, no per-request lookup.

The marker exists because API-key callers are outside this repository: changing
a marked route's request parameters, request body, or response silently breaks
them. The committed snapshot in ``external_api_contract.json`` records the shape
the last release promised; :func:`verify_external_api_contract` compares the
running code with it once at startup. A missing or unreadable snapshot fails
startup (the guard must not go quiet), while drift logs a warning per change.

The contract is read from FastAPI's own route analysis (``route.dependant``),
not re-derived from the function signature: FastAPI is the authority on where a
parameter lives, which default wins, and which dependency parameters are
exposed to callers.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import marshal
import types
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import (
    Annotated,
    Any,
    TypeAliasType,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from fastapi.params import File, Form
from fastapi.routing import iter_route_contexts
from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

logger = logging.getLogger(__name__)

#: Attribute the decorator writes; route objects never see it, their endpoint does.
EXTERNAL_API_MARKER = "__memory_external_api__"
SNAPSHOT_SCHEMA = 1
#: Committed at the package root (``src/``), which is already shipped by the
#: image; resolved from the file so it works both in the source tree and in
#: the container, where ``src/`` is copied wholesale to ``/app/src``.
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "external_api_contract.json"


def external_api[Endpoint: Callable[..., Any]](endpoint: Endpoint) -> Endpoint:
    """Mark an endpoint as reachable through the API-key (``ex_api``) face.

    The decorator is intentionally inert at request time: it attaches one
    attribute and returns the function unchanged. Put it below the route
    decorator, closest to the function::

        @router.get("/memory_insight")
        @external_api
        async def memory_insight(...): ...
    """

    setattr(endpoint, EXTERNAL_API_MARKER, True)
    return endpoint


def is_external_api_endpoint(endpoint: object) -> bool:
    """Whether ``endpoint`` carries the :func:`external_api` marker."""

    return bool(getattr(endpoint, EXTERNAL_API_MARKER, False))


# ── Type helpers ─────────────────────────────────────────────────────────────


def _annotation_parts(annotation: object) -> tuple[object, tuple[object, ...]]:
    if get_origin(annotation) is Annotated:
        args = get_args(annotation)
        return args[0], tuple(args[1:])
    return annotation, ()


def _unwrap_optional(annotation: object) -> object:
    """Peel ``T | None`` and PEP 695 aliases to the payload type."""

    if isinstance(annotation, TypeAliasType):
        return _unwrap_optional(annotation.__value__)
    if get_origin(annotation) in (Union, types.UnionType):
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(args) == 1:
            return _unwrap_optional(args[0])
    return annotation


def _strip_wrappers(annotation: object) -> object:
    annotation = _unwrap_optional(annotation)
    if get_origin(annotation) is Annotated:
        return _strip_wrappers(get_args(annotation)[0])
    return annotation


def _annotation_text(annotation: object) -> str:
    """Stable, readable name; metadata is recorded separately as constraints."""

    if annotation is Any:
        return "Any"
    if annotation is None or annotation is type(None):
        return "None"
    if isinstance(annotation, TypeAliasType):
        return _annotation_text(annotation.__value__)
    if isinstance(annotation, type):
        if annotation.__module__ == "builtins":
            return annotation.__qualname__
        return f"{annotation.__module__}.{annotation.__qualname__}"
    origin = get_origin(annotation)
    if origin is Annotated:
        return _annotation_text(get_args(annotation)[0])
    if origin in (Union, types.UnionType):
        return " | ".join(_annotation_text(arg) for arg in get_args(annotation))
    if origin is not None:
        args = get_args(annotation)
        name = getattr(origin, "__name__", str(origin))
        if args:
            return f"{name}[{', '.join(_annotation_text(arg) for arg in args)}]"
        return name
    return str(annotation).replace("typing.", "")


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        # Set iteration order depends on hash randomisation, so sort by the
        # normalised JSON form; otherwise the same default serialises in a
        # different order in each process and looks like contract drift.
        items = [_json_value(item) for item in value]
        return sorted(
            items,
            key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False, default=repr),
        )
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    return repr(value)


def _code_fingerprint(code: types.CodeType) -> tuple[object, ...]:
    """Behavioural structure of a code object, without environment-specific fields.

    ``co_filename`` and line tables are deliberately excluded: the same factory
    must hash identically on a developer machine, in CI, and in the image, and
    moving a function within a file must not look like a contract change.
    Nested code objects (comprehensions, inner functions) are normalised the
    same way because their constants would otherwise carry a filename too.
    """

    def const_fingerprint(const: object) -> object:
        if isinstance(const, types.CodeType):
            return _code_fingerprint(const)
        if isinstance(const, tuple):
            return tuple(const_fingerprint(item) for item in const)
        if isinstance(const, frozenset):
            # Hash randomisation makes frozenset order process-dependent.
            return tuple(sorted((const_fingerprint(item) for item in const), key=repr))
        return const

    return (
        code.co_argcount,
        code.co_posonlyargcount,
        code.co_kwonlyargcount,
        code.co_flags,
        code.co_code,
        code.co_names,
        code.co_varnames,
        code.co_freevars,
        code.co_cellvars,
        tuple(const_fingerprint(item) for item in code.co_consts),
    )


def _factory_value(factory: Callable[..., Any], takes_validated_data: bool = False) -> object:
    """Identify a default factory without calling it.

    Calling it is what makes a factory's effect visible, but factories are not
    required to be deterministic (``ApiResponse.time`` computes the current
    timestamp), so evaluation would produce a different snapshot on every run.
    The digest covers bytecode, names, and constants recursively, but not the
    source filename or line numbers, so it is stable across processes, hosts,
    and container paths for the pinned Python version.
    """

    name = getattr(factory, "__qualname__", getattr(factory, "__name__", repr(factory)))
    if takes_validated_data:
        return f"<factory:{name}:validated>"
    code = getattr(factory, "__code__", None)
    if code is None:
        return f"<factory:{name}>"
    try:
        payload = marshal.dumps(_code_fingerprint(code))
    except (TypeError, ValueError):
        return f"<factory:{name}>"
    digest = hashlib.sha1(payload).hexdigest()[:12]
    return f"<factory:{name}#{digest}>"


def _is_pydantic_model(annotation: object) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _is_dataclass_model(annotation: object) -> bool:
    return isinstance(annotation, type) and dataclasses.is_dataclass(annotation)


def _body_model(annotation: object) -> Any | None:
    """The model a request body carries, including ``list[Model]`` and aliases."""

    annotation = _strip_wrappers(annotation)
    if _is_pydantic_model(annotation) or _is_dataclass_model(annotation):
        return annotation
    if get_origin(annotation) in (list, tuple, set, frozenset):
        args = get_args(annotation)
        item = _strip_wrappers(args[0]) if args else None
        if _is_pydantic_model(item) or _is_dataclass_model(item):
            return item
    return None


# ── Field trees ──────────────────────────────────────────────────────────────


def _constraints(annotation: object, field_info: object, name: str) -> list[str]:
    """Constraints from the outer ``Param``/``Body`` and any ``Annotated`` FieldInfo."""

    items: list[object] = list(getattr(field_info, "metadata", None) or ())
    _, metadata = _annotation_parts(_unwrap_optional(annotation))
    for item in metadata:
        items.extend(getattr(item, "metadata", None) or ())

    constraints: list[str] = []
    for item in items:
        text = repr(item)
        if text not in constraints:
            constraints.append(text)
    alias = getattr(field_info, "alias", None)
    if alias and alias != name:
        constraints.append(f"alias={alias}")
    return constraints


def _pydantic_default(field: FieldInfo) -> object:
    factory = field.default_factory
    if factory is not None:
        return _factory_value(
            factory, bool(getattr(field, "default_factory_takes_validated_data", False))
        )
    return _json_value(field.default)


def _fastapi_field_default(field: Any) -> object:
    # FastAPI's ``ModelField.default`` calls the factory on every access, so it
    # must not be read when a factory exists.
    info = field.field_info
    factory = getattr(info, "default_factory", None)
    if factory is not None:
        return _factory_value(
            factory, bool(getattr(info, "default_factory_takes_validated_data", False))
        )
    return _json_value(getattr(field, "default", PydanticUndefined))


_MODEL_CONFIG_KEYS = ("extra", "strict", "populate_by_name", "from_attributes")


def _model_config(model: type[BaseModel]) -> dict[str, object]:
    """Wire-relevant config only; pydantic fills defaults, so this is explicit settings."""

    config = getattr(model, "model_config", None) or {}
    return {key: _json_value(config[key]) for key in _MODEL_CONFIG_KEYS if key in config}


def _alias_value(alias: object) -> object:
    if isinstance(alias, str):
        return alias
    return repr(alias)


def _pydantic_fields(model: type[BaseModel], seen: frozenset[type]) -> dict[str, dict[str, Any]]:
    seen = seen | {model}
    fields: dict[str, dict[str, Any]] = {}
    for name, field in model.model_fields.items():
        required = field.is_required()
        entry: dict[str, Any] = {
            "annotation": _annotation_text(field.annotation),
            "required": required,
            "default": None if required else _pydantic_default(field),
            "constraints": _constraints(field.annotation, field, name),
        }
        for attribute in ("validation_alias", "serialization_alias"):
            alias = getattr(field, attribute, None)
            if alias is not None:
                entry[attribute] = _alias_value(alias)

        nested = _body_model(field.annotation)
        if nested is not None and nested in seen:
            entry["recursive"] = True
        elif nested is not None:
            nested_contract = _model_contract(nested, seen)
            entry["fields"] = nested_contract["fields"]
            if "config" in nested_contract:
                entry["config"] = nested_contract["config"]
        fields[name] = entry
    return fields


def _dataclass_fields(model: type, seen: frozenset[type]) -> dict[str, dict[str, Any]]:
    seen = seen | {model}
    try:
        hints = get_type_hints(model)
    except Exception:
        hints = {}

    fields: dict[str, dict[str, Any]] = {}
    for field in dataclasses.fields(model):
        annotation = hints.get(field.name, field.type)
        if field.default is not dataclasses.MISSING:
            required, default = False, _json_value(field.default)
        elif field.default_factory is not dataclasses.MISSING:
            required, default = False, _factory_value(field.default_factory)
        else:
            required, default = True, None

        entry: dict[str, Any] = {
            "annotation": _annotation_text(annotation),
            "required": required,
            "default": default,
            "constraints": _constraints(annotation, None, field.name),
        }
        nested = _body_model(annotation)
        if nested is not None and nested in seen:
            entry["recursive"] = True
        elif nested is not None:
            nested_contract = _model_contract(nested, seen)
            entry["fields"] = nested_contract["fields"]
            if "config" in nested_contract:
                entry["config"] = nested_contract["config"]
        fields[field.name] = entry
    return fields


def _model_contract(model: Any, seen: frozenset[type]) -> dict[str, Any]:
    """Fields plus the wire-relevant config of a request or response model."""

    if _is_pydantic_model(model):
        contract: dict[str, Any] = {"fields": _pydantic_fields(model, seen)}
        config = _model_config(model)
        if config:
            contract["config"] = config
        return contract
    if _is_dataclass_model(model):
        return {"fields": _dataclass_fields(model, seen)}
    return {"fields": {}}


# ── Route contract ───────────────────────────────────────────────────────────


def _field_contract(field: Any, location: str) -> dict[str, Any]:
    """One parameter exactly as FastAPI resolved it for this route."""

    info = field.field_info
    annotation = info.annotation
    required = info.is_required()
    contract: dict[str, Any] = {
        "in": location,
        "annotation": _annotation_text(annotation),
        "required": required,
        "default": None if required else _fastapi_field_default(field),
        "constraints": _constraints(annotation, info, field.name),
    }

    model = _body_model(annotation)
    if model is not None:
        contract.update(_model_contract(model, frozenset()))
    if location in {"body", "form", "file"} and getattr(info, "embed", None):
        contract["embed"] = True
    return contract


def _body_location(field_info: object) -> str:
    if isinstance(field_info, File):
        return "file"
    if isinstance(field_info, Form):
        return "form"
    return "body"


_PARAMETER_LISTS = (
    ("query_params", "query"),
    ("path_params", "path"),
    ("header_params", "header"),
    ("cookie_params", "cookie"),
)

_ParameterEntry = tuple[str, dict[str, Any], str]


def _collect_parameters(
    dependant: Any,
    entries: list[_ParameterEntry],
    source: str = "endpoint",
) -> None:
    """Flatten route params and every ``Depends`` layer below it.

    A parameter is identified by location and name, so the same name may appear
    as both a query parameter and a header. Entries are collected before keys
    are assigned, so a same-slot collision can be resolved consistently no
    matter how many sources expose it.
    """

    for attribute, location in _PARAMETER_LISTS:
        for field in getattr(dependant, attribute, None) or ():
            entries.append((f"{location}:{field.name}", _field_contract(field, location), source))

    for field in getattr(dependant, "body_params", None) or ():
        location = _body_location(field.field_info)
        entries.append((f"{location}:{field.name}", _field_contract(field, location), source))

    for child in getattr(dependant, "dependencies", None) or ():
        name = getattr(child.call, "__qualname__", "dependency")
        child_source = name if source == "endpoint" else f"{source}.{name}"
        _collect_parameters(child, entries, child_source)


def _merge_parameters(entries: list[_ParameterEntry]) -> dict[str, dict[str, Any]]:
    """Assign keys by ``location:name``; qualify every entry in a real collision.

    Identical contracts for the same slot are deduplicated. When contracts
    differ, *all* of them are keyed by source, so a third or later dependency
    cannot fall back to the bare key and leave the key set inconsistent.
    """

    grouped: dict[str, list[tuple[dict[str, Any], str]]] = {}
    for key, contract, source in entries:
        grouped.setdefault(key, []).append((contract, source))

    merged: dict[str, dict[str, Any]] = {}
    for key, items in grouped.items():
        unique: list[tuple[dict[str, Any], str]] = []
        for contract, source in items:
            if all(contract != seen for seen, _ in unique):
                unique.append((contract, source))

        if len(unique) == 1:
            merged[key] = unique[0][0]
            continue

        used: set[str] = set()
        for contract, source in unique:
            candidate = f"{key}@{source}"
            index = 2
            while candidate in used:
                candidate = f"{key}@{source}#{index}"
                index += 1
            used.add(candidate)
            merged[candidate] = contract
    return merged


def _response_contract(route: Any) -> dict[str, Any]:
    model = getattr(route, "response_model", None)
    declared = getattr(route, "responses", None) or {}
    contract: dict[str, Any] = {
        "model": None if model is None else _annotation_text(model),
        "status_code": getattr(route, "status_code", None) or 200,
        "declared": sorted(str(key) for key in declared),
    }
    if model is not None and _is_pydantic_model(model):
        contract.update(_model_contract(model, frozenset()))
    return contract


def _route_key(route: object) -> str:
    methods = ",".join(sorted(getattr(route, "methods", ()) or ()))
    return f"{methods} {getattr(route, 'path', '')}".strip()


def _route_contract(route: Any) -> dict[str, Any]:
    entries: list[_ParameterEntry] = []
    _collect_parameters(route.dependant, entries)
    parameters = _merge_parameters(entries)
    return {
        "endpoint": getattr(route.endpoint, "__name__", repr(route.endpoint)),
        "parameters": parameters,
        "response": _response_contract(route),
    }


def build_external_api_contract(routes: Iterable[object]) -> dict[str, Any]:
    """Snapshot every marked route's request and response contract.

    ``include_router`` is lazy in the pinned FastAPI version: a composed router
    holds include nodes rather than flat routes, so paths are resolved through
    ``iter_route_contexts`` before the marker is inspected.
    """

    external_routes: dict[str, dict[str, Any]] = {}
    for route in iter_route_contexts(list(routes)):
        if is_external_api_endpoint(route.endpoint):
            external_routes[_route_key(route)] = _route_contract(route)
    return {"schema": SNAPSHOT_SCHEMA, "routes": external_routes}


def dump_external_api_contract(contract: Mapping[str, Any]) -> str:
    """Serialise deterministically so the file only changes when the contract does."""

    return json.dumps(contract, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# ── Diff ─────────────────────────────────────────────────────────────────────


def _flatten_route(route: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Dotted-path view of a route, request-body and response fields included."""

    flat: dict[str, dict[str, Any]] = {}

    def visit(path: str, node: Mapping[str, Any]) -> None:
        flat[path] = {key: value for key, value in node.items() if key != "fields"}
        for name, child in (node.get("fields") or {}).items():
            visit(f"{path}.{name}", child)

    for name, node in (route.get("parameters") or {}).items():
        visit(name, node)
    response = route.get("response")
    if response:
        visit("response", response)
    return flat


def _describe(path: str) -> str:
    if path == "response":
        return "response"
    if path.startswith("response."):
        return f"response field '{path.removeprefix('response.')}'"
    return f"parameter '{path}'"


def _diff_entry(
    route_key: str, path: str, expected: Mapping[str, Any], actual: Mapping[str, Any]
) -> list[str]:
    return [
        f"{route_key}: {_describe(path)} {field} changed: "
        f"{expected.get(field)!r} -> {actual.get(field)!r}"
        for field in sorted(set(expected) | set(actual))
        if expected.get(field) != actual.get(field)
    ]


def diff_external_api_contract(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> list[str]:
    """Human-readable differences between the committed and running contracts."""

    expected_schema = expected.get("schema") if isinstance(expected, Mapping) else None
    actual_schema = actual.get("schema") if isinstance(actual, Mapping) else None
    if expected_schema != SNAPSHOT_SCHEMA or actual_schema != SNAPSHOT_SCHEMA:
        raise ValueError(
            "contract schema mismatch: "
            f"snapshot={expected_schema!r}, runtime={actual_schema!r}, "
            f"expected={SNAPSHOT_SCHEMA}"
        )

    expected_routes = expected.get("routes") if isinstance(expected, Mapping) else None
    actual_routes = actual.get("routes") if isinstance(actual, Mapping) else None
    if not isinstance(expected_routes, Mapping) or not isinstance(actual_routes, Mapping):
        raise ValueError("contract snapshot has no 'routes' object")

    issues: list[str] = []
    for route_key in sorted(set(expected_routes) | set(actual_routes)):
        if route_key not in actual_routes:
            issues.append(f"{route_key}: external API route removed")
            continue
        if route_key not in expected_routes:
            issues.append(f"{route_key}: external API route added (update the snapshot)")
            continue

        expected_entries = _flatten_route(expected_routes[route_key])
        actual_entries = _flatten_route(actual_routes[route_key])
        for path in sorted(set(expected_entries) | set(actual_entries)):
            if path not in actual_entries:
                issues.append(f"{route_key}: {_describe(path)} removed")
            elif path not in expected_entries:
                issues.append(f"{route_key}: {_describe(path)} added")
            else:
                issues.extend(
                    _diff_entry(route_key, path, expected_entries[path], actual_entries[path])
                )
    return issues


def verify_external_api_contract(
    routes: Iterable[object], snapshot_path: Path | None = None
) -> list[str]:
    """Compare marked routes with the committed snapshot.

    Called once while the application is built:

    - missing, unreadable, or structurally invalid snapshot → ``RuntimeError``
      (a disabled guard must not pass silently);
    - contract drift → one warning per change, so a normal parameter update
      does not block a release that also updates the snapshot.
    """

    path = snapshot_path or SNAPSHOT_PATH
    try:
        expected = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"external API contract snapshot missing: {path}") from exc
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"external API contract snapshot unreadable: {path}: {exc}") from exc

    try:
        issues = diff_external_api_contract(expected, build_external_api_contract(routes))
    except (AttributeError, ValueError) as exc:
        raise RuntimeError(f"external API contract snapshot invalid: {path}: {exc}") from exc

    for issue in issues:
        logger.warning("external API contract changed: %s", issue)
    return issues


__all__ = [
    "EXTERNAL_API_MARKER",
    "SNAPSHOT_PATH",
    "build_external_api_contract",
    "diff_external_api_contract",
    "dump_external_api_contract",
    "is_external_api_endpoint",
    "external_api",
    "verify_external_api_contract",
]
