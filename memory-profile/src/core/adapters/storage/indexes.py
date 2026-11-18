"""Read-only mirror of the API-owned Elasticsearch / Neo4j index definitions.

This module is the single index registry of memory-profile. The writer owns every
index and its schema; this service only reads them. Rather than hand-copying
aliases and field names (which drifts silently), the registry loads
``storage_index_contract.json`` — a snapshot generated from the API source by
``scripts/sync_storage_index_contract.py`` and verified in CI. Regenerate the
snapshot whenever the API-side definitions change; the import-time validation
below fails fast if the file is missing, malformed, or internally inconsistent.

What the storage/retrieval code uses:

* Elasticsearch aliases (``{index_name}_current``), full-text fields, embedding
  fields and the supported dense-vector dimensions;
* the writer's default node projection (``DEFAULT_PROJECTION``);
* Neo4j full-text index names and embedding property names;
* ``ELASTIC_ONLY_LABELS`` (labels that exist only in Elasticsearch).

The managed Neo4j index/constraint definitions and the writer-internal
consistency report are mirrored for diagnostics and drift tests; readers do not
create indexes.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from src.core.domain.enums import MemorySource

logger = logging.getLogger(__name__)

CONTRACT_SCHEMA = 1
CONTRACT_PATH = Path(__file__).with_name("storage_index_contract.json")


class StorageIndexContractError(RuntimeError):
    """The committed index-contract snapshot is missing or unusable."""


def _load(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:  # pragma: no cover - packaging guard
        raise StorageIndexContractError(
            f"storage index contract missing: {path}"
        ) from exc
    except OSError as exc:  # pragma: no cover - filesystem failure
        raise StorageIndexContractError(
            f"storage index contract unreadable: {path}: {exc}"
        ) from exc
    try:
        contract = json.loads(raw)
    except ValueError as exc:
        raise StorageIndexContractError(
            f"storage index contract invalid JSON: {path}: {exc}"
        ) from exc
    if not isinstance(contract, dict):
        raise StorageIndexContractError(
            f"storage index contract must be a JSON object: {path}"
        )
    return contract


def _require_mapping(parent: Mapping[str, Any], key: str, path: Path) -> Mapping[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise StorageIndexContractError(f"{path}: missing object {key!r}")
    return value


def _validate(contract: Mapping[str, Any], path: Path) -> None:
    if contract.get("schema") != CONTRACT_SCHEMA:
        raise StorageIndexContractError(
            f"{path}: unsupported schema {contract.get('schema')!r} "
            f"(expected {CONTRACT_SCHEMA})"
        )
    elasticsearch = _require_mapping(contract, "elasticsearch", path)
    definitions = _require_mapping(elasticsearch, "index_definitions", path)
    if not definitions:
        raise StorageIndexContractError(f"{path}: no Elasticsearch index definitions")

    names: set[str] = set()
    aliases: set[str] = set()
    alias_suffix = elasticsearch.get("alias_suffix")
    if not isinstance(alias_suffix, str) or not alias_suffix:
        raise StorageIndexContractError(f"{path}: invalid index alias suffix")
    for label, definition in definitions.items():
        if not isinstance(definition, dict):
            raise StorageIndexContractError(f"{path}: invalid definition for {label}")
        name = definition.get("name")
        alias = definition.get("alias")
        if not isinstance(name, str) or not isinstance(alias, str):
            raise StorageIndexContractError(f"{path}: invalid index name/alias for {label}")
        if alias != f"{name}{alias_suffix}":
            raise StorageIndexContractError(
                f"{path}: alias {alias!r} for {label} must be {name}{alias_suffix}"
            )
        if name in names or alias in aliases:
            raise StorageIndexContractError(
                f"{path}: duplicate index name/alias for {label}"
            )
        names.add(name)
        aliases.add(alias)
        for field in ("schema_version", "generation"):
            value = definition.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise StorageIndexContractError(
                    f"{path}: invalid {field} for {label}"
                )
        if not isinstance(definition.get("mappings"), dict):
            raise StorageIndexContractError(f"{path}: missing mappings for {label}")

    dimensions = elasticsearch.get("supported_embedding_dimensions")
    default_dimension = elasticsearch.get("default_embedding_dimension")
    if (
        not isinstance(dimensions, list)
        or not dimensions
        or not all(isinstance(item, int) and not isinstance(item, bool) for item in dimensions)
    ):
        raise StorageIndexContractError(f"{path}: invalid embedding dimensions")
    if default_dimension not in dimensions:
        raise StorageIndexContractError(
            f"{path}: default embedding dimension is not supported"
        )

    for section in ("fulltext_fields", "embedding_fields"):
        values = _require_mapping(elasticsearch, section, path)
        unknown = set(values) - set(definitions)
        if unknown:
            raise StorageIndexContractError(
                f"{path}: {section} references undefined labels {sorted(unknown)}"
            )

    neo4j = _require_mapping(contract, "neo4j", path)
    read = _require_mapping(neo4j, "read_definitions", path)
    for key in ("fulltext_indexes", "embedding_fields"):
        section = _require_mapping(read, key, path)
        unknown = set(section) - set(definitions)
        if unknown:
            raise StorageIndexContractError(
                f"{path}: neo4j.{key} references undefined labels {sorted(unknown)}"
            )
    managed = _require_mapping(neo4j, "managed_definitions", path)
    for key in (
        "fulltext_indexes",
        "vector_indexes",
        "range_indexes",
        "composite_indexes",
        "delete_at_indexes",
        "constraints",
        "composite_constraints",
    ):
        if not isinstance(managed.get(key), list):
            raise StorageIndexContractError(
                f"{path}: neo4j.managed_definitions.{key} must be a list"
            )

    projections = _require_mapping(
        _require_mapping(contract, "projections", path), "default", path
    )
    unknown = set(projections) - set(definitions)
    if unknown:
        raise StorageIndexContractError(
            f"{path}: projections references undefined labels {sorted(unknown)}"
        )
    for label, fields in projections.items():
        if not isinstance(fields, list) or not fields:
            raise StorageIndexContractError(
                f"{path}: projection for {label} must be a non-empty list"
            )
        for item in fields:
            if not isinstance(item, dict):
                raise StorageIndexContractError(
                    f"{path}: projection item for {label} must be an object"
                )
            if "coalesce" in item:
                if not isinstance(item.get("coalesce"), list) or not item.get("coalesce"):
                    raise StorageIndexContractError(
                        f"{path}: coalesce projection for {label} has no fields"
                    )
                if not isinstance(item.get("alias"), str) or not item["alias"]:
                    raise StorageIndexContractError(
                        f"{path}: coalesce projection for {label} has no alias"
                    )
            elif not isinstance(item.get("field"), str) or not item["field"]:
                raise StorageIndexContractError(
                    f"{path}: projection field for {label} is invalid"
                )


CONTRACT = _load(CONTRACT_PATH)
_validate(CONTRACT, CONTRACT_PATH)

_ELASTICSEARCH = CONTRACT["elasticsearch"]
_NEO4J = CONTRACT["neo4j"]

#: Full Elasticsearch index definitions keyed by node label value.
ES_INDEX_DEFINITIONS: dict[str, dict[str, Any]] = dict(
    _ELASTICSEARCH["index_definitions"]
)
#: Stable read alias per node label (``{name}_current``).
ES_INDEX_ALIAS: dict[str, str] = {
    label: definition["alias"] for label, definition in ES_INDEX_DEFINITIONS.items()
}
#: Analyzed full-text fields per node label.
ES_FULLTEXT_FIELDS: dict[str, tuple[str, ...]] = {
    label: tuple(fields) for label, fields in _ELASTICSEARCH["fulltext_fields"].items()
}
#: Dimension-agnostic embedding property per node label.
ES_EMBEDDING_FIELD: dict[str, str] = dict(_ELASTICSEARCH["embedding_fields"])
#: Shard count the writer uses for every index (diagnostics only).
ES_INDEX_SHARD_COUNT: int = int(_ELASTICSEARCH["index_shard_count"])
#: Default dense-vector dimension (un-suffixed field name).
DEFAULT_EMBEDDING_DIMENSION: int = int(_ELASTICSEARCH["default_embedding_dimension"])
#: Every dense-vector dimension the writer indexes.
SUPPORTED_EMBEDDING_DIMENSIONS: tuple[int, ...] = tuple(
    _ELASTICSEARCH["supported_embedding_dimensions"]
)

#: Full-text index name per node label on the Neo4j side.
NEO4J_FULLTEXT_INDEX: dict[str, str] = dict(
    _NEO4J["read_definitions"]["fulltext_indexes"]
)
#: Embedding property per node label on the Neo4j side.
NEO4J_EMBEDDING_FIELD: dict[str, str] = dict(
    _NEO4J["read_definitions"]["embedding_fields"]
)
#: Every index/constraint the writer manages (diagnostics; readers never create).
NEO4J_MANAGED_DEFINITIONS: dict[str, list[dict[str, Any]]] = dict(
    _NEO4J["managed_definitions"]
)
#: Writer-internal consistency report (its own two registries disagree in places).
WRITER_CONSISTENCY_REPORT: dict[str, list[Any]] = dict(CONTRACT["consistency"])
#: The writer's default node projection: the exact fields each read family returns.
DEFAULT_PROJECTION_SPECS: dict[str, list[dict[str, Any]]] = dict(
    CONTRACT["projections"]["default"]
)

#: Labels that exist only in Elasticsearch and never fall back to Neo4j.
ELASTIC_ONLY_LABELS: frozenset[str] = frozenset(
    CONTRACT["routing"]["elastic_only_labels"]
)

#: Every label the writer's index registry knows about.
INDEX_CONTRACT_LABELS: frozenset[str] = frozenset(ES_INDEX_DEFINITIONS)


def es_embedding_field(label: str, dimension: int) -> str:
    """Resolve the dense-vector field name for one label and dimension.

    The default dimension keeps the un-suffixed property name; every other
    supported dimension uses ``{field}_{dimension}``, matching the writer.
    """

    field = ES_EMBEDDING_FIELD.get(label)
    if field is None:
        raise KeyError(f"node type {label} has no embedding field")
    if dimension == DEFAULT_EMBEDDING_DIMENSION:
        return field
    if dimension not in SUPPORTED_EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"unsupported embedding dimension {dimension}; "
            f"expected one of {SUPPORTED_EMBEDDING_DIMENSIONS}"
        )
    return f"{field}_{dimension}"


def labels_unknown_to_domain(labels: Iterable[str] | None = None) -> list[str]:
    """Contract labels this service's domain enum does not know about yet.

    A new writer label (or a renamed one) must be added to
    ``src.core.domain.enums.MemorySource``; the drift test asserts this list is
    empty so the gap is caught in CI instead of at query time.
    """

    known = {member.value for member in MemorySource}
    return sorted(set(labels if labels is not None else INDEX_CONTRACT_LABELS) - known)


_unknown = labels_unknown_to_domain()
if _unknown:  # pragma: no cover - drift guard; normally asserted by tests
    logger.warning(
        "storage_index_contract_unknown_labels labels=%s; extend MemorySource",
        _unknown,
    )

__all__ = [
    "CONTRACT_PATH",
    "CONTRACT_SCHEMA",
    "DEFAULT_EMBEDDING_DIMENSION",
    "DEFAULT_PROJECTION_SPECS",
    "ELASTIC_ONLY_LABELS",
    "ES_EMBEDDING_FIELD",
    "ES_FULLTEXT_FIELDS",
    "ES_INDEX_ALIAS",
    "ES_INDEX_DEFINITIONS",
    "ES_INDEX_SHARD_COUNT",
    "INDEX_CONTRACT_LABELS",
    "NEO4J_EMBEDDING_FIELD",
    "NEO4J_FULLTEXT_INDEX",
    "NEO4J_MANAGED_DEFINITIONS",
    "SUPPORTED_EMBEDDING_DIMENSIONS",
    "StorageIndexContractError",
    "WRITER_CONSISTENCY_REPORT",
    "es_embedding_field",
    "labels_unknown_to_domain",
]