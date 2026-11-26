"""Synchronous Elasticsearch projection of authoritative Neo4j memory nodes.

``MemoryProjection`` is the memory-jobs port of the API's storage-outbox
projector (``api/app/core/memory/storage/outbox/clients.py::project_event``) and
of the create-if-missing half of its index bootstrap
(``.../provider/elasticsearch/index/__init__.py``). The observable behavior is
kept identical because during the two-phase rollout both services project the
same rows:

* Neo4j stays authoritative: an upsert re-reads the node, a missing node
  converges to a document deletion, and an unreadable node fails the event
  instead of being mistaken for a deletion;
* ``check_claim`` runs after the read and before any Elasticsearch write, so a
  lease lost during a slow round-trip cannot overwrite a document another worker
  now owns;
* every Elasticsearch acknowledgement is verified, never assumed.

The API keeps owning schema migrations (reindex, additive mapping, downgrade).
``warm_up`` therefore only creates an index that does not exist yet, with the
same payload the API's own ``_run_migration`` would write, and leaves an
existing one untouched. A mismatch between an existing index and this
deployment's definitions is logged and skipped: this service must not mutate a
schema the API owns.

Everything here is blocking, on purpose: Celery runs these tasks in prefork or
threads workers and the shared managers own the connection pools.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from memory_storage_schema.definitions import (
    INDEX_DEFINITIONS,
    IndexDefinition,
    get_index_name,
    validate_definition_registry,
)
from memory_storage_schema.labels import MemoryNodeType
from memory_storage_schema.serialization import normalize_document

from src.application.ports.outbox.outbox import OutboxEvent
from src.infrastructure.outbox.model import DELETE, DRAFT_DELETE, UPSERT

if TYPE_CHECKING:
    from elasticsearch import Elasticsearch

    from src.config import JobsSettings
    from src.infrastructure.elasticsearch import ElasticsearchManager
    from src.infrastructure.neo4j import Neo4jManager

logger = logging.getLogger(__name__)

#: ``_meta`` key carrying the schema identity stamped into every physical index.
#: It is part of the API's index contract (``INDEX_SCHEMA_META_KEY`` there): the
#: API reads it back to decide whether an existing index is current, so a created
#: index must carry it or the API would rebuild the index on its next start-up.
INDEX_SCHEMA_META_KEY = "redbear_memory_storage"

#: Warm-up is a per-process one-time check: the API caches the same state in
#: ``_indices_ready``. The lock makes concurrent threads (Celery ``threads``
#: pool) run the existence checks once, while a failure leaves the flag unset so
#: the next event retries instead of projecting against a missing index.
_warm_lock = threading.Lock()
_indices_ready = False

#: The only Cypher this adapter runs. ``label`` is interpolated from the
#: :class:`MemoryNodeType` enum value — the enum, not the event text, is the
#: whitelist, exactly like the API's provider query.
_NODE_QUERY = "MATCH (n:{label}) WHERE n.id = $id RETURN n"


class MemoryProjection:
    """Project one outbox event by re-reading Neo4j and writing Elasticsearch."""

    def __init__(
        self,
        *,
        elasticsearch: ElasticsearchManager,
        neo4j: Neo4jManager,
        settings: JobsSettings,
    ) -> None:
        self._elasticsearch = elasticsearch
        self._neo4j = neo4j
        #: Held so the composition root can pass one settings object to every
        #: domain adapter; no outbox knob is currently read by the projection.
        self._settings = settings

    # --------------------------------------------------------------- bootstrap

    def warm_up(self) -> None:
        """Create the Elasticsearch indices a fresh cluster is missing.

        Best-effort and one-time per process: a failure propagates so the event
        that triggered it is retried, and the next call retries the checks
        because the ready flag is only set after every label succeeded.
        """

        global _indices_ready
        if _indices_ready:
            return
        with _warm_lock:
            if _indices_ready:
                return
            self._ensure_indices()
            _indices_ready = True

    def _ensure_indices(self) -> None:
        validate_definition_registry()
        client = self._elasticsearch.client()
        for label, definition in INDEX_DEFINITIONS.items():
            alias = get_index_name(label)
            current_index = _get_alias_target(client, alias)
            if current_index is None:
                self._create_index(client, label, definition)
                continue
            # The API owns migrations. An index that does not match this
            # deployment's definitions (older generation, additive field, newer
            # schema) is left exactly as it is; reindexing here would fight the
            # API for the same alias.
            try:
                _validate_index(client, label, current_index)
            except RuntimeError as exc:
                logger.warning(
                    "Existing Elasticsearch index does not match this deployment; "
                    "leaving it for the API to migrate alias=%s index=%s error=%s",
                    alias,
                    current_index,
                    exc,
                    extra={
                        "event": "outbox_index_incompatible",
                        "exception_type": type(exc).__name__,
                    },
                )

    def _create_index(
        self,
        client: Elasticsearch,
        label: MemoryNodeType,
        definition: IndexDefinition,
    ) -> None:
        """Create one missing physical index and point its alias at it.

        The payload mirrors the API's migration: definition settings, mappings
        with the schema identity ``_meta``, and the alias added with
        ``is_write_index``. Writing them in one create request means a crash can
        never leave a physical index the alias does not reference.
        """

        physical_name = _new_physical_index_name(definition)
        result = client.indices.create(
            index=physical_name,
            settings=definition.settings,
            mappings=_versioned_mappings(label, definition),
            aliases={definition.alias: {"is_write_index": True}},
        )
        if result.get("acknowledged") is not True:
            raise RuntimeError(
                f"Elasticsearch index creation for '{definition.alias}' "
                "was not acknowledged"
            )
        # The API validates what Elasticsearch actually stored before it starts
        # using an index; a create that silently dropped mappings must fail the
        # retry instead of being projected into.
        _validate_index(client, label, physical_name)

    # --------------------------------------------------------------- projection

    def project(
        self,
        event: OutboxEvent,
        check_claim: Callable[[], None],
    ) -> None:
        """Project one event; raise to retry it or when the lease is gone."""

        # ``MemoryNodeType`` is the whitelist: an unexpected label fails the
        # event before any query text is built from it.
        label = MemoryNodeType(event.label)

        if event.operation == DELETE:
            # A physical delete has no node left to read; delete the document.
            check_claim()
            self._delete_document(label, event.node_id)
            return

        if event.operation not in (UPSERT, DRAFT_DELETE):
            raise ValueError(f"unknown outbox operation: {event.operation!r}")

        # Upserts and soft deletes are both projected from the current Neo4j
        # state: a softly deleted node still exists and carries ``delete_at``.
        data = self._read_authoritative_node(label, event)
        # A slow read must not write after the lease moved to another worker.
        check_claim()
        if data is not None:
            self._index_document(label, event.node_id, data)
        else:
            # The node no longer exists: the deletion wins over the stale event.
            self._delete_document(label, event.node_id)

    def _read_authoritative_node(
        self,
        label: MemoryNodeType,
        event: OutboxEvent,
    ) -> dict[str, Any] | None:
        """Return the node's properties, or ``None`` when it does not exist."""

        query = _NODE_QUERY.format(label=label.value)
        with self._neo4j.session(access_mode="READ") as session:
            records = session.run(query, id=event.node_id).data()
        nodes = [_to_native(record["n"]) for record in records]

        # ``n.id`` is the unique business key, but a corrupted graph could still
        # return more than one node; refusing is safer than picking one.
        if len(nodes) > 1:
            raise ValueError("Invalid or ambiguous authoritative node result")
        if not nodes:
            return None
        data = nodes[0]
        if not isinstance(data, Mapping) or data.get("id") != event.node_id:
            raise ValueError("Authoritative node identity mismatch")
        return dict(data)

    def _index_document(
        self,
        label: MemoryNodeType,
        node_id: str,
        data: Mapping[str, Any],
    ) -> None:
        document = normalize_document(label, data)
        result = self._elasticsearch.client().index(
            index=get_index_name(label),
            id=str(node_id),
            document=document,
            refresh="wait_for",
        )
        _raise_on_response_failures(result, "index")
        if result.get("result") not in {"created", "updated"}:
            raise RuntimeError("Elasticsearch index acknowledgement missing")

    def _delete_document(self, label: MemoryNodeType, node_id: str) -> None:
        result = self._elasticsearch.client().delete_by_query(
            index=get_index_name(label),
            query={"term": {"id": node_id}},
            conflicts="abort",
            refresh=True,
        )
        _raise_on_response_failures(result, "delete_by_query")
        deleted = result.get("deleted")
        if (
            not isinstance(deleted, int)
            or isinstance(deleted, bool)
            or deleted < 0
        ):
            raise RuntimeError("Elasticsearch delete acknowledgement missing")


# --------------------------------------------------------------- value helpers


def _to_native(value: Any) -> Any:
    """Convert one Neo4j driver value into native JSON-compatible Python.

    ``Result.data()`` already turns nodes and relationships into property
    dictionaries; temporal, spatial and byte driver types keep their wrapper and
    expose ``to_native()``. Containers recurse so nested temporal values cannot
    reach Elasticsearch as non-serializable objects.
    """

    if value is None:
        return value
    if hasattr(value, "to_native"):
        return value.to_native()
    if isinstance(value, Mapping):
        return {key: _to_native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_native(item) for item in value]
    return value


def _raise_on_response_failures(result: Mapping[str, Any], operation: str) -> None:
    """Reject a bulk response that timed out, failed or conflicted."""

    if result.get("timed_out"):
        raise RuntimeError(f"Elasticsearch {operation} timed out")
    failures = result.get("failures") or []
    if failures:
        raise RuntimeError(f"Elasticsearch {operation} failures: {failures!r}")
    version_conflicts = int(result.get("version_conflicts", 0) or 0)
    if version_conflicts:
        raise RuntimeError(
            f"Elasticsearch {operation} had {version_conflicts} version conflicts"
        )
    shards = result.get("_shards") or {}
    if isinstance(shards, Mapping) and int(shards.get("failed", 0) or 0):
        raise RuntimeError(
            f"Elasticsearch {operation} shard failures: "
            f"{shards.get('failures', [])!r}"
        )


# -------------------------------------------------------------- index bootstrap


def _display_value(value: Any) -> str:
    return str(value)


def _contains_definition(actual: Any, expected: Any) -> bool:
    """Whether ``actual`` contains every value ``expected`` requires.

    Ported from the API's index bootstrap: Elasticsearch echoes back normalized
    mappings/settings, so the comparison must be containment, not equality.
    """

    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            return False
        return all(
            (
                key in actual
                and _contains_definition(actual[key], value)
            )
            or (key not in actual and value in ({}, []))
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and all(
            any(_contains_definition(item, required) for item in actual)
            for required in expected
        )
    return actual == expected


def _canonical_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}

    def visit(value: Any, prefix: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                child_prefix = f"{prefix}.{key}" if prefix else str(key)
                visit(child, child_prefix)
            return
        canonical_key = prefix.removeprefix("index.")
        flattened[canonical_key] = value

    visit(settings, "")
    return flattened


def _settings_contain_definition(
    actual: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> bool:
    actual_flat = _canonical_settings(actual)
    expected_flat = _canonical_settings(expected)
    return all(
        key in actual_flat
        and str(actual_flat[key]).lower() == str(value).lower()
        for key, value in expected_flat.items()
    )


def _versioned_mappings(
    label: MemoryNodeType,
    definition: IndexDefinition,
) -> dict[str, Any]:
    """Return the definition's mappings stamped with the schema identity.

    The stamp is what lets the API adopt an index created here: without it,
    ``_get_update_action`` refuses the index and rebuilds it.
    """

    mappings = deepcopy(definition.mappings)
    metadata = mappings.setdefault("_meta", {})
    if not isinstance(metadata, dict):
        raise RuntimeError(
            f"Elasticsearch index definition '{definition.name}' _meta "
            "must be a mapping"
        )
    metadata[INDEX_SCHEMA_META_KEY] = {
        "label": label.name,
        "schema_version": definition.schema_version,
        "generation": definition.generation,
    }
    return mappings


def _new_physical_index_name(definition: IndexDefinition) -> str:
    """Name a fresh physical index the way the API's migration does."""

    return f"{definition.name}_g{definition.generation}_{uuid4().hex[:12]}"


def _single_index_payload(
    response: Mapping[str, Any],
    requested_name: str,
    operation: str,
) -> tuple[str, Mapping[str, Any]]:
    if requested_name in response:
        payload = response[requested_name]
        if isinstance(payload, Mapping):
            return requested_name, payload
    items = [
        (name, payload)
        for name, payload in response.items()
        if isinstance(payload, Mapping)
    ]
    if len(items) != 1:
        raise RuntimeError(
            f"Elasticsearch {operation} for '{requested_name}' returned "
            f"{len(items)} indices"
        )
    return items[0]


def _get_alias_target(client: Elasticsearch, alias: str) -> str | None:
    if not client.indices.exists_alias(name=alias):
        return None
    response = client.indices.get_alias(name=alias)
    targets = list(response)
    if len(targets) != 1:
        raise RuntimeError(
            f"Elasticsearch alias '{alias}' must resolve to exactly one index; "
            f"found {len(targets)}"
        )
    return str(targets[0])


def _get_mappings(
    client: Elasticsearch,
    index_name: str,
) -> tuple[str, Mapping[str, Any]]:
    response = client.indices.get_mapping(index=index_name)
    physical_name, payload = _single_index_payload(
        response, index_name, "get_mapping"
    )
    mappings = payload.get("mappings", {})
    if not isinstance(mappings, Mapping):
        raise RuntimeError(
            f"Elasticsearch index '{physical_name}' returned invalid mappings"
        )
    return physical_name, mappings


def _schema_identity(
    mappings: Mapping[str, Any],
) -> tuple[str, int, int] | None:
    metadata = mappings.get("_meta", {})
    if not isinstance(metadata, Mapping):
        return None
    storage_metadata = metadata.get(INDEX_SCHEMA_META_KEY, {})
    if not isinstance(storage_metadata, Mapping):
        return None
    label = storage_metadata.get("label")
    if not isinstance(label, str):
        return None

    schema_version = storage_metadata.get("schema_version")
    generation = storage_metadata.get("generation")
    if schema_version is None and generation is None:
        legacy_version = storage_metadata.get("version")
        if (
            isinstance(legacy_version, int)
            and not isinstance(legacy_version, bool)
            and legacy_version > 0
        ):
            return label, legacy_version, legacy_version
        return None
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or not isinstance(generation, int)
        or isinstance(generation, bool)
        or schema_version < 1
        or generation < 1
    ):
        return None
    return label, schema_version, generation


def _validate_index(
    client: Elasticsearch,
    label: MemoryNodeType,
    index_name: str | None = None,
) -> str:
    """Check that one index matches this deployment's definition.

    Only the read-only subset of the API's validation is ported: the worker
    reports a mismatch and leaves the index for the API to migrate, so this
    function never writes.
    """

    definition = INDEX_DEFINITIONS[label]
    requested_name = index_name or definition.alias
    settings_response = client.indices.get_settings(index=requested_name)
    physical_name, settings_payload = _single_index_payload(
        settings_response, requested_name, "get_settings"
    )
    index_settings = settings_payload.get("settings", {})
    shard_count = (
        index_settings.get("index", {}).get("number_of_shards")
        if isinstance(index_settings, Mapping)
        else None
    )
    expected_shards = definition.settings.get("number_of_shards")
    if _display_value(shard_count) != _display_value(expected_shards):
        raise RuntimeError(
            f"Elasticsearch index '{physical_name}' must have "
            f"{_display_value(expected_shards)} primary shards, found "
            f"{_display_value(shard_count)}; increment generation to rebuild it"
        )
    if not _settings_contain_definition(index_settings, definition.settings):
        raise RuntimeError(
            f"Elasticsearch index '{physical_name}' does not contain its "
            "configured settings; increment generation to rebuild it"
        )

    mapped_name, mappings = _get_mappings(client, requested_name)
    if mapped_name != physical_name:
        raise RuntimeError(
            "Elasticsearch settings and mappings resolved to different indices "
            f"for '{requested_name}'"
        )
    expected_identity = (
        label.name,
        definition.schema_version,
        definition.generation,
    )
    actual_identity = _schema_identity(mappings)
    if actual_identity != expected_identity:
        raise RuntimeError(
            f"Elasticsearch index '{physical_name}' has schema identity "
            f"{_display_value(actual_identity)}, expected "
            f"{str(expected_identity)}"
        )
    if not _contains_definition(mappings, definition.mappings):
        raise RuntimeError(
            f"Elasticsearch index '{physical_name}' does not contain its "
            "configured mapping; increment schema_version for additive fields "
            "or generation for incompatible changes"
        )
    return physical_name


__all__ = ["INDEX_SCHEMA_META_KEY", "MemoryProjection"]