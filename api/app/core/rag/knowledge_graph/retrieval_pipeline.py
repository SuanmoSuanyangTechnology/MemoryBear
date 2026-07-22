import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.core.rag.knowledge_graph.models import (
    EntityProjectionHit,
    GraphEvidenceHit,
    GraphQueryAnalysis,
    GraphRetrievalRequest,
    RelationProjectionHit,
)
from app.core.rag.knowledge_graph.prompts import (
    QUERY_ANALYSIS_SYSTEM_PROMPT,
    build_query_analysis_prompt,
)
from app.core.rag.models.chunk import DocumentChunk
from app.core.rag.retrieval.elasticsearch_queries import normalize_vector


logger = logging.getLogger(__name__)

_RRF_BASE = 60
_ENTITY_WEIGHT = 0.45
_RELATION_WEIGHT = 0.40
_NEIGHBOR_WEIGHT = 0.15


@dataclass
class _ChunkMatch:
    channel_scores: dict[tuple[str, str], float] = field(default_factory=dict)
    entity_names: set[str] = field(default_factory=set)
    relation_labels: set[str] = field(default_factory=set)
    evidence_tokens: set[tuple[str, str, str]] = field(default_factory=set)

    @property
    def raw_score(self) -> float:
        return sum(self.channel_scores.values())

    @property
    def match_count(self) -> int:
        return len(self.entity_names) + len(self.relation_labels)

    @property
    def evidence_count(self) -> int:
        return len(self.evidence_tokens)

    def add_score(self, channel: str, key: str, score: float) -> None:
        token = (channel, key)
        self.channel_scores[token] = max(
            self.channel_scores.get(token, 0.0),
            max(0.0, float(score)),
        )

    def merge(self, other: "_ChunkMatch") -> None:
        for (channel, key), score in other.channel_scores.items():
            self.add_score(channel, key, score)
        self.entity_names.update(other.entity_names)
        self.relation_labels.update(other.relation_labels)
        self.evidence_tokens.update(other.evidence_tokens)


class KnowledgeGraphRetrievalPipeline:
    def __init__(
        self,
        store: Any,
        llm: Any,
        embedding: Any,
        parent_resolver: Any,
    ) -> None:
        self._store = store
        self._llm = llm
        self._embedding = embedding
        self._parent_resolver = parent_resolver

    async def retrieve(
        self,
        request: GraphRetrievalRequest,
    ) -> list[DocumentChunk]:
        started_at = time.perf_counter()
        timeout_seconds = settings.KNOWLEDGE_GRAPH_RETRIEVAL_TIMEOUT_MS / 1000
        timeout_context = asyncio.timeout(timeout_seconds)
        try:
            async with timeout_context:
                return await self._retrieve(request, started_at)
        except TimeoutError as exc:
            if timeout_context.expired():
                logger.warning(
                    "[EvidenceGraph] retrieval_failed"
                    " kb_id=%s stage=timeout error_type=%s elapsed_ms=%d",
                    request.runtime.knowledge_id,
                    type(exc).__name__,
                    self._elapsed_ms(started_at),
                )
            raise

    async def _retrieve(
        self,
        request: GraphRetrievalRequest,
        started_at: float,
    ) -> list[DocumentChunk]:
        stage = "validate_filters"
        counts = {
            "entity_hits": 0,
            "relation_hits": 0,
            "neighbor_hits": 0,
            "evidence_hits": 0,
            "matched_chunks": 0,
            "hydrated_chunks": 0,
            "scoped_chunks": 0,
            "result_count": 0,
        }
        try:
            if (
                request.allowed_document_ids is not None
                and not request.allowed_document_ids
            ):
                self._log_outcome(
                    "retrieval_empty",
                    request,
                    started_at,
                    counts,
                    reason="no_allowed_documents",
                )
                return []

            stage = "query_analysis"
            analysis = await self._analyze_query(
                request.query,
                request.runtime.knowledge_id,
            )
            entity_text = (
                " ".join(self._clean_terms(analysis.entity_terms))
                or request.query
            )
            relation_text = (
                " ".join(self._clean_terms(analysis.relation_terms))
                or request.query
            )

            stage = "query_embedding"
            entity_vector, relation_vector = await asyncio.gather(
                self._embedding.aembed_query(entity_text),
                self._embedding.aembed_query(relation_text),
            )
            stage = "projection_search"
            entity_hits, relation_hits = await asyncio.gather(
                self._store.search_entity_projections(
                    request.runtime,
                    normalize_vector(entity_vector),
                    request.entity_top_n,
                ),
                self._store.search_relation_projections(
                    request.runtime,
                    normalize_vector(relation_vector),
                    request.relation_top_n,
                ),
            )

            ranked_entities = self._deduplicate_entities(entity_hits)
            ranked_relations = self._deduplicate_relations(relation_hits)
            counts["entity_hits"] = len(ranked_entities)
            counts["relation_hits"] = len(ranked_relations)
            stage = "neighbor_search"
            neighbor_hits = await self._store.load_neighbor_relations(
                request.runtime,
                tuple(hit.entity_key for hit in ranked_entities),
                request.neighbor_top_n,
            )
            ranked_neighbors = self._deduplicate_relations(neighbor_hits)
            counts["neighbor_hits"] = len(ranked_neighbors)

            entity_ranks = {
                hit.entity_key: rank
                for rank, hit in enumerate(ranked_entities, start=1)
            }
            relation_ranks = {
                hit.relation_key: rank
                for rank, hit in enumerate(ranked_relations, start=1)
            }
            neighbor_ranks = {
                hit.relation_key: rank
                for rank, hit in enumerate(ranked_neighbors, start=1)
            }
            relation_keys = tuple(
                dict.fromkeys(
                    [hit.relation_key for hit in ranked_relations]
                    + [hit.relation_key for hit in ranked_neighbors]
                )
            )
            stage = "evidence_search"
            evidence_hits = await self._store.load_evidence_for_projection_keys(
                request.runtime,
                tuple(entity_ranks),
                relation_keys,
                request.evidence_per_key,
                allowed_document_ids=request.allowed_document_ids,
            )
            counts["evidence_hits"] = len(evidence_hits)
            if not evidence_hits:
                self._log_outcome(
                    "retrieval_empty",
                    request,
                    started_at,
                    counts,
                    reason="no_evidence",
                )
                return []

            stage = "evidence_scoring"
            matches = self._score_evidence(
                evidence_hits,
                ranked_entities,
                ranked_relations,
                ranked_neighbors,
                entity_ranks,
                relation_ranks,
                neighbor_ranks,
                request.allowed_document_ids,
            )
            counts["matched_chunks"] = len(matches)
            if not matches:
                self._log_outcome(
                    "retrieval_empty",
                    request,
                    started_at,
                    counts,
                    reason="no_scored_matches",
                )
                return []

            stage = "chunk_hydration"
            chunks = await self._store.hydrate_source_chunks(
                chunk_index_name=request.runtime.chunk_index_name,
                knowledge_id=request.runtime.knowledge_id,
                source_chunk_ids=tuple(matches),
                allowed_document_ids=request.allowed_document_ids,
                file_names=request.file_names,
            )
            counts["hydrated_chunks"] = len(chunks)
            scoped_chunks = self._scope_hydrated_chunks(request, chunks, matches)
            counts["scoped_chunks"] = len(scoped_chunks)
            if not scoped_chunks:
                self._log_outcome(
                    "retrieval_empty",
                    request,
                    started_at,
                    counts,
                    reason="no_scoped_chunks",
                )
                return []

            parent_matches = self._build_parent_matches(scoped_chunks, matches)
            for chunk in scoped_chunks:
                source_id = str((chunk.metadata or {}).get("doc_id") or "")
                chunk.metadata["score"] = matches[source_id].raw_score

            stage = "parent_resolution"
            resolved = await self._parent_resolver(
                scoped_chunks,
                request.runtime.chunk_index_name,
            )
            resolved_with_matches = self._attach_resolved_matches(
                request,
                resolved,
                matches,
                parent_matches,
            )
            stage = "rank_candidates"
            result = self._rank_and_limit(
                resolved_with_matches,
                request.max_chunks_per_document,
            )
            counts["result_count"] = len(result)
            self._log_outcome(
                "retrieval_done" if result else "retrieval_empty",
                request,
                started_at,
                counts,
                reason=None if result else "no_ranked_chunks",
            )
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[EvidenceGraph] retrieval_failed"
                " kb_id=%s stage=%s error_type=%s elapsed_ms=%d",
                request.runtime.knowledge_id,
                stage,
                type(exc).__name__,
                self._elapsed_ms(started_at),
            )
            raise

    async def _analyze_query(
        self,
        query: str,
        knowledge_id: str,
    ) -> GraphQueryAnalysis:
        try:
            raw_result = await self._llm.call_structured(
                [
                    {
                        "role": "system",
                        "content": QUERY_ANALYSIS_SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": build_query_analysis_prompt(query),
                    },
                ],
                GraphQueryAnalysis,
            )
            return GraphQueryAnalysis.model_validate(raw_result)
        except Exception as exc:
            logger.warning(
                "[EvidenceGraph] query_analysis_fallback"
                " kb_id=%s error_type=%s",
                knowledge_id,
                type(exc).__name__,
            )
            return GraphQueryAnalysis(
                entity_terms=[query],
                relation_terms=[query],
            )

    @staticmethod
    def _log_outcome(
        event: str,
        request: GraphRetrievalRequest,
        started_at: float,
        counts: dict[str, int],
        *,
        reason: str | None,
    ) -> None:
        reason_field = f" reason={reason}" if reason is not None else ""
        logger.info(
            "[EvidenceGraph] %s kb_id=%s%s"
            " entity_hits=%d relation_hits=%d neighbor_hits=%d"
            " evidence_hits=%d matched_chunks=%d hydrated_chunks=%d"
            " scoped_chunks=%d result_count=%d elapsed_ms=%d",
            event,
            request.runtime.knowledge_id,
            reason_field,
            counts["entity_hits"],
            counts["relation_hits"],
            counts["neighbor_hits"],
            counts["evidence_hits"],
            counts["matched_chunks"],
            counts["hydrated_chunks"],
            counts["scoped_chunks"],
            counts["result_count"],
            KnowledgeGraphRetrievalPipeline._elapsed_ms(started_at),
        )

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return int((time.perf_counter() - started_at) * 1000)

    @staticmethod
    def _clean_terms(terms: list[str]) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                term
                for value in terms
                if (term := " ".join(str(value).split()).strip())
            )
        )

    @staticmethod
    def _deduplicate_entities(
        hits: list[EntityProjectionHit],
    ) -> list[EntityProjectionHit]:
        by_key: dict[str, EntityProjectionHit] = {}
        for hit in hits:
            current = by_key.get(hit.entity_key)
            if current is None or hit.score > current.score:
                by_key[hit.entity_key] = hit
        return sorted(
            by_key.values(),
            key=lambda hit: (-hit.score, hit.entity_key),
        )

    @staticmethod
    def _deduplicate_relations(
        hits: list[RelationProjectionHit],
    ) -> list[RelationProjectionHit]:
        by_key: dict[str, RelationProjectionHit] = {}
        for hit in hits:
            current = by_key.get(hit.relation_key)
            if current is None or hit.score > current.score:
                by_key[hit.relation_key] = hit
        return sorted(
            by_key.values(),
            key=lambda hit: (-hit.score, hit.relation_key),
        )

    @classmethod
    def _score_evidence(
        cls,
        evidence_hits: list[GraphEvidenceHit],
        entity_hits: list[EntityProjectionHit],
        relation_hits: list[RelationProjectionHit],
        neighbor_hits: list[RelationProjectionHit],
        entity_ranks: dict[str, int],
        relation_ranks: dict[str, int],
        neighbor_ranks: dict[str, int],
        allowed_document_ids: tuple[str, ...] | None,
    ) -> dict[str, _ChunkMatch]:
        allowed_documents = (
            {str(item) for item in allowed_document_ids}
            if allowed_document_ids is not None
            else None
        )
        entity_names = {hit.entity_key: hit.entity_name for hit in entity_hits}
        relation_labels = {
            hit.relation_key: hit.label
            for hit in (*relation_hits, *neighbor_hits)
        }
        matches: dict[str, _ChunkMatch] = {}

        for evidence in evidence_hits:
            source_id = str(evidence.source_chunk_id).strip()
            document_id = str(evidence.document_id).strip()
            if not source_id or not document_id:
                continue
            if allowed_documents is not None and document_id not in allowed_documents:
                continue

            match = matches.setdefault(source_id, _ChunkMatch())
            if evidence.entity_key in entity_ranks:
                key = str(evidence.entity_key)
                rank = entity_ranks[key]
                match.add_score(
                    "entity",
                    key,
                    _ENTITY_WEIGHT / (_RRF_BASE + rank),
                )
                entity_name = evidence.entity_name or entity_names.get(key)
                if entity_name:
                    match.entity_names.add(entity_name)
                match.evidence_tokens.add(("entity", key, document_id))

            if evidence.relation_key:
                key = str(evidence.relation_key)
                if key in relation_ranks:
                    match.add_score(
                        "relation",
                        key,
                        _RELATION_WEIGHT / (_RRF_BASE + relation_ranks[key]),
                    )
                if key in neighbor_ranks:
                    match.add_score(
                        "neighbor",
                        key,
                        _NEIGHBOR_WEIGHT / (_RRF_BASE + neighbor_ranks[key]),
                    )
                if key in relation_ranks or key in neighbor_ranks:
                    relation_label = evidence.relation_label or relation_labels.get(key)
                    if relation_label:
                        match.relation_labels.add(relation_label)
                    match.evidence_tokens.add(("relation", key, document_id))

        return {
            source_id: match
            for source_id, match in matches.items()
            if match.channel_scores
        }

    @staticmethod
    def _scope_hydrated_chunks(
        request: GraphRetrievalRequest,
        chunks: list[DocumentChunk],
        matches: dict[str, _ChunkMatch],
    ) -> list[DocumentChunk]:
        allowed_documents = (
            {str(item) for item in request.allowed_document_ids}
            if request.allowed_document_ids is not None
            else None
        )
        allowed_files = {str(item) for item in request.file_names}
        scoped: list[DocumentChunk] = []
        seen_source_ids: set[str] = set()
        for chunk in chunks:
            metadata = chunk.metadata or {}
            source_id = str(metadata.get("doc_id") or "")
            document_id = str(metadata.get("document_id") or "")
            if source_id not in matches or source_id in seen_source_ids:
                continue
            if str(metadata.get("knowledge_id")) != request.runtime.knowledge_id:
                continue
            if metadata.get("status") != 1:
                continue
            if allowed_documents is not None and document_id not in allowed_documents:
                continue
            if allowed_files and str(metadata.get("file_name")) not in allowed_files:
                continue
            seen_source_ids.add(source_id)
            scoped.append(chunk)
        return scoped

    @staticmethod
    def _build_parent_matches(
        chunks: list[DocumentChunk],
        matches: dict[str, _ChunkMatch],
    ) -> dict[str, _ChunkMatch]:
        parent_matches: dict[str, _ChunkMatch] = {}
        for chunk in chunks:
            metadata = chunk.metadata or {}
            if metadata.get("chunk_type") != "child":
                continue
            parent_id = str(metadata.get("parent_id") or "")
            source_id = str(metadata.get("doc_id") or "")
            if not parent_id or source_id not in matches:
                continue
            parent_matches.setdefault(parent_id, _ChunkMatch()).merge(matches[source_id])
        return parent_matches

    @classmethod
    def _attach_resolved_matches(
        cls,
        request: GraphRetrievalRequest,
        chunks: list[DocumentChunk],
        source_matches: dict[str, _ChunkMatch],
        parent_matches: dict[str, _ChunkMatch],
    ) -> list[tuple[DocumentChunk, _ChunkMatch]]:
        allowed_documents = (
            {str(item) for item in request.allowed_document_ids}
            if request.allowed_document_ids is not None
            else None
        )
        allowed_files = {str(item) for item in request.file_names}
        attached: list[tuple[DocumentChunk, _ChunkMatch]] = []
        seen_ids: set[str] = set()
        for chunk in chunks:
            metadata = chunk.metadata or {}
            source_id = str(metadata.get("doc_id") or "")
            document_id = str(metadata.get("document_id") or "")
            match = source_matches.get(source_id) or parent_matches.get(source_id)
            if match is None or source_id in seen_ids:
                continue
            if str(metadata.get("knowledge_id")) != request.runtime.knowledge_id:
                continue
            if metadata.get("status") != 1:
                continue
            if allowed_documents is not None and document_id not in allowed_documents:
                continue
            if allowed_files and str(metadata.get("file_name")) not in allowed_files:
                continue
            seen_ids.add(source_id)
            attached.append((chunk, match))
        return attached

    @classmethod
    def _rank_and_limit(
        cls,
        chunks_with_matches: list[tuple[DocumentChunk, _ChunkMatch]],
        max_chunks_per_document: int,
    ) -> list[DocumentChunk]:
        if not chunks_with_matches:
            return []
        maximum_score = max(match.raw_score for _, match in chunks_with_matches)

        decorated: list[tuple[DocumentChunk, _ChunkMatch]] = []
        for chunk, match in chunks_with_matches:
            graph_score = (
                min(1.0, match.raw_score / maximum_score)
                if maximum_score > 0
                else 0.0
            )
            chunk.metadata.update(
                {
                    "retrieval_source": "graph",
                    "graph_score": graph_score,
                    "score": graph_score,
                    "matched_entities": sorted(match.entity_names),
                    "matched_relations": sorted(match.relation_labels),
                }
            )
            decorated.append((chunk, match))

        decorated.sort(key=cls._candidate_sort_key)
        per_document: dict[str, int] = {}
        selected: list[DocumentChunk] = []
        limit = max(1, int(max_chunks_per_document))
        for chunk, _ in decorated:
            document_id = str((chunk.metadata or {}).get("document_id") or "")
            count = per_document.get(document_id, 0)
            if count >= limit:
                continue
            per_document[document_id] = count + 1
            selected.append(chunk)
        return selected

    @staticmethod
    def _candidate_sort_key(
        item: tuple[DocumentChunk, _ChunkMatch],
    ) -> tuple[float, int, int, int, str]:
        chunk, match = item
        raw_sort_id = (chunk.metadata or {}).get("sort_id")
        try:
            sort_id = int(raw_sort_id)
        except (TypeError, ValueError):
            sort_id = 2**31 - 1
        source_id = str((chunk.metadata or {}).get("doc_id") or "")
        return (
            -match.raw_score,
            -match.match_count,
            -match.evidence_count,
            sort_id,
            source_id,
        )
