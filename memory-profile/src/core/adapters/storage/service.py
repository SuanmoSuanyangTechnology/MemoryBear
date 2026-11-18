"""Read-only Neo4j / Elasticsearch access: the storage service of the read DSL.

This is the single storage substrate for memory-profile read services. It owns
the read DSL execution (filters + projections + routing) and every client call;
nothing above it talks to the drivers directly.

Every query goes through :class:`src.infrastructure.neo4j.Neo4jManager`
(``read_query``) or :class:`src.infrastructure.elasticsearch.ElasticsearchManager`
(``client``): this module creates no pool and never issues a write. The read
backend mirrors the host ``MEMORY_READ_BACKEND`` switch (``ELASTIC`` by default);
RAG memory lives only in Elasticsearch and is therefore always read from ES.

:func:`build_read_storage` is the composition entry point used by the retrieval
adapter (and available to any other read service that consumes the DSL).

Known gap: the host fans a multi-label full-text/vector search out with one ES
``msearch`` per branch; this port keeps the same result set but issues one search
request per label.
"""

from __future__ import annotations

import asyncio
import heapq
import logging
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from src.core.adapters.storage.filters import (
    NodeFilter,
    compile_elasticsearch_filter,
    compile_neo4j_filter,
)
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
    apply_elasticsearch_projection,
    compile_elasticsearch_projection,
    compile_neo4j_projection,
    compile_neo4j_relationship_projection,
    projection_for,
    score_output_names,
)
from src.storage.provider.factory import BackendFactory

logger = logging.getLogger(__name__)

DEFAULT_READ_BACKEND = "ELASTIC"

#: One embedding-scan page; matches the host ``Neo4jClient`` batch query.
EMBEDDING_SCAN_BATCH = 1000
#: Single-page cap for lookups on the Elasticsearch backend.
ES_LOOKUP_PAGE_SIZE = 1000
ES_MAX_SEARCH_LIMIT = 10_000
ES_MIN_KNN_CANDIDATES = 100
ES_KNN_CANDIDATE_MULTIPLIER = 10
VIRTUAL_SCORE_FIELD = "score"


# ────────────────────────────────────────────────────────────────
# Result value objects (host ``storage.models.dto``)
# ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class StorageItem:
    label: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StorageReadResult:
    items: Sequence[StorageItem] = field(default_factory=list)
    backend: str | None = None
    total: int = 0

    @classmethod
    def from_items(
        cls,
        items: Sequence[dict[str, Any]],
        *,
        label: str,
        backend: str,
    ) -> StorageReadResult:
        materialized = [StorageItem(label=label, data=item) for item in items]
        return cls(items=materialized, backend=backend, total=len(materialized))


@dataclass(frozen=True, slots=True)
class NodeSearchSpec:
    label: str
    node_filter: NodeFilter
    projection: NodeProjection | None


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


def merge_read_results(results: Sequence[StorageReadResult]) -> StorageReadResult:
    """Merge per-label results; the backend is only reported when uniform."""

    items = [item for result in results for item in result.items]
    backend = (
        results[0].backend
        if results
        and results[0].backend is not None
        and all(result.backend == results[0].backend for result in results)
        else None
    )
    return StorageReadResult(
        items=items,
        backend=backend,
        total=sum(result.total for result in results),
    )


def _search_specs(
    node_filters: Mapping[str, NodeFilter],
    projections: Mapping[str, NodeProjection],
    client_for: Any,
) -> list[tuple[Any, NodeSearchSpec]]:
    """Pair every label filter with its client and projection (host default)."""

    specs: list[tuple[Any, NodeSearchSpec]] = []
    for label, node_filter in node_filters.items():
        projection = projections.get(label)
        if projection is None:
            try:
                projection = projection_for(label)
            except KeyError:
                projection = None
        specs.append(
            (
                client_for(label),
                NodeSearchSpec(
                    label=label,
                    node_filter=node_filter,
                    projection=projection,
                ),
            )
        )
    return specs


# ────────────────────────────────────────────────────────────────
# Neo4j
# ────────────────────────────────────────────────────────────────


class Neo4jReadClient:
    """Read-only Cypher over ``Neo4jManager.read_query``."""

    backend = "neo4j"

    def __init__(self, neo4j: Any) -> None:
        self._neo4j = neo4j

    async def _query(self, cypher: str, **parameters: Any) -> list[dict[str, Any]]:
        return await self._neo4j.read_query(cypher, **parameters)

    async def search_by_fulltext(
        self,
        spec: NodeSearchSpec,
        text: str,
        limit: int,
    ) -> StorageReadResult:
        index = NEO4J_FULLTEXT_INDEX.get(spec.label)
        if index is None:
            return StorageReadResult(backend=self.backend)
        normalized = text.strip()
        if not normalized:
            return StorageReadResult(backend=self.backend)
        predicate, filter_parameters = compile_neo4j_filter(spec.node_filter)
        return_expression, projection_parameters = compile_neo4j_projection(
            spec.projection,
            virtual_fields={VIRTUAL_SCORE_FIELD: VIRTUAL_SCORE_FIELD},
        )
        cypher = f"""
        CALL db.index.fulltext.queryNodes($idx, $text) YIELD node as n, score
        WHERE {predicate}
        ORDER BY score DESC
        RETURN {return_expression}
        LIMIT $limit
        """
        rows = await self._query(
            cypher,
            idx=index,
            text=normalized,
            limit=limit,
            **filter_parameters,
            **projection_parameters,
        )
        items = [row["n"] for row in rows if isinstance(row.get("n"), dict)]
        return StorageReadResult.from_items(items, label=spec.label, backend=self.backend)

    async def search_by_embedding(
        self,
        spec: NodeSearchSpec,
        embedding: Sequence[float],
        limit: int,
    ) -> StorageReadResult:
        field_name = NEO4J_EMBEDDING_FIELD.get(spec.label)
        if field_name is None:
            return StorageReadResult(backend=self.backend)
        query_vector = np.asarray(embedding, dtype=np.float64)
        query_norm = float(np.linalg.norm(query_vector))
        if not math.isfinite(query_norm) or query_norm == 0:
            return StorageReadResult(backend=self.backend)
        query_vector = query_vector / query_norm
        predicate, filter_parameters = compile_neo4j_filter(spec.node_filter)

        top_heap: list[tuple[float, str]] = []
        last_id = ""
        scan = f"""
        MATCH (n:`{spec.label}`)
        WHERE {predicate} AND n.id > $last_id
        RETURN n.id AS id,
               n.`{field_name}` AS embedding
        ORDER BY n.id
        LIMIT {EMBEDDING_SCAN_BATCH}
        """
        while True:
            try:
                batch = await self._query(
                    scan, **filter_parameters, last_id=last_id
                )
            except Exception:
                logger.warning(
                    "[Retrieval] embedding scan failed for %s; returning partial result",
                    spec.label,
                    exc_info=True,
                )
                break
            if not batch:
                break
            vectors: list[list[float]] = []
            ids: list[str] = []
            for record in batch:
                vector = record.get("embedding")
                if vector is None or record.get("id") is None:
                    continue
                vectors.append(list(vector))
                ids.append(str(record["id"]))
            if vectors:
                similarities = await asyncio.to_thread(
                    _cosine_similarities, vectors, query_vector
                )
                for node_id, similarity in zip(ids, similarities, strict=True):
                    value = float(similarity)
                    if len(top_heap) < limit:
                        heapq.heappush(top_heap, (value, node_id))
                    elif value > top_heap[0][0]:
                        heapq.heapreplace(top_heap, (value, node_id))
            if len(batch) < EMBEDDING_SCAN_BATCH:
                break
            last_id = str(batch[-1]["id"])

        if not top_heap:
            return StorageReadResult(backend=self.backend)

        top_heap.sort(key=lambda pair: pair[0], reverse=True)
        top_ids = [node_id for _, node_id in top_heap]
        similarity_map = {node_id: score for score, node_id in top_heap}
        return_expression, projection_parameters = compile_neo4j_projection(
            spec.projection,
            virtual_fields={VIRTUAL_SCORE_FIELD: "0"},
        )
        cypher = f"""
        MATCH (n: `{spec.label}`)
        WHERE n.id IN $ids
        RETURN {return_expression}, n.id AS __search_id
        """
        try:
            rows = await self._query(cypher, ids=top_ids, **projection_parameters)
            rank_map = {node_id: index for index, node_id in enumerate(top_ids)}
            projected = [
                (row["n"], str(row["__search_id"]))
                for row in rows
                if isinstance(row.get("n"), dict) and row.get("__search_id") is not None
            ]
            projected.sort(key=lambda pair: rank_map.get(pair[1], len(rank_map)))
            score_names = score_output_names(spec.projection)
            items: list[dict[str, Any]] = []
            for node, node_id in projected:
                for field_name in score_names:
                    node[field_name] = similarity_map.get(node_id, 0)
                items.append(node)
        except Exception:
            # The host degrades a failed projection fetch to an empty vector
            # branch instead of failing the whole hybrid search.
            logger.warning(
                "[Retrieval] embedding projection fetch failed for %s",
                spec.label,
                exc_info=True,
            )
            return StorageReadResult(backend=self.backend)
        return StorageReadResult.from_items(items, label=spec.label, backend=self.backend)

    async def get_node(
        self,
        label: str,
        node_filter: NodeFilter,
        projection: NodeProjection | None,
    ) -> StorageReadResult:
        predicate, parameters = compile_neo4j_filter(node_filter)
        return_expression, projection_parameters = compile_neo4j_projection(
            projection,
            virtual_fields={VIRTUAL_SCORE_FIELD: "0"},
        )
        cypher = f"""
        MATCH (n:`{label}`)
        WHERE {predicate}
        RETURN {return_expression}
        """
        rows = await self._query(
            cypher, **parameters, **projection_parameters
        )
        items = [row["n"] for row in rows if isinstance(row.get("n"), dict)]
        return StorageReadResult.from_items(items, label=label, backend=self.backend)

    async def search_relationships(
        self,
        pattern: RelationshipPattern,
        rel_filter: RelationshipFilter,
        projection: tuple[RelationshipProjectionField, ...] | None,
    ) -> StorageReadResult:
        relationship_pattern = (
            f"[r:`{pattern.relationship_type}`]"
            if pattern.relationship_type
            else "[r]"
        )
        edge = (
            f"-{relationship_pattern}->" if pattern.directed else f"-{relationship_pattern}-"
        )
        source = f"(source:`{pattern.source_label}`)"
        target = f"(target:`{pattern.target_label}`)"
        predicates: list[str] = []
        parameters: dict[str, Any] = {}
        for scope, variable, node_filter in (
            ("source", "source", rel_filter.source),
            ("target", "target", rel_filter.target),
            ("relationship", "r", rel_filter.relationship),
        ):
            if node_filter is None:
                continue
            predicate, scoped_parameters = compile_neo4j_filter(
                node_filter,
                variable=variable,
                parameter_prefix=f"relationship_filter_{scope}",
            )
            predicates.append(f"({predicate})")
            parameters.update(scoped_parameters)
        where_clause = f"WHERE {' AND '.join(predicates)}" if predicates else ""
        return_expression = compile_neo4j_relationship_projection(projection)
        cypher = f"""
        MATCH {source}{edge}{target}
        {where_clause}
        RETURN {return_expression}
        """
        rows = await self._query(cypher, **parameters)
        items = [row["item"] for row in rows if isinstance(row.get("item"), dict)]
        return StorageReadResult.from_items(
            items, label=pattern.target_label, backend=self.backend
        )


def _cosine_similarities(vectors: list[list[float]], query: np.ndarray) -> np.ndarray:
    """Cosine similarity for a batch of stored vectors (host similarity util).

    The host clips to ``[0, 1]`` and treats a zero-norm stored vector as a zero
    similarity; both rules are part of the score contract, so keep them.
    """

    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    zero_norm_mask = norms[:, 0] == 0.0
    if zero_norm_mask.any():
        norms[zero_norm_mask, 0] = 1.0
    matrix = matrix / norms
    similarities = np.clip(matrix @ query, 0, 1)
    similarities[zero_norm_mask] = 0.0
    return similarities


# ────────────────────────────────────────────────────────────────
# Elasticsearch
# ────────────────────────────────────────────────────────────────


def _raise_on_response_failures(response: Mapping[str, Any], operation: str) -> None:
    if response.get("error") is not None:
        raise RuntimeError(f"Elasticsearch {operation} failed: {response['error']!r}")
    if response.get("timed_out"):
        raise RuntimeError(f"Elasticsearch {operation} timed out")
    shards = response.get("_shards") or {}
    if isinstance(shards, Mapping) and int(shards.get("failed", 0) or 0):
        raise RuntimeError(
            f"Elasticsearch {operation} shard failures: {shards.get('failures', [])!r}"
        )


def _projection_requests_score(projection: NodeProjection | None) -> bool:
    return VIRTUAL_SCORE_FIELD in score_output_names(projection)


def _parse_hits(
    response: Mapping[str, Any],
    spec: NodeSearchSpec,
    operation: str,
    *,
    score_transform: Any = None,
    source_required: bool = False,
) -> StorageReadResult:
    _raise_on_response_failures(response, operation)
    hits_container = response.get("hits")
    hits = hits_container.get("hits") if isinstance(hits_container, Mapping) else None
    if not isinstance(hits, (list, tuple)):
        raise RuntimeError(f"Elasticsearch {operation} returned invalid hits")
    include_score = _projection_requests_score(spec.projection)
    items: list[dict[str, Any]] = []
    for index, hit in enumerate(hits):
        if not isinstance(hit, Mapping):
            raise RuntimeError(
                f"Elasticsearch {operation} returned invalid hit at index {index}"
            )
        source_value = hit.get("_source")
        if source_value is None and not source_required:
            # A body with ``_source: false`` legitimately answers without source.
            source: Mapping[str, Any] = {}
        elif isinstance(source_value, Mapping):
            source = source_value
        else:
            raise RuntimeError(
                f"Elasticsearch {operation} returned invalid _source at index {index}"
            )
        virtual_fields: dict[str, Any] = {}
        if include_score:
            raw_score = hit.get("_score")
            if not isinstance(raw_score, (int, float)) or isinstance(raw_score, bool):
                raise RuntimeError(
                    f"Elasticsearch {operation} returned invalid _score at index {index}"
                )
            score = float(raw_score)
            virtual_fields[VIRTUAL_SCORE_FIELD] = (
                score_transform(score) if score_transform is not None else score
            )
        items.append(
            apply_elasticsearch_projection(
                dict(source), spec.projection, virtual_fields=virtual_fields
            )
        )
    return StorageReadResult.from_items(items, label=spec.label, backend="elastic")


def _msearch_source_options(
    projection: NodeProjection | None,
) -> tuple[dict[str, Any], bool]:
    """Port of the host ``_compile_msearch_source_options``.

    Inside an ``_msearch`` search body the projection must travel as ``_source``:
    ``source_includes`` is a ``_search`` query parameter and an ``_msearch`` body
    carrying it (or ``allow_partial_search_results``) is rejected with a 400
    ``parsing_exception``. ``source_required`` tells the hit parser whether a
    missing ``_source`` is expected (``_source: false``) or a protocol error.
    """

    source_fields = compile_elasticsearch_projection(
        projection, virtual_fields={VIRTUAL_SCORE_FIELD}
    )
    if source_fields is None:
        return {}, True
    if source_fields:
        return {"_source": source_fields}, True
    return {"_source": False}, False


def _resolve_embedding_field(label: str, dimension: int) -> str:
    """Resolve the dimension-matched dense_vector property name (contract helper)."""

    return es_embedding_field(label, dimension)


class ElasticReadClient:
    """Read-only Elasticsearch searches over the writer's index aliases."""

    backend = "elastic"

    def __init__(self, elasticsearch: Any) -> None:
        self._elasticsearch = elasticsearch

    async def _client(self) -> Any:
        return await self._elasticsearch.client()


    async def search_by_fulltext_many(
        self,
        specs: Sequence[NodeSearchSpec],
        text: str,
        limit: int,
    ) -> dict[str, StorageReadResult]:
        """One ES ``_msearch`` for every label (host ``search_many_by_fulltext``).

        Each label contributes a header line plus a standard search body. The
        ``allow_partial_search_results`` flag rides on the header line (``_msearch``
        exposes no such request option through the client) and the projection
        travels as ``_source`` — the query-style spellings of both
        (``allow_partial_search_results`` / ``source_includes`` as body keys) are
        what made Elasticsearch answer 400 ``parsing_exception``.
        """

        normalized = text.strip()
        if not normalized:
            return {
                spec.label: StorageReadResult(backend=self.backend) for spec in specs
            }
        searches: list[dict[str, Any]] = []
        requested: list[tuple[NodeSearchSpec, bool]] = []
        for spec in specs:
            index = ES_INDEX_ALIAS.get(spec.label)
            fields = ES_FULLTEXT_FIELDS.get(spec.label)
            if index is None or fields is None:
                continue
            source_options, requires_source = _msearch_source_options(spec.projection)
            requested.append((spec, requires_source))
            searches.append(
                {"index": index, "allow_partial_search_results": False}
            )
            searches.append(
                {
                    "query": {
                        "bool": {
                            "must": [
                                {
                                    "multi_match": {
                                        "query": normalized,
                                        "fields": list(fields),
                                    }
                                }
                            ],
                            "filter": [compile_elasticsearch_filter(spec.node_filter)],
                        }
                    },
                    "size": limit,
                    **source_options,
                }
            )
        results: dict[str, StorageReadResult] = {
            spec.label: StorageReadResult(backend=self.backend) for spec in specs
        }
        if not requested:
            return results
        client = await self._client()
        response = await client.msearch(
            searches=searches, max_concurrent_searches=len(requested)
        )
        responses = response.get("responses") if isinstance(response, Mapping) else None
        if not isinstance(responses, (list, tuple)) or len(responses) != len(requested):
            raise RuntimeError("Elasticsearch msearch returned an invalid response list")
        for (spec, requires_source), item in zip(requested, responses, strict=True):
            if not isinstance(item, Mapping):
                raise RuntimeError("Elasticsearch msearch returned an invalid response")
            results[spec.label] = _parse_hits(
                item,
                spec,
                f"fulltext search for {spec.label}",
                source_required=requires_source,
            )
        return results

    async def search_by_fulltext(
        self,
        spec: NodeSearchSpec,
        text: str,
        limit: int,
    ) -> StorageReadResult:
        index = ES_INDEX_ALIAS.get(spec.label)
        fields = ES_FULLTEXT_FIELDS.get(spec.label)
        normalized = text.strip()
        if index is None or fields is None or not normalized:
            return StorageReadResult(backend=self.backend)
        source_fields = compile_elasticsearch_projection(
            spec.projection, virtual_fields={VIRTUAL_SCORE_FIELD}
        )
        client = await self._client()
        response = await client.search(
            index=index,
            query={
                "bool": {
                    "must": [
                        {"multi_match": {"query": normalized, "fields": list(fields)}}
                    ],
                    "filter": [compile_elasticsearch_filter(spec.node_filter)],
                }
            },
            size=limit,
            allow_partial_search_results=False,
            source_includes=source_fields,
        )
        return _parse_hits(
            response, spec, f"fulltext search for {spec.label}"
        )

    async def search_by_embedding(
        self,
        spec: NodeSearchSpec,
        embedding: Sequence[float],
        limit: int,
    ) -> StorageReadResult:
        index = ES_INDEX_ALIAS.get(spec.label)
        if index is None:
            return StorageReadResult(backend=self.backend)
        query_vector = [float(value) for value in embedding]
        if not query_vector or not math.isfinite(math.hypot(*query_vector)):
            raise ValueError("embedding query vector must contain finite numbers")
        if math.hypot(*query_vector) == 0:
            return StorageReadResult(backend=self.backend)
        field_name = _resolve_embedding_field(spec.label, len(query_vector))
        source_fields = compile_elasticsearch_projection(
            spec.projection, virtual_fields={VIRTUAL_SCORE_FIELD}
        )
        num_candidates = min(
            ES_MAX_SEARCH_LIMIT,
            max(ES_MIN_KNN_CANDIDATES, limit, limit * ES_KNN_CANDIDATE_MULTIPLIER),
        )
        client = await self._client()
        response = await client.search(
            index=index,
            knn={
                "field": field_name,
                "query_vector": query_vector,
                "k": limit,
                "num_candidates": num_candidates,
                "filter": compile_elasticsearch_filter(spec.node_filter),
            },
            size=limit,
            allow_partial_search_results=False,
            source_includes=source_fields,
        )
        return _parse_hits(
            response,
            spec,
            f"embedding search for {spec.label}",
            score_transform=lambda score: (2.0 * score) - 1.0,
        )

    async def get_node(
        self,
        label: str,
        node_filter: NodeFilter,
        projection: NodeProjection | None,
    ) -> StorageReadResult:
        index = ES_INDEX_ALIAS.get(label)
        if index is None:
            return StorageReadResult(backend=self.backend)
        spec = NodeSearchSpec(
            label=label,
            node_filter=node_filter,
            projection=projection or (),
        )
        source_fields = compile_elasticsearch_projection(
            projection, virtual_fields={VIRTUAL_SCORE_FIELD}
        )
        client = await self._client()
        response = await client.search(
            index=index,
            query=compile_elasticsearch_filter(node_filter),
            size=ES_LOOKUP_PAGE_SIZE,
            allow_partial_search_results=False,
            source_includes=source_fields,
        )
        return _parse_hits(response, spec, f"node lookup for {label}")


# ────────────────────────────────────────────────────────────────
# Backend router
# ────────────────────────────────────────────────────────────────


class ReadStorage:
    """Select the read backend per node family, like the host ``BackendFactory``."""

    def __init__(
        self,
        *,
        neo4j: Neo4jReadClient,
        elastic: ElasticReadClient,
        read_backend: str = "ELASTIC",
    ) -> None:
        backend = (read_backend or "ELASTIC").strip().upper()
        if backend not in {"ELASTIC", "NEO4J"}:
            raise ValueError(
                f"Invalid memory read backend {read_backend!r}; expected one of: ELASTIC, NEO4J"
            )
        self._neo4j = neo4j
        self._elastic = elastic
        self._read_backend = backend

    @property
    def read_backend(self) -> str:
        return self._read_backend

    def _client_for(self, label: str) -> Neo4jReadClient | ElasticReadClient:
        if label in BackendFactory.ELASTIC_ONLY_LABELS:
            return self._elastic
        return self._elastic if self._read_backend == "ELASTIC" else self._neo4j

    async def search_by_fulltext(
        self,
        node_filters: Mapping[str, NodeFilter],
        text: str,
        pre_limit: int,
        projections: Mapping[str, NodeProjection] | None = None,
    ) -> StorageReadResult:
        projections = projections or {}
        specs = _search_specs(node_filters, projections, self._client_for)
        elastic_specs = [
            (client, spec) for client, spec in specs if isinstance(client, ElasticReadClient)
        ]
        batched: dict[str, StorageReadResult] = {}
        if elastic_specs:
            batched = await elastic_specs[0][0].search_by_fulltext_many(
                [spec for _client, spec in elastic_specs], text, pre_limit
            )
        results: list[StorageReadResult] = []
        for client, spec in specs:
            if isinstance(client, ElasticReadClient):
                results.append(
                    batched.get(
                        spec.label, StorageReadResult(backend=self.read_backend)
                    )
                )
            else:
                results.append(await client.search_by_fulltext(spec, text, pre_limit))
        return merge_read_results(results)

    async def search_by_embedding(
        self,
        node_filters: Mapping[str, NodeFilter],
        embedding: Sequence[float],
        pre_limit: int,
        projections: Mapping[str, NodeProjection] | None = None,
    ) -> StorageReadResult:
        projections = projections or {}
        specs = _search_specs(node_filters, projections, self._client_for)
        results = await asyncio.gather(
            *(
                client.search_by_embedding(spec, embedding, pre_limit)
                for client, spec in specs
            )
        )
        return merge_read_results(results)

    async def get_node(
        self,
        label: str,
        node_filter: NodeFilter,
        projection: NodeProjection | None = None,
    ) -> StorageReadResult:
        return await self._client_for(label).get_node(label, node_filter, projection)

    async def search_relationships(
        self,
        pattern: RelationshipPattern,
        rel_filter: RelationshipFilter,
        projection: tuple[RelationshipProjectionField, ...] | None = None,
    ) -> StorageReadResult:
        # Relationships live only in the graph; the host factory always returns Neo4j.
        return await self._neo4j.search_relationships(pattern, rel_filter, projection)


def _resolve_read_backend(runtime: Any, read_backend: str | None) -> str:
    """Resolve the graph read backend: argument → settings → env → default."""

    if read_backend:
        return read_backend
    configured = getattr(runtime.settings, "memory_read_backend", None)
    if configured:
        return str(configured)
    return os.environ.get("MEMORY_READ_BACKEND", DEFAULT_READ_BACKEND)


def build_read_storage(runtime: Any, *, read_backend: str | None = None) -> ReadStorage:
    """Wire the read clients onto the process runtime managers.

    This is the composition seam for every read service: the returned
    :class:`ReadStorage` executes the DSL against Neo4j/Elasticsearch without
    opening a second pool.
    """

    return ReadStorage(
        neo4j=Neo4jReadClient(runtime.neo4j),
        elastic=ElasticReadClient(runtime.elasticsearch),
        read_backend=_resolve_read_backend(runtime, read_backend),
    )


__all__ = [
    "DEFAULT_READ_BACKEND",
    "ElasticReadClient",
    "Neo4jReadClient",
    "NodeSearchSpec",
    "ReadStorage",
    "RelationshipFilter",
    "RelationshipPattern",
    "StorageItem",
    "StorageReadResult",
    "build_read_storage",
    "merge_read_results",
]