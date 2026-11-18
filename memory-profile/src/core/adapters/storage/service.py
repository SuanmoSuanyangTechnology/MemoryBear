"""Read-only Neo4j / Elasticsearch access through the ported host storage layer.

This module used to *be* the storage substrate: it carried the filter/projection
compilers, a hand-written Elasticsearch client and a hand-written Neo4j client.
Those now live in :mod:`src.storage` (a byte-faithful port of the host
``api/app/core/memory/storage`` package, write surface removed) and this module is
the thin seam that keeps the read engine's interface stable:

* :class:`ReadStorage` resolves the two process-owned clients once — the Neo4j
  driver and the Elasticsearch client of the runtime, so no second pool is opened
  — hands them to the ported :class:`~src.storage.provider.factory.BackendFactory`
  and reads through :class:`~src.storage.router.read_router.ReadRouter` (node
  lookups, relationship traversals) and the clients' batched search APIs
  (``search_many_by_fulltext`` / ``search_many_by_embedding``), exactly like the
  host's ``read_router._run_grouped_search``;
* the core DSL stays in :mod:`.filters` / :mod:`.projection` (the application layer
  builds its criteria with it) and is translated by :mod:`.dsl_bridge`; results
  come back as :class:`~src.core.adapters.storage.results.StorageReadResult`.

Behaviour kept from the previous implementation:

* ``MEMORY_READ_BACKEND`` selects the graph read backend (``ELASTIC`` default) and
  the RAG memory label always reads from Elasticsearch;
* a label without an index on the routed backend yields an empty result instead of
  raising (the ported clients raise ``UnsupportedQueryError`` for such labels);
* labels keep their per-label projections, so one multi-label call is still one
  Elasticsearch ``_msearch`` per client.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.core.adapters.storage.dsl_bridge import (
    to_backend_filter,
    to_backend_label,
    to_backend_projection,
    to_backend_relationship_filter,
    to_backend_relationship_pattern,
    to_backend_relationship_projection,
    to_core_result,
)
from src.core.adapters.storage.filters import NodeFilter
from src.core.adapters.storage.indexes import (
    ES_FULLTEXT_FIELDS,
    ES_INDEX_ALIAS,
    NEO4J_EMBEDDING_FIELD,
    NEO4J_FULLTEXT_INDEX,
    es_embedding_field,
)
from src.core.adapters.storage.projection import (
    NodeProjection,
    RelationshipProjectionField,
    projection_for,
)
from src.core.adapters.storage.results import (
    StorageItem,
    StorageReadResult,
    merge_read_results,
)
from src.storage.enums import BackendType, MemoryNodeType, StorageBackendType
from src.storage.models import NodeSearchSpec
from src.storage.provider.base import BaseClient
from src.storage.provider.elasticsearch.client import ElasticClient
from src.storage.provider.factory import BackendFactory
from src.storage.provider.neo4j.client import Neo4jClient, configure_neo4j_manager
from src.storage.router.read_router import ReadRouter

logger = logging.getLogger(__name__)

DEFAULT_READ_BACKEND = "ELASTIC"


# ────────────────────────────────────────────────────────────────
# Core-side input value objects (the application layer builds these)
# ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RelationshipPattern:
    """Graph traversal pattern (host ``storage.models.pattern``)."""

    source_label: str
    target_label: str
    relationship_type: str | None = None
    directed: bool = True


@dataclass(frozen=True, slots=True)
class RelationshipFilter:
    """Scoped filters for a relationship traversal; ``None`` = no constraint."""

    source: NodeFilter | None = None
    target: NodeFilter | None = None
    relationship: NodeFilter | None = None


# ────────────────────────────────────────────────────────────────
# Routing
# ────────────────────────────────────────────────────────────────


class _ReadFactory(BackendFactory):
    """``BackendFactory`` with the profile-owned clients injected.

    The ported factory can build its own clients from settings
    (``BackendFactory.create()``). The profile must not do that: it already owns
    one driver and one Elasticsearch client per process, and opening a second
    Elasticsearch client would also run the host's unguarded index ensure outside
    the startup bootstrap. Injecting the instances keeps the factory's routing
    (``MEMORY_READ_BACKEND``, ``ELASTIC_ONLY_LABELS``) untouched.
    """

    def __init__(
        self,
        *,
        read_backend: BackendType,
        clients: Mapping[BackendType, BaseClient],
    ) -> None:
        super().__init__()
        self._clients = dict(clients)
        self._read_backend = read_backend


def _resolve_read_backend(runtime: Any, read_backend: str | None) -> str:
    """Resolve the graph read backend: argument → settings → env → default."""

    if read_backend:
        return read_backend
    settings = getattr(runtime, "settings", None)
    configured = getattr(settings, "memory_read_backend", None)
    if configured:
        return str(configured)
    return os.environ.get("MEMORY_READ_BACKEND", DEFAULT_READ_BACKEND)


class ReadStorage:
    """Route read-DSL calls onto the ported storage clients."""

    def __init__(
        self,
        *,
        runtime: Any = None,
        read_backend: str | None = None,
        clients: Mapping[BackendType, BaseClient] | None = None,
    ) -> None:
        resolved = _resolve_read_backend(runtime, read_backend)
        try:
            self._backend = BackendType(str(resolved).strip().upper())
        except ValueError:
            raise ValueError(
                f"Invalid memory read backend {resolved!r}; expected one of: "
                f"{', '.join(member.value for member in BackendType)}"
            ) from None
        if runtime is None and clients is None:
            raise ValueError(
                "ReadStorage needs a runtime or explicit ported clients"
            )
        self._runtime = runtime
        self._injected = dict(clients) if clients is not None else None
        self._router: ReadRouter | None = None
        self._lock = asyncio.Lock()
        if clients is not None:
            # Explicitly injected clients open no pool: wire the router eagerly so
            # routing is inspectable without awaiting a read.
            self._router = ReadRouter(
                _ReadFactory(read_backend=self._backend, clients=clients)
            )

    # ── wiring ─────────────────────────────────────────────────

    @property
    def read_backend(self) -> str:
        return self._backend.value

    def _client_for(
        self,
        label: str,
        dimension: StorageBackendType = StorageBackendType.GRAPH_MAIN_READ,
    ) -> BaseClient:
        """Resolve the routed client for one label (diagnostic/test seam)."""

        if self._router is None:
            raise RuntimeError(
                "ReadStorage clients are not wired yet; run a read or inject clients"
            )
        backend_label = to_backend_label(label)
        if backend_label is None:
            raise KeyError(f"unknown storage label {label!r}")
        return self._router.backend_factory.get_read_client(
            backend_label, dimension
        )

    async def _read_router(self) -> ReadRouter:
        if self._router is None:
            async with self._lock:
                if self._router is None:
                    clients = self._injected
                    if clients is None:
                        clients = await self._build_clients()
                    self._router = ReadRouter(
                        _ReadFactory(read_backend=self._backend, clients=clients)
                    )
        return self._router

    async def _build_clients(self) -> dict[BackendType, BaseClient]:
        runtime = self._runtime
        configure_neo4j_manager(runtime.neo4j)
        neo4j = await Neo4jClient.create()
        elastic = ElasticClient()
        # Borrow the process pool: index confirmation is the startup bootstrap's
        # job (``PROFILE_STORAGE_INDEX_BOOTSTRAP``), never a request's.
        elastic.client = await runtime.elasticsearch.client()
        return {BackendType.NEO4J: neo4j, BackendType.ELASTIC: elastic}

    def _empty(self) -> StorageReadResult:
        return StorageReadResult(backend=self._backend.value.lower())

    # ── per-label planning ─────────────────────────────────────

    def _supports(
        self,
        client: BaseClient,
        label: MemoryNodeType,
        *,
        kind: str,
        dimension: int | None = None,
    ) -> bool:
        """Whether the routed backend has an index for this label and query kind."""

        if isinstance(client, ElasticClient):
            if label.value not in ES_INDEX_ALIAS:
                return False
            if kind == "fulltext":
                return label.value in ES_FULLTEXT_FIELDS
            if dimension is None:
                return False
            try:
                es_embedding_field(label.value, dimension)
            except (KeyError, ValueError):
                return False
            return True
        if kind == "fulltext":
            return label.value in NEO4J_FULLTEXT_INDEX
        return label.value in NEO4J_EMBEDDING_FIELD

    def _plan_searches(
        self,
        router: ReadRouter,
        node_filters: Mapping[str, NodeFilter],
        projections: Mapping[str, NodeProjection],
        *,
        kind: str,
        dimension: int | None = None,
    ) -> tuple[list[tuple[BaseClient, list[NodeSearchSpec]]], list[StorageReadResult]]:
        """Group labels by their routed client, keeping one spec (and projection) each.

        Grouping by client is what lets one multi-label call stay a single
        Elasticsearch ``_msearch`` even though every label carries its own
        projection — the host cannot express that through the router's single
        ``projection=`` argument, so the adapter performs the same grouping the
        host's ``_run_grouped_search`` does and calls the clients' batched APIs.
        """

        dimension_key = (
            StorageBackendType.TEXT_MAIN_READ
            if kind == "fulltext"
            else StorageBackendType.VECTOR_MAIN_READ
        )
        groups: dict[int, tuple[BaseClient, list[NodeSearchSpec]]] = {}
        empties: list[StorageReadResult] = []
        for label, node_filter in node_filters.items():
            backend_label = to_backend_label(label)
            if backend_label is None:
                empties.append(self._empty())
                continue
            client = router.backend_factory.get_read_client(
                backend_label, dimension_key
            )
            if not self._supports(
                client, backend_label, kind=kind, dimension=dimension
            ):
                empties.append(self._empty())
                continue
            projection = projections.get(label)
            if projection is None:
                try:
                    projection = projection_for(label)
                except KeyError:
                    empties.append(self._empty())
                    continue
            slot = groups.setdefault(id(client), (client, []))
            slot[1].append(
                NodeSearchSpec(
                    label=backend_label,
                    node_filter=to_backend_filter(node_filter),
                    projection=to_backend_projection(projection),
                )
            )
        return list(groups.values()), empties

    # ── full-text / vector search ──────────────────────────────

    async def search_by_fulltext(
        self,
        node_filters: Mapping[str, NodeFilter],
        text: str,
        pre_limit: int,
        projections: Mapping[str, NodeProjection] | None = None,
    ) -> StorageReadResult:
        router = await self._read_router()
        groups, empties = self._plan_searches(
            router, node_filters, projections or {}, kind="fulltext"
        )
        if not groups:
            return merge_read_results(empties)
        batches = await asyncio.gather(
            *(
                client.search_many_by_fulltext(specs, text, pre_limit)
                for client, specs in groups
            )
        )
        results = [
            to_core_result(result) for batch in batches for result in batch
        ]
        return merge_read_results([*results, *empties])

    async def search_by_embedding(
        self,
        node_filters: Mapping[str, NodeFilter],
        embedding: Sequence[float],
        pre_limit: int,
        projections: Mapping[str, NodeProjection] | None = None,
    ) -> StorageReadResult:
        router = await self._read_router()
        vector = [float(value) for value in embedding]
        groups, empties = self._plan_searches(
            router,
            node_filters,
            projections or {},
            kind="embedding",
            dimension=len(vector),
        )
        if not groups:
            return merge_read_results(empties)
        batches = await asyncio.gather(
            *(
                client.search_many_by_embedding(specs, vector, pre_limit)
                for client, specs in groups
            )
        )
        results = [
            to_core_result(result) for batch in batches for result in batch
        ]
        return merge_read_results([*results, *empties])

    # ── node lookup / relationships ────────────────────────────

    async def get_node(
        self,
        label: str,
        node_filter: NodeFilter,
        projection: NodeProjection | None = None,
    ) -> StorageReadResult:
        router = await self._read_router()
        backend_label = to_backend_label(label)
        if backend_label is None:
            return self._empty()
        client = router.backend_factory.get_read_client(
            backend_label, StorageBackendType.GRAPH_MAIN_READ
        )
        if isinstance(client, ElasticClient) and label not in ES_INDEX_ALIAS:
            return self._empty()
        result = await router.get_node(
            backend_label,
            to_backend_filter(node_filter),
            to_backend_projection(projection),
        )
        return to_core_result(result)

    async def search_relationships(
        self,
        pattern: RelationshipPattern,
        rel_filter: RelationshipFilter,
        projection: Sequence[RelationshipProjectionField] | None = None,
    ) -> StorageReadResult:
        router = await self._read_router()
        backend_filter = to_backend_relationship_filter(rel_filter)
        if backend_filter is None:
            raise ValueError(
                "a relationship search needs at least one scoped node filter"
            )
        result = await router.search_relationships_by_graph(
            to_backend_relationship_pattern(pattern),
            backend_filter,
            to_backend_relationship_projection(projection),
        )
        return to_core_result(result)


def build_read_storage(runtime: Any, *, read_backend: str | None = None) -> ReadStorage:
    """Wire the read router onto the process runtime managers.

    This is the composition seam for every read service: the returned
    :class:`ReadStorage` executes the DSL against Neo4j/Elasticsearch without
    opening a second pool.
    """

    return ReadStorage(runtime=runtime, read_backend=read_backend)


__all__ = [
    "DEFAULT_READ_BACKEND",
    "ReadStorage",
    "RelationshipFilter",
    "RelationshipPattern",
    "StorageItem",
    "StorageReadResult",
    "build_read_storage",
    "merge_read_results",
]
