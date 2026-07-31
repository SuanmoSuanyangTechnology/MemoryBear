import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from elasticsearch import AsyncElasticsearch, BadRequestError, NotFoundError

from app.core.rag.knowledge_graph.models import (
    AffectedProjectionKeys,
    EntityEvidence,
    EntityProjectionHit,
    GraphEvidenceHit,
    GraphIndexRuntime,
    ProjectionEvidenceGroup,
    RelationEvidence,
    RelationProjectionHit,
    SourceChunkVectorHit,
)
from app.core.rag.knowledge_graph.normalizer import (
    document_map_id,
    projection_id,
)
from app.core.rag.models.chunk import DocumentChunk
from app.core.rag.retrieval.elasticsearch_queries import raise_on_shard_failures
from app.core.rag.vdb.elasticsearch.pit_search import iter_async_search_after_hits
from app.core.rag.vdb.elasticsearch.response_validation import (
    raise_on_delete_by_query_failure,
)
from app.core.rag.vdb.field import Field
from app.core.utils.datetime_utils import utcnow_naive

ENTITY_EVIDENCE = "entity_evidence"
RELATION_EVIDENCE = "relation_evidence"
ENTITY_PROJECTION = "entity_projection"
RELATION_PROJECTION = "relation_projection"
DOCUMENT_PROJECTION_MAP = "document_projection_map"

EVIDENCE_TYPES = (ENTITY_EVIDENCE, RELATION_EVIDENCE)
EVIDENCE_GRAPH_TYPES = (
    ENTITY_EVIDENCE,
    RELATION_EVIDENCE,
    ENTITY_PROJECTION,
    RELATION_PROJECTION,
    DOCUMENT_PROJECTION_MAP,
)
GRAPH_FULL_SCAN_BATCH_SIZE = 1000
GRAPH_METRIC_SCAN_BATCH_SIZE = 1000
GRAPH_PAGERANK_BULK_SIZE = 1000


@lru_cache(maxsize=1)
def _graph_index_definition() -> dict[str, Any]:
    mapping_path = (
        Path(__file__).resolve().parents[1]
        / "res"
        / "mapping.json"
    )
    definition = json.loads(mapping_path.read_text(encoding="utf-8"))
    if not isinstance(definition.get("settings"), Mapping) or not isinstance(
        definition.get("mappings"), Mapping
    ):
        raise ValueError("invalid graph index mapping definition")
    return definition


class GraphElasticsearchStore:
    def __init__(self, client: AsyncElasticsearch) -> None:
        self._client = client

    async def ensure_graph_index(self, index_name: str) -> None:
        if await self._client.indices.exists(index=index_name):
            return
        definition = _graph_index_definition()
        try:
            await self._client.indices.create(
                index=index_name,
                settings=definition["settings"],
                mappings=definition["mappings"],
            )
        except BadRequestError:
            if not await self._client.indices.exists(index=index_name):
                raise

    async def ensure_vector_mapping(
        self,
        index_name: str,
        dimension: int,
    ) -> str:
        if dimension <= 0:
            raise ValueError("vector dimension must be greater than zero")
        field_name = f"q_{dimension}_vec"
        mapping = await self._client.indices.get_mapping(index=index_name)
        index_mapping = mapping.get(index_name)
        if index_mapping is None and len(mapping) == 1:
            index_mapping = next(iter(mapping.values()))
        if not isinstance(index_mapping, Mapping):
            raise ValueError(f"missing mapping for index: {index_name}")
        mappings = index_mapping.get("mappings") or {}
        properties = mappings.get("properties") or {}
        current = properties.get(field_name)
        if current is None:
            await self._client.indices.put_mapping(
                index=index_name,
                properties={
                    field_name: {
                        "type": "dense_vector",
                        "dims": dimension,
                        "index": True,
                        "similarity": "cosine",
                    }
                },
            )
        elif (
            current.get("type") != "dense_vector"
            or current.get("dims") != dimension
        ):
            raise ValueError(f"incompatible vector mapping for {field_name}")
        return field_name

    async def refresh_sources(
        self,
        chunk_index_name: str,
        graph_index_name: str,
    ) -> None:
        await self._client.indices.refresh(
            index=[chunk_index_name, graph_index_name],
            ignore_unavailable=True,
        )

    async def refresh_graph(self, graph_index_name: str) -> None:
        await self._client.indices.refresh(
            index=graph_index_name,
            ignore_unavailable=True,
        )

    async def _collect_search_after_hits(
        self,
        *,
        index_name: str,
        query: Mapping[str, Any],
        sort: Sequence[str | Mapping[str, Any]],
        context: str,
        source_includes: Sequence[str] | None = None,
        source: bool | Mapping[str, Any] | None = None,
        batch_size: int = GRAPH_FULL_SCAN_BATCH_SIZE,
    ) -> list[dict[str, Any]]:
        return [
            hit
            async for hit in iter_async_search_after_hits(
                self._client,
                index=index_name,
                query=query,
                sort=sort,
                context=context,
                source_includes=source_includes,
                source=source,
                batch_size=batch_size,
            )
        ]

    async def load_document_chunks(
        self,
        chunk_index_name: str,
        knowledge_id: str,
        document_id: str,
    ) -> list[dict[str, Any]]:
        filters = [
            {"term": {"metadata.knowledge_id": knowledge_id}},
            {"term": {"metadata.document_id": document_id}},
            {"term": {"metadata.status": 1}},
        ]
        hits = await self._collect_search_after_hits(
            index_name=chunk_index_name,
            query={"bool": {"filter": filters}},
            source_includes=[
                "page_content",
                "metadata.knowledge_id",
                "metadata.document_id",
                "metadata.status",
                "metadata.doc_id",
                "metadata.sort_id",
                "metadata.chunk_type",
                "metadata.parent_id",
            ],
            sort=[
                {"metadata.sort_id": {"order": "asc", "unmapped_type": "long"}},
                {"metadata.doc_id": {"order": "asc"}},
            ],
            context="load graph source document",
        )

        scoped_hits: list[dict[str, Any]] = []
        for hit in hits:
            source = hit.get("_source") or {}
            metadata = source.get("metadata") or {}
            if not isinstance(metadata, Mapping):
                continue
            if str(metadata.get("knowledge_id")) != str(knowledge_id):
                continue
            if str(metadata.get("document_id")) != str(document_id):
                continue
            if metadata.get("status") != 1:
                continue
            scoped_hits.append(hit)
        return scoped_hits

    async def load_document_map(
        self,
        index_name: str,
        knowledge_id: str,
        document_id: str,
    ) -> dict[str, Any] | None:
        result = await self._client.search(
            index=index_name,
            size=1,
            query=self._graph_query(
                knowledge_id,
                DOCUMENT_PROJECTION_MAP,
                [{"term": {"document_id": document_id}}],
            ),
        )
        raise_on_shard_failures(result, "load graph document map")
        hits = self._hits(result)
        if not hits:
            return None
        source = hits[0].get("_source")
        return dict(source) if isinstance(source, Mapping) else None

    async def load_document_evidence_keys(
        self,
        index_name: str,
        knowledge_id: str,
        document_id: str,
    ) -> AffectedProjectionKeys:
        hits = await self._collect_search_after_hits(
            index_name=index_name,
            query=self._graph_query(
                knowledge_id,
                EVIDENCE_TYPES,
                [{"term": {"document_id": document_id}}],
            ),
            source_includes=[
                "knowledge_graph_kwd",
                "entity_key_kwd",
                "relation_key_kwd",
            ],
            sort=[
                {"knowledge_graph_kwd": {"order": "asc"}},
                {"entity_key_kwd": {"order": "asc", "missing": "_last"}},
                {"relation_key_kwd": {"order": "asc", "missing": "_last"}},
                {"source_chunk_id_kwd": {"order": "asc", "missing": "_last"}},
                {"document_id": {"order": "asc", "missing": "_last"}},
            ],
            context="load graph document evidence keys",
        )
        entity_keys: set[str] = set()
        relation_keys: set[str] = set()
        for hit in hits:
            source = hit.get("_source") or {}
            if source.get("knowledge_graph_kwd") == ENTITY_EVIDENCE:
                if source.get("entity_key_kwd"):
                    entity_keys.add(str(source["entity_key_kwd"]))
            elif source.get("knowledge_graph_kwd") == RELATION_EVIDENCE:
                if source.get("relation_key_kwd"):
                    relation_keys.add(str(source["relation_key_kwd"]))
        return AffectedProjectionKeys(
            entity_keys=tuple(sorted(entity_keys)),
            relation_keys=tuple(sorted(relation_keys)),
        )

    async def replace_document_evidence(
        self,
        index_name: str,
        knowledge_id: str,
        document_id: str,
        entity_evidence: Sequence[EntityEvidence],
        relation_evidence: Sequence[RelationEvidence],
        *,
        ensure_valid: Callable[[], None] | None = None,
    ) -> AffectedProjectionKeys:
        old_map = await self.load_document_map(
            index_name,
            knowledge_id,
            document_id,
        )
        actual_old = await self.load_document_evidence_keys(
            index_name,
            knowledge_id,
            document_id,
        )
        mapped_entity_keys = set((old_map or {}).get("entity_keys_kwd") or ())
        mapped_relation_keys = set((old_map or {}).get("relation_keys_kwd") or ())
        old_entity_keys = mapped_entity_keys | set(actual_old.entity_keys)
        old_relation_keys = mapped_relation_keys | set(actual_old.relation_keys)

        if (
            old_entity_keys != mapped_entity_keys
            or old_relation_keys != mapped_relation_keys
        ):
            await self._write_document_map(
                index_name,
                knowledge_id,
                document_id,
                entity_keys=old_entity_keys,
                relation_keys=old_relation_keys,
                source_chunk_ids=set(
                    (old_map or {}).get("source_chunk_ids_kwd") or ()
                ),
                ensure_valid=ensure_valid,
            )

        self._ensure_valid(ensure_valid)
        result = await self._client.delete_by_query(
            index=index_name,
            conflicts="abort",
            refresh=False,
            wait_for_completion=True,
            query=self._graph_query(
                knowledge_id,
                EVIDENCE_TYPES,
                [{"term": {"document_id": document_id}}],
            ),
        )
        raise_on_delete_by_query_failure(
            result,
            "replace graph document evidence",
        )

        operations: list[dict[str, Any]] = []
        for evidence in entity_evidence:
            operations.extend(
                self._index_operation(
                    index_name,
                    evidence.id,
                    self._entity_evidence_source(evidence),
                )
            )
        for evidence in relation_evidence:
            operations.extend(
                self._index_operation(
                    index_name,
                    evidence.id,
                    self._relation_evidence_source(evidence),
                )
            )
        await self._bulk(operations, ensure_valid=ensure_valid)

        self._ensure_valid(ensure_valid)
        await self.refresh_graph(index_name)

        new_entity_keys = {item.entity_key for item in entity_evidence}
        new_relation_keys = {item.relation_key for item in relation_evidence}
        return AffectedProjectionKeys(
            entity_keys=tuple(sorted(old_entity_keys | new_entity_keys)),
            relation_keys=tuple(sorted(old_relation_keys | new_relation_keys)),
        )

    async def load_entity_evidence(
        self,
        index_name: str,
        knowledge_id: str,
        entity_keys: Sequence[str],
    ) -> list[EntityEvidence]:
        if not entity_keys:
            return []
        hits = await self._collect_search_after_hits(
            index_name=index_name,
            query=self._graph_query(
                knowledge_id,
                ENTITY_EVIDENCE,
                [{"terms": {"entity_key_kwd": list(entity_keys)}}],
            ),
            sort=[
                {"entity_key_kwd": {"order": "asc"}},
                {"source_chunk_id_kwd": {"order": "asc", "missing": "_last"}},
                {"document_id": {"order": "asc", "missing": "_last"}},
            ],
            context="load entity evidence",
        )
        evidence: list[EntityEvidence] = []
        for hit in hits:
            source = hit.get("_source") or {}
            evidence.append(
                EntityEvidence(
                    id=str(hit.get("_id") or source.get("id") or ""),
                    kb_id=str(source["kb_id"]),
                    document_id=str(source["document_id"]),
                    source_chunk_id=str(source["source_chunk_id_kwd"]),
                    entity_key=str(source["entity_key_kwd"]),
                    entity_name=str(source["entity_name_kwd"]),
                    entity_type=str(source["entity_type_kwd"]),
                    description=str(source.get("description") or ""),
                    aliases=tuple(source.get("aliases_kwd") or ()),
                    confidence=float(source.get("confidence_flt") or 0.0),
                )
            )
        return evidence

    async def load_relation_evidence(
        self,
        index_name: str,
        knowledge_id: str,
        relation_keys: Sequence[str],
    ) -> list[RelationEvidence]:
        if not relation_keys:
            return []
        return await self._load_relation_evidence_with_filters(
            index_name,
            knowledge_id,
            [{"terms": {"relation_key_kwd": list(relation_keys)}}],
            "load relation evidence",
        )

    async def load_relations_for_entity_keys(
        self,
        index_name: str,
        knowledge_id: str,
        entity_keys: Sequence[str],
    ) -> list[RelationEvidence]:
        if not entity_keys:
            return []
        return await self._load_relation_evidence_with_filters(
            index_name,
            knowledge_id,
            [
                {
                    "bool": {
                        "should": [
                            {"terms": {"from_entity_key_kwd": list(entity_keys)}},
                            {"terms": {"to_entity_key_kwd": list(entity_keys)}},
                        ],
                        "minimum_should_match": 1,
                    }
                }
            ],
            "load entity relation evidence",
        )

    async def write_entity_projections(
        self,
        index_name: str,
        knowledge_id: str,
        projections: Sequence[Mapping[str, Any]],
        delete_keys: Sequence[str] = (),
        *,
        ensure_valid: Callable[[], None] | None = None,
    ) -> None:
        operations: list[dict[str, Any]] = []
        for key in sorted(set(delete_keys)):
            operations.append(
                {
                    "delete": {
                        "_index": index_name,
                        "_id": projection_id(knowledge_id, "entity", key),
                    }
                }
            )
        for projection in projections:
            source = dict(projection)
            source["knowledge_graph_kwd"] = ENTITY_PROJECTION
            source["kb_id"] = knowledge_id
            key = str(source["entity_key_kwd"])
            operations.extend(
                self._index_operation(
                    index_name,
                    projection_id(knowledge_id, "entity", key),
                    source,
                )
            )
        await self._bulk(operations, ensure_valid=ensure_valid)

    async def write_relation_projections(
        self,
        index_name: str,
        knowledge_id: str,
        projections: Sequence[Mapping[str, Any]],
        delete_keys: Sequence[str] = (),
        *,
        ensure_valid: Callable[[], None] | None = None,
    ) -> None:
        operations: list[dict[str, Any]] = []
        for key in sorted(set(delete_keys)):
            operations.append(
                {
                    "delete": {
                        "_index": index_name,
                        "_id": projection_id(knowledge_id, "relation", key),
                    }
                }
            )
        for projection in projections:
            source = dict(projection)
            source["knowledge_graph_kwd"] = RELATION_PROJECTION
            source["kb_id"] = knowledge_id
            key = str(source["relation_key_kwd"])
            operations.extend(
                self._index_operation(
                    index_name,
                    projection_id(knowledge_id, "relation", key),
                    source,
                )
            )
        await self._bulk(operations, ensure_valid=ensure_valid)

    async def finish_document_map(
        self,
        index_name: str,
        knowledge_id: str,
        document_id: str,
        entity_evidence: Sequence[EntityEvidence],
        relation_evidence: Sequence[RelationEvidence],
        *,
        ensure_valid: Callable[[], None] | None = None,
    ) -> None:
        entity_keys = {item.entity_key for item in entity_evidence}
        relation_keys = {item.relation_key for item in relation_evidence}
        source_chunk_ids = {
            item.source_chunk_id for item in (*entity_evidence, *relation_evidence)
        }
        if not entity_keys and not relation_keys:
            await self._bulk(
                [
                    {
                        "delete": {
                            "_index": index_name,
                            "_id": document_map_id(knowledge_id, document_id),
                        }
                    }
                ],
                ensure_valid=ensure_valid,
            )
            return
        await self._write_document_map(
            index_name,
            knowledge_id,
            document_id,
            entity_keys=entity_keys,
            relation_keys=relation_keys,
            source_chunk_ids=source_chunk_ids,
            ensure_valid=ensure_valid,
        )

    async def list_document_maps(
        self,
        index_name: str,
        knowledge_id: str,
    ) -> list[dict[str, Any]]:
        hits = await self._collect_search_after_hits(
            index_name=index_name,
            query=self._graph_query(knowledge_id, DOCUMENT_PROJECTION_MAP),
            sort=[{"document_id": {"order": "asc"}}],
            context="list graph document maps",
            batch_size=GRAPH_METRIC_SCAN_BATCH_SIZE,
        )
        return [
            dict(source)
            for hit in hits
            if isinstance((source := hit.get("_source")), Mapping)
        ]

    async def load_graph_metric_inputs(
        self,
        index_name: str,
        knowledge_id: str,
    ) -> tuple[tuple[str, ...], tuple[dict[str, Any], ...]]:
        entity_sources, relation_sources = await asyncio.gather(
            self._scan_projection_sources(
                index_name,
                knowledge_id,
                document_type=ENTITY_PROJECTION,
                sort_field="entity_key_kwd",
                source_includes=["entity_key_kwd"],
            ),
            self._scan_projection_sources(
                index_name,
                knowledge_id,
                document_type=RELATION_PROJECTION,
                sort_field="relation_key_kwd",
                source_includes=[
                    "relation_key_kwd",
                    "from_entity_key_kwd",
                    "to_entity_key_kwd",
                    "directed_int",
                    "evidence_count_int",
                ],
            ),
        )
        entity_keys = tuple(
            sorted(
                {
                    str(source["entity_key_kwd"])
                    for source in entity_sources
                    if source.get("entity_key_kwd")
                }
            )
        )
        relations = tuple(
            sorted(
                (
                    dict(source)
                    for source in relation_sources
                    if source.get("relation_key_kwd")
                    and source.get("from_entity_key_kwd")
                    and source.get("to_entity_key_kwd")
                ),
                key=lambda source: str(source["relation_key_kwd"]),
            )
        )
        return entity_keys, relations

    async def update_entity_pageranks(
        self,
        index_name: str,
        knowledge_id: str,
        pageranks: Mapping[str, float],
        *,
        ensure_valid: Callable[[], None] | None = None,
    ) -> None:
        items = sorted(pageranks.items())
        for offset in range(0, len(items), GRAPH_PAGERANK_BULK_SIZE):
            operations: list[dict[str, Any]] = []
            for entity_key, pagerank in items[
                offset : offset + GRAPH_PAGERANK_BULK_SIZE
            ]:
                operations.extend(
                    [
                        {
                            "update": {
                                "_index": index_name,
                                "_id": projection_id(
                                    knowledge_id,
                                    "entity",
                                    str(entity_key),
                                ),
                            }
                        },
                        {"doc": {"pagerank_flt": float(pagerank)}},
                    ]
                )
            await self._bulk(operations, ensure_valid=ensure_valid)

    async def _scan_projection_sources(
        self,
        index_name: str,
        knowledge_id: str,
        *,
        document_type: str,
        sort_field: str,
        source_includes: Sequence[str],
    ) -> list[dict[str, Any]]:
        hits = await self._collect_search_after_hits(
            index_name=index_name,
            query=self._graph_query(knowledge_id, document_type),
            source_includes=source_includes,
            sort=[
                {
                    sort_field: {
                        "order": "asc",
                        "unmapped_type": "keyword",
                    }
                }
            ],
            context=f"scan {document_type}",
            batch_size=GRAPH_METRIC_SCAN_BATCH_SIZE,
        )
        return [
            dict(source)
            for hit in hits
            if isinstance((source := hit.get("_source")), Mapping)
        ]

    async def clear_evidence_graph(
        self,
        index_name: str,
        knowledge_id: str,
        *,
        ensure_valid: Callable[[], None] | None = None,
    ) -> None:
        self._ensure_valid(ensure_valid)
        result = await self._client.delete_by_query(
            index=index_name,
            conflicts="abort",
            ignore_unavailable=True,
            refresh=True,
            wait_for_completion=True,
            query=self._graph_query(knowledge_id, EVIDENCE_GRAPH_TYPES),
        )
        raise_on_delete_by_query_failure(
            result,
            "clear evidence graph",
        )

    async def clear_all_graph_documents(
        self,
        index_name: str,
        knowledge_id: str,
        *,
        ensure_valid: Callable[[], None] | None = None,
    ) -> None:
        self._ensure_valid(ensure_valid)
        result = await self._client.delete_by_query(
            index=index_name,
            conflicts="abort",
            ignore_unavailable=True,
            refresh=True,
            wait_for_completion=True,
            query={
                "bool": {
                    "filter": [
                        {"term": {"kb_id": knowledge_id}},
                        {"exists": {"field": "knowledge_graph_kwd"}},
                    ]
                }
            },
        )
        raise_on_delete_by_query_failure(
            result,
            "clear all graph documents",
        )

    async def load_projection_graph(
        self,
        index_name: str,
        knowledge_id: str,
        *,
        node_limit: int = 256,
        edge_limit: int = 128,
    ) -> dict[str, Any]:
        empty_graph = {
            "directed": True,
            "multigraph": True,
            "graph": {"source_id": []},
            "nodes": [],
            "edges": [],
        }
        try:
            node_result, edge_result, document_maps = await asyncio.gather(
                self._client.search(
                    index=index_name,
                    size=max(1, node_limit),
                    query=self._graph_query(
                        knowledge_id,
                        ENTITY_PROJECTION,
                    ),
                    sort=[
                        {
                            "pagerank_flt": {
                                "order": "desc",
                                "unmapped_type": "float",
                                "missing": "_last",
                            }
                        },
                        {"degree_int": {"order": "desc", "unmapped_type": "long"}},
                        {
                            "evidence_count_int": {
                                "order": "desc",
                                "unmapped_type": "long",
                            }
                        },
                        {
                            "entity_key_kwd": {
                                "order": "asc",
                                "unmapped_type": "keyword",
                            }
                        },
                    ],
                ),
                self._client.search(
                    index=index_name,
                    size=max(1, edge_limit * 4),
                    query=self._graph_query(
                        knowledge_id,
                        RELATION_PROJECTION,
                    ),
                    sort=[
                        {
                            "evidence_count_int": {
                                "order": "desc",
                                "unmapped_type": "long",
                            }
                        },
                        {
                            "relation_key_kwd": {
                                "order": "asc",
                                "unmapped_type": "keyword",
                            }
                        },
                    ],
                ),
                self.list_document_maps(index_name, knowledge_id),
            )
        except NotFoundError:
            return empty_graph

        raise_on_shard_failures(node_result, "load graph entity projections")
        raise_on_shard_failures(edge_result, "load graph relation projections")

        nodes: list[dict[str, Any]] = []
        for hit in self._hits(node_result):
            source = hit.get("_source") or {}
            entity_key = str(source.get("entity_key_kwd") or "")
            if not entity_key:
                continue
            nodes.append(
                {
                    "id": entity_key,
                    "entity_name": str(source.get("entity_name_kwd") or ""),
                    "entity_type": str(source.get("entity_type_kwd") or ""),
                    "description": str(source.get("description") or ""),
                    "pagerank": float(source.get("pagerank_flt") or 0.0),
                    "source_id": sorted(
                        {
                            str(value)
                            for value in (source.get("source_id") or ())
                            if str(value).strip()
                        }
                    ),
                    "aliases": list(source.get("aliases_kwd") or ()),
                    "evidence_count": int(source.get("evidence_count_int") or 0),
                    "document_count": int(source.get("document_count_int") or 0),
                    "degree": int(source.get("degree_int") or 0),
                }
            )
            if len(nodes) >= node_limit:
                break

        node_ids = {node["id"] for node in nodes}
        edges: list[dict[str, Any]] = []
        for hit in self._hits(edge_result):
            source = hit.get("_source") or {}
            relation_key = str(source.get("relation_key_kwd") or "")
            from_key = str(source.get("from_entity_key_kwd") or "")
            to_key = str(source.get("to_entity_key_kwd") or "")
            if (
                not relation_key
                or not from_key
                or not to_key
                or from_key == to_key
                or from_key not in node_ids
                or to_key not in node_ids
            ):
                continue
            predicate = str(source.get("predicate_kwd") or "")
            keywords = [
                str(value)
                for value in (source.get("keywords_kwd") or ())
                if str(value).strip()
            ]
            edges.append(
                {
                    "id": relation_key,
                    "src_id": from_key,
                    "tgt_id": to_key,
                    "source": from_key,
                    "target": to_key,
                    "description": str(source.get("description") or ""),
                    "keywords": keywords or ([predicate] if predicate else []),
                    "weight": int(source.get("evidence_count_int") or 1),
                    "source_id": sorted(
                        {
                            str(value)
                            for value in (source.get("source_id") or ())
                            if str(value).strip()
                        }
                    ),
                    "directed": bool(source.get("directed_int")),
                    "document_count": int(source.get("document_count_int") or 0),
                }
            )
            if len(edges) >= edge_limit:
                break

        graph_source_ids = sorted(
            {
                str(item["document_id"])
                for item in document_maps
                if item.get("document_id")
            }
        )
        return {
            **empty_graph,
            "graph": {"source_id": graph_source_ids},
            "nodes": nodes,
            "edges": edges,
        }

    async def search_entity_projections(
        self,
        runtime: GraphIndexRuntime,
        query_vector: Sequence[float],
        top_n: int,
        min_similarity: float = -1.0,
    ) -> list[EntityProjectionHit]:
        result = await self._projection_search(
            runtime,
            query_vector,
            top_n,
            ENTITY_PROJECTION,
            min_similarity,
        )
        hits: list[EntityProjectionHit] = []
        for hit in self._hits(result):
            source = hit.get("_source") or {}
            if (
                not isinstance(source, Mapping)
                or not source.get("entity_key_kwd")
                or not source.get("entity_name_kwd")
            ):
                continue
            score = self._script_score_to_cosine(hit.get("_score"))
            if score < min_similarity:
                continue
            hits.append(
                EntityProjectionHit(
                    entity_key=str(source["entity_key_kwd"]),
                    entity_name=str(source["entity_name_kwd"]),
                    entity_type=str(source.get("entity_type_kwd") or ""),
                    description=str(source.get("description") or ""),
                    aliases=tuple(
                        str(value)
                        for value in (source.get("aliases_kwd") or ())
                        if str(value).strip()
                    ),
                    score=score,
                    degree=int(source.get("degree_int") or 0),
                    evidence_count=int(source.get("evidence_count_int") or 0),
                    document_count=int(source.get("document_count_int") or 0),
                )
            )
        return hits

    async def search_relation_projections(
        self,
        runtime: GraphIndexRuntime,
        query_vector: Sequence[float],
        top_n: int,
        min_similarity: float = -1.0,
    ) -> list[RelationProjectionHit]:
        result = await self._projection_search(
            runtime,
            query_vector,
            top_n,
            RELATION_PROJECTION,
            min_similarity,
        )
        return [
            hit
            for hit in self._relation_projection_hits(result, script_score=True)
            if hit.score >= min_similarity
        ]

    async def load_entity_projections(
        self,
        runtime: GraphIndexRuntime,
        entity_keys: Sequence[str],
    ) -> list[EntityProjectionHit]:
        keys = tuple(dict.fromkeys(str(key) for key in entity_keys if str(key)))
        if not keys:
            return []
        result = await self._client.search(
            index=runtime.graph_index_name,
            size=len(keys),
            query=self._graph_query(
                runtime.knowledge_id,
                ENTITY_PROJECTION,
                [{"terms": {"entity_key_kwd": list(keys)}}],
            ),
            sort=[
                {
                    "entity_key_kwd": {
                        "order": "asc",
                        "unmapped_type": "keyword",
                    }
                }
            ],
        )
        raise_on_shard_failures(result, "load graph entity projections")
        projections: list[EntityProjectionHit] = []
        for hit in self._hits(result):
            source = hit.get("_source") or {}
            if not isinstance(source, Mapping) or not source.get("entity_key_kwd"):
                continue
            projections.append(
                EntityProjectionHit(
                    entity_key=str(source["entity_key_kwd"]),
                    entity_name=str(source.get("entity_name_kwd") or ""),
                    entity_type=str(source.get("entity_type_kwd") or ""),
                    description=str(source.get("description") or ""),
                    aliases=tuple(
                        str(value)
                        for value in (source.get("aliases_kwd") or ())
                        if str(value).strip()
                    ),
                    score=0.0,
                    degree=int(source.get("degree_int") or 0),
                    evidence_count=int(source.get("evidence_count_int") or 0),
                    document_count=int(source.get("document_count_int") or 0),
                )
            )
        return projections

    async def load_neighbor_relations(
        self,
        runtime: GraphIndexRuntime,
        entity_keys: Sequence[str],
        top_n: int,
    ) -> list[RelationProjectionHit]:
        if not entity_keys:
            return []
        result = await self._client.search(
            index=runtime.graph_index_name,
            size=max(1, min(400, top_n * 4)),
            query=self._graph_query(
                runtime.knowledge_id,
                RELATION_PROJECTION,
                [
                    {
                        "bool": {
                            "should": [
                                {"terms": {"from_entity_key_kwd": list(entity_keys)}},
                                {"terms": {"to_entity_key_kwd": list(entity_keys)}},
                            ],
                            "minimum_should_match": 1,
                        }
                    }
                ],
            ),
            sort=[
                {
                    "relation_key_kwd": {
                        "order": "asc",
                        "unmapped_type": "keyword",
                    }
                },
            ],
        )
        raise_on_shard_failures(result, "load graph neighbor relations")
        relations = self._relation_projection_hits(result)
        endpoint_keys = tuple(
            dict.fromkeys(
                key
                for relation in relations
                for key in (relation.from_entity_key, relation.to_entity_key)
                if key
            )
        )
        endpoint_hits = await self.load_entity_projections(runtime, endpoint_keys)
        degrees = {hit.entity_key: hit.degree for hit in endpoint_hits}
        seed_positions = {
            str(entity_key): index
            for index, entity_key in enumerate(entity_keys)
        }
        missing_position = len(seed_positions)
        decorated = [
            relation.model_copy(
                update={
                    "endpoint_degree": (
                        degrees.get(relation.from_entity_key, 0)
                        + degrees.get(relation.to_entity_key, 0)
                    )
                }
            )
            for relation in relations
        ]
        decorated.sort(
            key=lambda hit: (
                -hit.endpoint_degree,
                -hit.evidence_count,
                min(
                    seed_positions.get(
                        hit.from_entity_key,
                        missing_position,
                    ),
                    seed_positions.get(
                        hit.to_entity_key,
                        missing_position,
                    ),
                ),
                hit.relation_key,
            )
        )
        return decorated[:top_n]

    async def load_evidence_groups(
        self,
        runtime: GraphIndexRuntime,
        entity_keys: Sequence[str],
        relation_keys: Sequence[str],
        evidence_per_projection: int,
        allowed_document_ids: Sequence[str] | None = None,
    ) -> list[ProjectionEvidenceGroup]:
        if allowed_document_ids is not None and not allowed_document_ids:
            return []
        group_specs = [
            ("entity", str(key), ENTITY_EVIDENCE, "entity_key_kwd")
            for key in dict.fromkeys(entity_keys)
            if str(key)
        ] + [
            ("relation", str(key), RELATION_EVIDENCE, "relation_key_kwd")
            for key in dict.fromkeys(relation_keys)
            if str(key)
        ]
        if not group_specs:
            return []

        searches: list[dict[str, Any]] = []
        limit = max(1, int(evidence_per_projection))
        for _, key, document_type, key_field in group_specs:
            extra_filters: list[dict[str, Any]] = [{"term": {key_field: key}}]
            if allowed_document_ids is not None:
                extra_filters.append(
                    {"terms": {"document_id": list(allowed_document_ids)}}
                )
            searches.extend(
                [
                    {"index": runtime.graph_index_name},
                    {
                        "size": limit,
                        "query": self._graph_query(
                            runtime.knowledge_id,
                            document_type,
                            extra_filters,
                        ),
                        "sort": [
                            {
                                "confidence_flt": {
                                    "order": "desc",
                                    "unmapped_type": "float",
                                }
                            },
                            {"source_chunk_id_kwd": {"order": "asc"}},
                        ],
                    },
                ]
            )
        result = await self._client.msearch(searches=searches)
        responses = result.get("responses") or []
        if len(responses) != len(group_specs):
            raise RuntimeError("graph evidence msearch response count mismatch")

        groups: list[ProjectionEvidenceGroup] = []
        for spec, response in zip(group_specs, responses, strict=True):
            projection_type, projection_key, document_type, _ = spec
            if response.get("error"):
                raise RuntimeError("graph evidence msearch response failed")
            raise_on_shard_failures(response, "load graph projection evidence group")
            evidence = self._parse_group_evidence(
                response,
                document_type,
                projection_key,
            )
            evidence.sort(
                key=lambda hit: (
                    -hit.score,
                    hit.source_chunk_id,
                    hit.evidence_id,
                )
            )
            groups.append(
                ProjectionEvidenceGroup(
                    projection_type=projection_type,
                    projection_key=projection_key,
                    evidence=tuple(evidence[:limit]),
                )
            )
        return groups

    async def hydrate_source_chunks(
        self,
        *,
        chunk_index_name: str,
        knowledge_id: str,
        source_chunk_ids: Sequence[str],
        allowed_document_ids: Sequence[str] | None,
        file_names: Sequence[str],
    ) -> list[DocumentChunk]:
        if not source_chunk_ids:
            return []
        filters: list[dict[str, Any]] = [
            {"terms": {"metadata.doc_id": list(source_chunk_ids)}},
            {"term": {"metadata.knowledge_id": knowledge_id}},
            {"term": {"metadata.status": 1}},
        ]
        if allowed_document_ids is not None:
            if not allowed_document_ids:
                return []
            filters.append(
                {"terms": {"metadata.document_id": list(allowed_document_ids)}}
            )
        if file_names:
            filters.append({"terms": {"metadata.file_name": list(file_names)}})

        result = await self._client.search(
            index=chunk_index_name,
            size=len(set(source_chunk_ids)),
            query={"bool": {"filter": filters}},
        )
        raise_on_shard_failures(result, "hydrate graph source chunks")

        source_ids = {str(item) for item in source_chunk_ids}
        allowed_documents = (
            {str(item) for item in allowed_document_ids}
            if allowed_document_ids is not None
            else None
        )
        allowed_files = {str(item) for item in file_names}
        chunks: list[DocumentChunk] = []
        for hit in self._hits(result):
            source = hit.get("_source") or {}
            metadata = source.get("metadata") or {}
            if not isinstance(metadata, Mapping):
                continue
            if str(metadata.get("doc_id")) not in source_ids:
                continue
            if str(metadata.get("knowledge_id")) != str(knowledge_id):
                continue
            if metadata.get("status") != 1:
                continue
            if (
                allowed_documents is not None
                and str(metadata.get("document_id")) not in allowed_documents
            ):
                continue
            if allowed_files and str(metadata.get("file_name")) not in allowed_files:
                continue
            page_content = source.get("page_content")
            if not isinstance(page_content, str):
                continue
            chunks.append(
                DocumentChunk(
                    page_content=page_content,
                    vector=source.get("vector"),
                    metadata=dict(metadata),
                )
            )
        return chunks

    async def rank_source_chunks(
        self,
        runtime: GraphIndexRuntime,
        source_chunk_ids: Sequence[str],
        query_vector: Sequence[float],
        limit: int,
        *,
        allowed_document_ids: Sequence[str] | None,
        file_names: Sequence[str],
    ) -> list[SourceChunkVectorHit]:
        source_ids = tuple(
            dict.fromkeys(
                str(source_id)
                for source_id in source_chunk_ids
                if str(source_id)
            )
        )
        maximum = min(max(0, int(limit)), len(source_ids))
        if not source_ids or not query_vector or maximum <= 0:
            return []
        if allowed_document_ids is not None and not allowed_document_ids:
            return []

        vector_field = Field.VECTOR.value
        filters: list[dict[str, Any]] = [
            {"terms": {"metadata.doc_id": list(source_ids)}},
            {"term": {"metadata.knowledge_id": runtime.knowledge_id}},
            {"term": {"metadata.status": 1}},
            {"exists": {"field": vector_field}},
        ]
        if allowed_document_ids is not None:
            filters.append(
                {
                    "terms": {
                        "metadata.document_id": list(allowed_document_ids)
                    }
                }
            )
        if file_names:
            filters.append(
                {"terms": {"metadata.file_name": list(file_names)}}
            )

        result = await self._client.search(
            index=runtime.chunk_index_name,
            size=maximum,
            query={
                "script_score": {
                    "query": {"bool": {"filter": filters}},
                    "script": {
                        "source": (
                            "cosineSimilarity(params.query_vector, "
                            f"'{vector_field}') + 1.0"
                        ),
                        "params": {"query_vector": list(query_vector)},
                    },
                }
            },
            sort=[
                {"_score": {"order": "desc"}},
                {"metadata.doc_id": {"order": "asc"}},
            ],
        )
        raise_on_shard_failures(result, "rank graph source chunks")

        allowed_sources = set(source_ids)
        ranked: list[SourceChunkVectorHit] = []
        seen: set[str] = set()
        for hit in self._hits(result):
            source = hit.get("_source") or {}
            metadata = source.get("metadata") or {}
            if not isinstance(metadata, Mapping):
                continue
            source_id = str(metadata.get("doc_id") or "")
            if (
                not source_id
                or source_id not in allowed_sources
                or source_id in seen
            ):
                continue
            seen.add(source_id)
            ranked.append(
                SourceChunkVectorHit(
                    source_chunk_id=source_id,
                    score=self._script_score_to_normalized_similarity(
                        hit.get("_score")
                    ),
                )
            )
        return ranked

    async def _projection_search(
        self,
        runtime: GraphIndexRuntime,
        query_vector: Sequence[float],
        top_n: int,
        projection_type: str,
        min_similarity: float,
    ) -> Mapping[str, Any]:
        vector_field = f"q_{len(query_vector)}_vec"
        result = await self._client.search(
            index=runtime.graph_index_name,
            size=top_n,
            min_score=float(min_similarity) + 1.0,
            query={
                "script_score": {
                    "query": self._graph_query(
                        runtime.knowledge_id,
                        projection_type,
                        [{"exists": {"field": vector_field}}],
                    ),
                    "script": {
                        "source": (
                            "cosineSimilarity(params.query_vector, "
                            f"'{vector_field}') + 1.0"
                        ),
                        "params": {"query_vector": list(query_vector)},
                    },
                }
            },
        )
        raise_on_shard_failures(result, f"search {projection_type}")
        return result

    def _parse_group_evidence(
        self,
        result: Mapping[str, Any],
        document_type: str,
        projection_key: str,
    ) -> list[GraphEvidenceHit]:
        evidence_hits: list[GraphEvidenceHit] = []
        for hit in self._hits(result):
            source = hit.get("_source") or {}
            if not isinstance(source, Mapping):
                continue
            if source.get("knowledge_graph_kwd") != document_type:
                continue
            source_chunk_id = str(source.get("source_chunk_id_kwd") or "")
            document_id = str(source.get("document_id") or "")
            if not source_chunk_id or not document_id:
                continue
            if document_type == ENTITY_EVIDENCE:
                entity_key = str(source.get("entity_key_kwd") or "")
                if entity_key != projection_key:
                    continue
                evidence_hits.append(
                    GraphEvidenceHit(
                        evidence_id=str(hit.get("_id") or ""),
                        source_chunk_id=source_chunk_id,
                        document_id=document_id,
                        score=float(source.get("confidence_flt") or 0.0),
                        entity_key=entity_key,
                        entity_name=(
                            str(source.get("entity_name_kwd") or "") or None
                        ),
                    )
                )
            elif document_type == RELATION_EVIDENCE:
                relation_key = str(source.get("relation_key_kwd") or "")
                if relation_key != projection_key:
                    continue
                evidence_hits.append(
                    GraphEvidenceHit(
                        evidence_id=str(hit.get("_id") or ""),
                        source_chunk_id=source_chunk_id,
                        document_id=document_id,
                        score=float(source.get("confidence_flt") or 0.0),
                        relation_key=relation_key,
                        relation_label=self._relation_label(source),
                    )
                )
        return evidence_hits

    async def _load_relation_evidence_with_filters(
        self,
        index_name: str,
        knowledge_id: str,
        extra_filters: Sequence[Mapping[str, Any]],
        context: str,
    ) -> list[RelationEvidence]:
        hits = await self._collect_search_after_hits(
            index_name=index_name,
            query=self._graph_query(
                knowledge_id,
                RELATION_EVIDENCE,
                extra_filters,
            ),
            sort=[
                {"relation_key_kwd": {"order": "asc"}},
                {"source_chunk_id_kwd": {"order": "asc", "missing": "_last"}},
                {"document_id": {"order": "asc", "missing": "_last"}},
            ],
            context=context,
        )
        evidence: list[RelationEvidence] = []
        for hit in hits:
            source = hit.get("_source") or {}
            evidence.append(
                RelationEvidence(
                    id=str(hit.get("_id") or source.get("id") or ""),
                    kb_id=str(source["kb_id"]),
                    document_id=str(source["document_id"]),
                    source_chunk_id=str(source["source_chunk_id_kwd"]),
                    relation_key=str(source["relation_key_kwd"]),
                    from_entity_key=str(source["from_entity_key_kwd"]),
                    from_entity_name=str(source["from_entity_name_kwd"]),
                    to_entity_key=str(source["to_entity_key_kwd"]),
                    to_entity_name=str(source["to_entity_name_kwd"]),
                    predicate=str(source["predicate_kwd"]),
                    description=str(source.get("description") or ""),
                    keywords=tuple(
                        str(value)
                        for value in (source.get("keywords_kwd") or ())
                        if str(value).strip()
                    ),
                    directed=bool(source.get("directed_int")),
                    confidence=float(source.get("confidence_flt") or 0.0),
                )
            )
        return evidence

    async def _write_document_map(
        self,
        index_name: str,
        knowledge_id: str,
        document_id: str,
        *,
        entity_keys: set[str],
        relation_keys: set[str],
        source_chunk_ids: set[str],
        ensure_valid: Callable[[], None] | None,
    ) -> None:
        source = {
            "knowledge_graph_kwd": DOCUMENT_PROJECTION_MAP,
            "kb_id": knowledge_id,
            "document_id": document_id,
            "entity_keys_kwd": sorted(entity_keys),
            "relation_keys_kwd": sorted(relation_keys),
            "source_chunk_ids_kwd": sorted(source_chunk_ids),
            "updated_at": utcnow_naive().strftime("%Y-%m-%d %H:%M:%S"),
        }
        await self._bulk(
            self._index_operation(
                index_name,
                document_map_id(knowledge_id, document_id),
                source,
            ),
            ensure_valid=ensure_valid,
        )

    async def _bulk(
        self,
        operations: Sequence[Mapping[str, Any]],
        *,
        ensure_valid: Callable[[], None] | None,
    ) -> None:
        if not operations:
            return
        self._ensure_valid(ensure_valid)
        result = await self._client.bulk(operations=list(operations), refresh=False)
        if result.get("errors"):
            failures = []
            for item in result.get("items") or []:
                operation = next(iter(item.values()), {})
                if operation.get("error"):
                    failures.append(operation.get("error"))
                if len(failures) == 3:
                    break
            raise RuntimeError(f"graph bulk write failed: {failures}")

    @staticmethod
    def _ensure_valid(ensure_valid: Callable[[], None] | None) -> None:
        if ensure_valid is not None:
            ensure_valid()

    @staticmethod
    def _index_operation(
        index_name: str,
        document_id: str,
        source: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        return [
            {"index": {"_index": index_name, "_id": document_id}},
            dict(source),
        ]

    @staticmethod
    def _entity_evidence_source(evidence: EntityEvidence) -> dict[str, Any]:
        return {
            "knowledge_graph_kwd": ENTITY_EVIDENCE,
            "kb_id": evidence.kb_id,
            "document_id": evidence.document_id,
            "source_chunk_id_kwd": evidence.source_chunk_id,
            "entity_key_kwd": evidence.entity_key,
            "entity_name_kwd": evidence.entity_name,
            "entity_type_kwd": evidence.entity_type,
            "aliases_kwd": list(evidence.aliases),
            "description": evidence.description,
            "evidence_text": evidence.description[:300],
            "confidence_flt": evidence.confidence,
        }

    @staticmethod
    def _relation_evidence_source(evidence: RelationEvidence) -> dict[str, Any]:
        return {
            "knowledge_graph_kwd": RELATION_EVIDENCE,
            "kb_id": evidence.kb_id,
            "document_id": evidence.document_id,
            "source_chunk_id_kwd": evidence.source_chunk_id,
            "relation_key_kwd": evidence.relation_key,
            "from_entity_key_kwd": evidence.from_entity_key,
            "from_entity_name_kwd": evidence.from_entity_name,
            "to_entity_key_kwd": evidence.to_entity_key,
            "to_entity_name_kwd": evidence.to_entity_name,
            "predicate_kwd": evidence.predicate,
            "keywords_kwd": list(evidence.keywords),
            "directed_int": int(evidence.directed),
            "description": evidence.description,
            "evidence_text": evidence.description[:300],
            "confidence_flt": evidence.confidence,
        }

    @staticmethod
    def _graph_query(
        knowledge_id: str,
        document_types: str | Sequence[str],
        extra_filters: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        type_filter: dict[str, Any]
        if isinstance(document_types, str):
            type_filter = {"term": {"knowledge_graph_kwd": document_types}}
        else:
            type_filter = {
                "terms": {"knowledge_graph_kwd": list(document_types)}
            }
        return {
            "bool": {
                "filter": [
                    {"term": {"kb_id": knowledge_id}},
                    type_filter,
                    *[dict(item) for item in extra_filters],
                ]
            }
        }

    @staticmethod
    def _hits(result: Mapping[str, Any]) -> list[dict[str, Any]]:
        hits = (result.get("hits") or {}).get("hits") or []
        return [dict(hit) for hit in hits if isinstance(hit, Mapping)]

    @classmethod
    def _relation_projection_hits(
        cls,
        result: Mapping[str, Any],
        *,
        script_score: bool = False,
    ) -> list[RelationProjectionHit]:
        hits: list[RelationProjectionHit] = []
        for hit in cls._hits(result):
            source = hit.get("_source") or {}
            if not isinstance(source, Mapping):
                continue
            if not source.get("relation_key_kwd"):
                continue
            directed = source.get("directed_int")
            hits.append(
                RelationProjectionHit(
                    relation_key=str(source["relation_key_kwd"]),
                    from_entity_key=str(source["from_entity_key_kwd"]),
                    from_entity_name=str(source.get("from_entity_name_kwd") or ""),
                    to_entity_key=str(source["to_entity_key_kwd"]),
                    to_entity_name=str(source.get("to_entity_name_kwd") or ""),
                    predicate=str(source.get("predicate_kwd") or ""),
                    label=cls._relation_label(source),
                    description=str(source.get("description") or ""),
                    keywords=tuple(
                        str(value)
                        for value in (source.get("keywords_kwd") or ())
                        if str(value).strip()
                    ),
                    directed=True if directed is None else bool(directed),
                    score=(
                        cls._script_score_to_cosine(hit.get("_score"))
                        if script_score
                        else float(hit.get("_score") or 0.0)
                    ),
                    evidence_count=int(source.get("evidence_count_int") or 0),
                    document_count=int(source.get("document_count_int") or 0),
                )
            )
        return hits

    @staticmethod
    def _script_score_to_cosine(raw_score: Any) -> float:
        try:
            score = float(raw_score) - 1.0
        except (TypeError, ValueError):
            return -1.0
        return max(-1.0, min(1.0, score))

    @classmethod
    def _script_score_to_normalized_similarity(cls, raw_score: Any) -> float:
        cosine_score = cls._script_score_to_cosine(raw_score)
        return max(0.0, min(1.0, (cosine_score + 1.0) / 2.0))

    @staticmethod
    def _relation_label(source: Mapping[str, Any]) -> str:
        from_name = str(source.get("from_entity_name_kwd") or "")
        predicate = str(source.get("predicate_kwd") or "")
        to_name = str(source.get("to_entity_name_kwd") or "")
        return " -> ".join(item for item in (from_name, predicate, to_name) if item)
