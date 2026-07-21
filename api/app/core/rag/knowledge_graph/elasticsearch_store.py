from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from elasticsearch import AsyncElasticsearch

from app.core.rag.knowledge_graph.models import (
    AffectedProjectionKeys,
    EntityEvidence,
    EntityProjectionHit,
    GraphEvidenceHit,
    GraphIndexRuntime,
    RelationEvidence,
    RelationProjectionHit,
)
from app.core.rag.knowledge_graph.normalizer import (
    document_map_id,
    projection_id,
)
from app.core.rag.models.chunk import DocumentChunk
from app.core.rag.retrieval.elasticsearch_queries import raise_on_shard_failures
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


class GraphElasticsearchStore:
    def __init__(self, client: AsyncElasticsearch) -> None:
        self._client = client

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
        result = await self._client.search(
            index=chunk_index_name,
            size=10000,
            query={"bool": {"filter": filters}},
            sort=[
                {"metadata.sort_id": {"order": "asc", "unmapped_type": "long"}},
                {"metadata.doc_id": {"order": "asc"}},
            ],
        )
        raise_on_shard_failures(result, "load graph source document")

        scoped_hits: list[dict[str, Any]] = []
        for hit in self._hits(result):
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
        result = await self._client.search(
            index=index_name,
            size=10000,
            query=self._graph_query(
                knowledge_id,
                EVIDENCE_TYPES,
                [{"term": {"document_id": document_id}}],
            ),
            source=[
                "knowledge_graph_kwd",
                "entity_key_kwd",
                "relation_key_kwd",
            ],
        )
        raise_on_shard_failures(result, "load graph document evidence keys")
        entity_keys: set[str] = set()
        relation_keys: set[str] = set()
        for hit in self._hits(result):
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
        await self._client.delete_by_query(
            index=index_name,
            conflicts="proceed",
            refresh=False,
            query=self._graph_query(
                knowledge_id,
                EVIDENCE_TYPES,
                [{"term": {"document_id": document_id}}],
            ),
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
        result = await self._client.search(
            index=index_name,
            size=10000,
            query=self._graph_query(
                knowledge_id,
                ENTITY_EVIDENCE,
                [{"terms": {"entity_key_kwd": list(entity_keys)}}],
            ),
        )
        raise_on_shard_failures(result, "load entity evidence")
        evidence: list[EntityEvidence] = []
        for hit in self._hits(result):
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
        result = await self._client.search(
            index=index_name,
            size=10000,
            query=self._graph_query(knowledge_id, DOCUMENT_PROJECTION_MAP),
            sort=[{"document_id": {"order": "asc"}}],
        )
        raise_on_shard_failures(result, "list graph document maps")
        return [
            dict(source)
            for hit in self._hits(result)
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
        await self._client.delete_by_query(
            index=index_name,
            conflicts="proceed",
            refresh=True,
            query=self._graph_query(knowledge_id, EVIDENCE_GRAPH_TYPES),
        )

    async def clear_all_graph_documents(
        self,
        index_name: str,
        knowledge_id: str,
        *,
        ensure_valid: Callable[[], None] | None = None,
    ) -> None:
        self._ensure_valid(ensure_valid)
        await self._client.delete_by_query(
            index=index_name,
            conflicts="proceed",
            refresh=True,
            query={
                "bool": {
                    "filter": [
                        {"term": {"kb_id": knowledge_id}},
                        {"exists": {"field": "knowledge_graph_kwd"}},
                    ]
                }
            },
        )

    async def search_entity_projections(
        self,
        runtime: GraphIndexRuntime,
        query_vector: Sequence[float],
        top_n: int,
    ) -> list[EntityProjectionHit]:
        result = await self._projection_search(
            runtime,
            query_vector,
            top_n,
            ENTITY_PROJECTION,
        )
        return [
            EntityProjectionHit(
                entity_key=str(source["entity_key_kwd"]),
                entity_name=str(source["entity_name_kwd"]),
                score=float(hit.get("_score") or 0.0),
            )
            for hit in self._hits(result)
            if isinstance((source := hit.get("_source")), Mapping)
            and source.get("entity_key_kwd")
            and source.get("entity_name_kwd")
        ]

    async def search_relation_projections(
        self,
        runtime: GraphIndexRuntime,
        query_vector: Sequence[float],
        top_n: int,
    ) -> list[RelationProjectionHit]:
        result = await self._projection_search(
            runtime,
            query_vector,
            top_n,
            RELATION_PROJECTION,
        )
        return self._relation_projection_hits(result)

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
            size=top_n,
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
        )
        raise_on_shard_failures(result, "load graph neighbor relations")
        return self._relation_projection_hits(result)

    async def load_evidence_for_projection_keys(
        self,
        runtime: GraphIndexRuntime,
        entity_keys: Sequence[str],
        relation_keys: Sequence[str],
        evidence_per_key: int,
        allowed_document_ids: Sequence[str] | None = None,
    ) -> list[GraphEvidenceHit]:
        should: list[dict[str, Any]] = []
        if entity_keys:
            should.append({"terms": {"entity_key_kwd": list(entity_keys)}})
        if relation_keys:
            should.append({"terms": {"relation_key_kwd": list(relation_keys)}})
        if not should:
            return []

        extra_filters: list[dict[str, Any]] = [
            {"bool": {"should": should, "minimum_should_match": 1}}
        ]
        if allowed_document_ids is not None:
            if not allowed_document_ids:
                return []
            extra_filters.append(
                {"terms": {"document_id": list(allowed_document_ids)}}
            )
        result = await self._client.search(
            index=runtime.graph_index_name,
            size=max(1, (len(entity_keys) + len(relation_keys)) * evidence_per_key * 4),
            query=self._graph_query(
                runtime.knowledge_id,
                EVIDENCE_TYPES,
                extra_filters,
            ),
        )
        raise_on_shard_failures(result, "load graph projection evidence")

        counts: defaultdict[tuple[str, str], int] = defaultdict(int)
        evidence_hits: list[GraphEvidenceHit] = []
        for hit in self._hits(result):
            source = hit.get("_source") or {}
            document_type = source.get("knowledge_graph_kwd")
            if document_type == ENTITY_EVIDENCE:
                key = str(source.get("entity_key_kwd") or "")
                group = (ENTITY_EVIDENCE, key)
                entity_name = str(source.get("entity_name_kwd") or "") or None
                relation_label = None
            elif document_type == RELATION_EVIDENCE:
                key = str(source.get("relation_key_kwd") or "")
                group = (RELATION_EVIDENCE, key)
                entity_name = None
                relation_label = self._relation_label(source)
            else:
                continue
            if not key or counts[group] >= evidence_per_key:
                continue
            counts[group] += 1
            evidence_hits.append(
                GraphEvidenceHit(
                    source_chunk_id=str(source["source_chunk_id_kwd"]),
                    document_id=str(source["document_id"]),
                    score=float(hit.get("_score") or 0.0),
                    entity_name=entity_name,
                    relation_label=relation_label,
                )
            )
        return evidence_hits

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

    async def _projection_search(
        self,
        runtime: GraphIndexRuntime,
        query_vector: Sequence[float],
        top_n: int,
        projection_type: str,
    ) -> Mapping[str, Any]:
        vector_field = f"q_{len(query_vector)}_vec"
        result = await self._client.search(
            index=runtime.graph_index_name,
            size=top_n,
            query={
                "script_score": {
                    "query": self._graph_query(
                        runtime.knowledge_id,
                        projection_type,
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

    async def _load_relation_evidence_with_filters(
        self,
        index_name: str,
        knowledge_id: str,
        extra_filters: Sequence[Mapping[str, Any]],
        context: str,
    ) -> list[RelationEvidence]:
        result = await self._client.search(
            index=index_name,
            size=10000,
            query=self._graph_query(
                knowledge_id,
                RELATION_EVIDENCE,
                extra_filters,
            ),
        )
        raise_on_shard_failures(result, context)
        evidence: list[RelationEvidence] = []
        for hit in self._hits(result):
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
            "updated_at": utcnow_naive().isoformat(),
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
    ) -> list[RelationProjectionHit]:
        hits: list[RelationProjectionHit] = []
        for hit in cls._hits(result):
            source = hit.get("_source") or {}
            if not isinstance(source, Mapping):
                continue
            if not source.get("relation_key_kwd"):
                continue
            hits.append(
                RelationProjectionHit(
                    relation_key=str(source["relation_key_kwd"]),
                    from_entity_key=str(source["from_entity_key_kwd"]),
                    to_entity_key=str(source["to_entity_key_kwd"]),
                    label=cls._relation_label(source),
                    score=float(hit.get("_score") or 0.0),
                )
            )
        return hits

    @staticmethod
    def _relation_label(source: Mapping[str, Any]) -> str:
        from_name = str(source.get("from_entity_name_kwd") or "")
        predicate = str(source.get("predicate_kwd") or "")
        to_name = str(source.get("to_entity_name_kwd") or "")
        return " -> ".join(item for item in (from_name, predicate, to_name) if item)
