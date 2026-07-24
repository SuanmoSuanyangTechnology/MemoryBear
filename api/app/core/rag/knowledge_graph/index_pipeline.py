import asyncio
import logging
import time
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Any

from app.core.config import settings
from app.core.rag.knowledge_graph.batching import (
    build_extraction_batches,
    select_source_chunks,
)
from app.core.rag.knowledge_graph.extraction_cache import GraphExtractionCache
from app.core.rag.knowledge_graph.models import (
    EntityEvidence,
    ExtractionBatch,
    ExtractionResult,
    GraphIndexRuntime,
    RelationEvidence,
)
from app.core.rag.knowledge_graph.normalizer import (
    entity_evidence_id,
    entity_key,
    normalize_name,
    relation_evidence_id,
    relation_key,
)


logger = logging.getLogger(__name__)


def _clean_display(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).split()).strip()


def _prefer_evidence(current: Any | None, candidate: Any) -> Any:
    if current is None or candidate.confidence > current.confidence:
        return candidate
    return current


def _clean_keywords(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                keyword
                for value in values
                if (keyword := _clean_display(value))
            }
        )
    )


def _bind_result_to_batch(
    result: ExtractionResult,
    batch: ExtractionBatch,
) -> ExtractionResult:
    if len(batch.source_chunk_ids) != 1:
        raise ValueError("extraction batch must contain exactly one source chunk")
    source_chunk_id = batch.source_chunk_ids[0]
    bound = result.model_copy(deep=True)
    for entity in bound.entities:
        entity.source_chunk_ids = [source_chunk_id]
    for relation in bound.relations:
        relation.source_chunk_ids = [source_chunk_id]
    return bound


def materialize_evidence(
    knowledge_id: str,
    document_id: str,
    results: Sequence[ExtractionResult],
) -> tuple[list[EntityEvidence], list[RelationEvidence]]:
    entity_items: dict[str, EntityEvidence] = {}
    relation_items: dict[str, RelationEvidence] = {}

    for result in results:
        entities_by_ref: dict[str, tuple[str, str, str]] = {}
        for entity in result.entities:
            display_name = _clean_display(entity.name)
            display_type = _clean_display(entity.entity_type)
            if not display_name or not display_type:
                logger.warning(
                    "[EvidenceGraph] evidence_skipped"
                    " kb_id=%s document_id=%s reason=empty_entity_fields",
                    knowledge_id,
                    document_id,
                )
                continue
            normalized_key = entity_key(
                knowledge_id,
                display_name,
                display_type,
            )
            entities_by_ref[entity.ref] = (
                normalized_key,
                display_name,
                display_type,
            )
            aliases = tuple(
                dict.fromkeys(
                    alias
                    for raw_alias in entity.aliases
                    if (alias := _clean_display(raw_alias))
                )
            )
            description = _clean_display(entity.description)[:300]
            for source_chunk_id in dict.fromkeys(entity.source_chunk_ids):
                source_id = str(source_chunk_id).strip()
                if not source_id:
                    continue
                evidence = EntityEvidence(
                    id=entity_evidence_id(
                        knowledge_id,
                        document_id,
                        source_id,
                        normalized_key,
                    ),
                    kb_id=knowledge_id,
                    document_id=document_id,
                    source_chunk_id=source_id,
                    entity_key=normalized_key,
                    entity_name=display_name,
                    entity_type=display_type,
                    description=description,
                    aliases=aliases,
                    confidence=entity.confidence,
                )
                entity_items[evidence.id] = _prefer_evidence(
                    entity_items.get(evidence.id),
                    evidence,
                )

        for relation in result.relations:
            from_entity = entities_by_ref.get(relation.from_ref)
            to_entity = entities_by_ref.get(relation.to_ref)
            if from_entity is None or to_entity is None:
                logger.warning(
                    "[EvidenceGraph] evidence_skipped"
                    " kb_id=%s document_id=%s reason=unresolved_relation_endpoint",
                    knowledge_id,
                    document_id,
                )
                continue
            predicate = _clean_display(relation.predicate)
            if not predicate:
                logger.warning(
                    "[EvidenceGraph] evidence_skipped"
                    " kb_id=%s document_id=%s reason=empty_relation_predicate",
                    knowledge_id,
                    document_id,
                )
                continue
            normalized_relation_key = relation_key(
                knowledge_id,
                from_entity[0],
                predicate,
                to_entity[0],
                relation.directed,
            )
            description = _clean_display(relation.description)[:300]
            keywords = _clean_keywords(relation.keywords)
            for source_chunk_id in dict.fromkeys(relation.source_chunk_ids):
                source_id = str(source_chunk_id).strip()
                if not source_id:
                    continue
                evidence = RelationEvidence(
                    id=relation_evidence_id(
                        knowledge_id,
                        document_id,
                        source_id,
                        normalized_relation_key,
                    ),
                    kb_id=knowledge_id,
                    document_id=document_id,
                    source_chunk_id=source_id,
                    relation_key=normalized_relation_key,
                    from_entity_key=from_entity[0],
                    from_entity_name=from_entity[1],
                    to_entity_key=to_entity[0],
                    to_entity_name=to_entity[1],
                    predicate=predicate,
                    description=description,
                    keywords=keywords,
                    directed=relation.directed,
                    confidence=relation.confidence,
                )
                relation_items[evidence.id] = _prefer_evidence(
                    relation_items.get(evidence.id),
                    evidence,
                )

    return (
        sorted(entity_items.values(), key=lambda item: item.id),
        sorted(relation_items.values(), key=lambda item: item.id),
    )


class KnowledgeGraphIndexPipeline:
    def __init__(
        self,
        store: Any,
        extractor: Any,
        embedding: Any,
        lock_guard: Any,
        extraction_cache: GraphExtractionCache | None = None,
    ) -> None:
        self._store = store
        self._extractor = extractor
        self._embedding = embedding
        self._lock_guard = lock_guard
        self._extraction_cache = extraction_cache or GraphExtractionCache()

    async def sync_document(
        self,
        runtime: GraphIndexRuntime,
        document_id: str,
        document_active: bool,
    ) -> None:
        started_at = time.perf_counter()
        stage = "ensure_graph_index"
        try:
            self._lock_guard.ensure_valid()
            await self._store.ensure_graph_index(runtime.graph_index_name)
            stage = "refresh_sources"
            self._lock_guard.ensure_valid()
            await self._store.refresh_sources(
                runtime.chunk_index_name,
                runtime.graph_index_name,
            )
            stage = "load_document_chunks"
            hits = (
                await self._store.load_document_chunks(
                    runtime.chunk_index_name,
                    runtime.knowledge_id,
                    document_id,
                )
                if document_active
                else []
            )
            chunks = select_source_chunks(hits)
            batches = build_extraction_batches(chunks)
            logger.info(
                "[EvidenceGraph] index_input"
                " kb_id=%s document_id=%s active=%s"
                " raw_hits=%d source_chunks=%d batches=%d",
                runtime.knowledge_id,
                document_id,
                str(bool(document_active)).lower(),
                len(hits),
                len(chunks),
                len(batches),
            )

            stage = "extract_batches"
            results = await self._extract_batches(batches, runtime)
            stage = "materialize_evidence"
            entity_evidence, relation_evidence = materialize_evidence(
                runtime.knowledge_id,
                document_id,
                results,
            )

            stage = "replace_document_evidence"
            self._lock_guard.ensure_valid()
            affected = await self._store.replace_document_evidence(
                runtime.graph_index_name,
                runtime.knowledge_id,
                document_id,
                entity_evidence,
                relation_evidence,
                ensure_valid=self._lock_guard.ensure_valid,
            )
            stage = "rebuild_relation_projections"
            self._lock_guard.ensure_valid()
            await self._rebuild_relation_projections(
                runtime,
                affected.relation_keys,
            )
            stage = "rebuild_entity_projections"
            self._lock_guard.ensure_valid()
            await self._rebuild_entity_projections(
                runtime,
                affected.entity_keys,
            )
            stage = "finish_document_map"
            self._lock_guard.ensure_valid()
            await self._store.finish_document_map(
                runtime.graph_index_name,
                runtime.knowledge_id,
                document_id,
                entity_evidence,
                relation_evidence,
                ensure_valid=self._lock_guard.ensure_valid,
            )
            stage = "refresh_graph"
            self._lock_guard.ensure_valid()
            await self._store.refresh_graph(runtime.graph_index_name)
            logger.info(
                "[EvidenceGraph] index_done"
                " kb_id=%s document_id=%s"
                " entity_evidence=%d relation_evidence=%d"
                " affected_entities=%d affected_relations=%d elapsed_ms=%d",
                runtime.knowledge_id,
                document_id,
                len(entity_evidence),
                len(relation_evidence),
                len(affected.entity_keys),
                len(affected.relation_keys),
                self._elapsed_ms(started_at),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "[EvidenceGraph] index_failed"
                " kb_id=%s document_id=%s stage=%s"
                " error_type=%s elapsed_ms=%d",
                runtime.knowledge_id,
                document_id,
                stage,
                type(exc).__name__,
                self._elapsed_ms(started_at),
            )
            raise

    async def rebuild_knowledge(
        self,
        runtime: GraphIndexRuntime,
        active_document_ids: tuple[str, ...],
    ) -> None:
        started_at = time.perf_counter()
        active_ids = tuple(dict.fromkeys(active_document_ids))
        logger.info(
            "[EvidenceGraph] rebuild_start kb_id=%s active_documents=%d",
            runtime.knowledge_id,
            len(active_ids),
        )
        for document_id in active_ids:
            await self.sync_document(runtime, document_id, True)

        self._lock_guard.ensure_valid()
        maps = await self._store.list_document_maps(
            runtime.graph_index_name,
            runtime.knowledge_id,
        )
        active_set = set(active_ids)
        stale_ids = sorted(
            {
                str(item.get("document_id"))
                for item in maps
                if item.get("document_id") is not None
                and str(item.get("document_id")) not in active_set
            }
        )
        for document_id in stale_ids:
            await self.sync_document(runtime, document_id, False)

        self._lock_guard.ensure_valid()
        await self._store.refresh_graph(runtime.graph_index_name)
        logger.info(
            "[EvidenceGraph] rebuild_done"
            " kb_id=%s active_documents=%d stale_documents=%d elapsed_ms=%d",
            runtime.knowledge_id,
            len(active_ids),
            len(stale_ids),
            self._elapsed_ms(started_at),
        )

    async def clear_knowledge(self, runtime: GraphIndexRuntime) -> None:
        started_at = time.perf_counter()
        self._lock_guard.ensure_valid()
        await self._store.clear_evidence_graph(
            runtime.graph_index_name,
            runtime.knowledge_id,
            ensure_valid=self._lock_guard.ensure_valid,
        )
        logger.info(
            "[EvidenceGraph] clear_done kb_id=%s elapsed_ms=%d",
            runtime.knowledge_id,
            self._elapsed_ms(started_at),
        )

    async def _extract_batches(
        self,
        batches: Sequence[ExtractionBatch],
        runtime: GraphIndexRuntime | None = None,
    ) -> list[ExtractionResult]:
        if not batches:
            return []
        semaphore = asyncio.Semaphore(
            settings.KNOWLEDGE_GRAPH_EXTRACT_MAX_CONCURRENCY
        )
        cache_hits = 0
        cache_misses = 0
        llm_calls = 0
        cache_stores = 0

        async def extract_one(batch: ExtractionBatch) -> ExtractionResult:
            nonlocal cache_hits, cache_misses, llm_calls, cache_stores
            async with semaphore:
                cache_key = (
                    self._extraction_cache.build_key(runtime, batch)
                    if runtime is not None
                    else None
                )
                if cache_key is not None:
                    cached = await self._extraction_cache.get(cache_key)
                    if cached is not None:
                        cache_hits += 1
                        return _bind_result_to_batch(cached, batch)
                    cache_misses += 1
                llm_calls += 1
                result = _bind_result_to_batch(
                    await self._extractor.extract(batch),
                    batch,
                )
                if cache_key is not None:
                    if await self._extraction_cache.set(cache_key, result):
                        cache_stores += 1
                return result

        tasks = [asyncio.create_task(extract_one(batch)) for batch in batches]
        try:
            results = list(await asyncio.gather(*tasks))
            if runtime is not None:
                logger.info(
                    "[EvidenceGraph] extraction_batches"
                    " kb_id=%s batches=%d cache_hits=%d"
                    " cache_misses=%d llm_calls=%d cache_stores=%d",
                    runtime.knowledge_id,
                    len(batches),
                    cache_hits,
                    cache_misses,
                    llm_calls,
                    cache_stores,
                )
            return results
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _rebuild_relation_projections(
        self,
        runtime: GraphIndexRuntime,
        relation_keys: Sequence[str],
    ) -> None:
        affected_keys = tuple(sorted(set(relation_keys)))
        if not affected_keys:
            return
        evidence = await self._store.load_relation_evidence(
            runtime.graph_index_name,
            runtime.knowledge_id,
            affected_keys,
        )
        grouped: defaultdict[str, list[RelationEvidence]] = defaultdict(list)
        for item in evidence:
            grouped[item.relation_key].append(item)

        projections: list[dict[str, Any]] = []
        texts: list[str] = []
        delete_keys: list[str] = []
        for key in affected_keys:
            items = sorted(grouped.get(key, ()), key=lambda item: item.id)
            if not items:
                delete_keys.append(key)
                continue
            from_name = self._most_common(item.from_entity_name for item in items)
            to_name = self._most_common(item.to_entity_name for item in items)
            predicate = self._most_common(item.predicate for item in items)
            description = self._aggregate_descriptions(items)
            keywords = self._aggregate_relation_keywords(items)
            projection = {
                "relation_key_kwd": key,
                "from_entity_key_kwd": items[0].from_entity_key,
                "from_entity_name_kwd": from_name,
                "to_entity_key_kwd": items[0].to_entity_key,
                "to_entity_name_kwd": to_name,
                "predicate_kwd": predicate,
                "keywords_kwd": list(keywords),
                "directed_int": int(items[0].directed),
                "description": description,
                "evidence_count_int": len(items),
                "document_count_int": len({item.document_id for item in items}),
            }
            projections.append(projection)
            texts.append(
                "\n".join(
                    part
                    for part in (
                        ", ".join(keywords),
                        f"{from_name} -> {predicate} -> {to_name}",
                        description,
                    )
                    if part
                )
            )

        await self._embed_projections(runtime, projections, texts)
        self._lock_guard.ensure_valid()
        await self._store.write_relation_projections(
            runtime.graph_index_name,
            runtime.knowledge_id,
            projections,
            tuple(delete_keys),
            ensure_valid=self._lock_guard.ensure_valid,
        )

    async def _rebuild_entity_projections(
        self,
        runtime: GraphIndexRuntime,
        entity_keys: Sequence[str],
    ) -> None:
        affected_keys = tuple(sorted(set(entity_keys)))
        if not affected_keys:
            return
        entity_evidence, relation_evidence = await asyncio.gather(
            self._store.load_entity_evidence(
                runtime.graph_index_name,
                runtime.knowledge_id,
                affected_keys,
            ),
            self._store.load_relations_for_entity_keys(
                runtime.graph_index_name,
                runtime.knowledge_id,
                affected_keys,
            ),
        )
        grouped: defaultdict[str, list[EntityEvidence]] = defaultdict(list)
        for item in entity_evidence:
            grouped[item.entity_key].append(item)
        degrees = self._entity_degrees(relation_evidence)

        projections: list[dict[str, Any]] = []
        texts: list[str] = []
        delete_keys: list[str] = []
        for key in affected_keys:
            items = sorted(grouped.get(key, ()), key=lambda item: item.id)
            if not items:
                delete_keys.append(key)
                continue
            name = self._most_common(item.entity_name for item in items)
            entity_type = self._most_common(item.entity_type for item in items)
            aliases = sorted(
                {
                    alias
                    for item in items
                    for alias in item.aliases
                    if normalize_name(alias) != normalize_name(name)
                },
                key=normalize_name,
            )
            description = self._aggregate_descriptions(items)
            projection = {
                "entity_key_kwd": key,
                "entity_name_kwd": name,
                "entity_type_kwd": entity_type,
                "aliases_kwd": aliases,
                "description": description,
                "evidence_count_int": len(items),
                "document_count_int": len({item.document_id for item in items}),
                "degree_int": degrees.get(key, 0),
            }
            projections.append(projection)
            texts.append(f"{name}\n{entity_type}\n{description}".strip())

        await self._embed_projections(runtime, projections, texts)
        self._lock_guard.ensure_valid()
        await self._store.write_entity_projections(
            runtime.graph_index_name,
            runtime.knowledge_id,
            projections,
            tuple(delete_keys),
            ensure_valid=self._lock_guard.ensure_valid,
        )

    async def _embed_projections(
        self,
        runtime: GraphIndexRuntime,
        projections: list[dict[str, Any]],
        texts: list[str],
    ) -> None:
        if not projections:
            return
        vectors = await self._embedding.aembed_documents(texts)
        if len(vectors) != len(projections):
            raise ValueError("embedding count does not match graph projections")
        dimensions = {len(vector) for vector in vectors}
        if 0 in dimensions or len(dimensions) != 1:
            raise ValueError("graph projection embeddings have invalid dimensions")
        dimension = dimensions.pop()
        self._lock_guard.ensure_valid()
        vector_field = await self._store.ensure_vector_mapping(
            runtime.graph_index_name,
            dimension,
        )
        for projection, vector in zip(projections, vectors, strict=True):
            projection[vector_field] = list(vector)

    @staticmethod
    def _aggregate_descriptions(items: Sequence[Any]) -> str:
        descriptions: list[str] = []
        for item in sorted(items, key=lambda value: (-value.confidence, value.id)):
            description = _clean_display(item.description)
            if description and description not in descriptions:
                descriptions.append(description)
            if len(descriptions) == 5:
                break
        return " | ".join(descriptions)

    @staticmethod
    def _aggregate_relation_keywords(
        items: Sequence[RelationEvidence],
    ) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    keyword
                    for item in items
                    for value in item.keywords
                    if (keyword := _clean_display(value))
                }
            )
        )

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return int((time.perf_counter() - started_at) * 1000)

    @staticmethod
    def _most_common(values: Sequence[str] | Any) -> str:
        ordered = [str(value) for value in values]
        counts = Counter(ordered)
        return max(
            ordered,
            key=lambda value: (counts[value], -ordered.index(value)),
        )

    @staticmethod
    def _entity_degrees(
        evidence: Sequence[RelationEvidence],
    ) -> dict[str, int]:
        unique_relations: dict[str, RelationEvidence] = {}
        for item in sorted(evidence, key=lambda value: value.id):
            unique_relations.setdefault(item.relation_key, item)
        neighbors: defaultdict[str, set[str]] = defaultdict(set)
        for item in unique_relations.values():
            neighbors[item.from_entity_key].add(item.to_entity_key)
            neighbors[item.to_entity_key].add(item.from_entity_key)
        return {key: len(values) for key, values in neighbors.items()}
