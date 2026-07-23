import asyncio
import logging
import time
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.core.rag.knowledge_graph.models import (
    EntityProjectionHit,
    GraphEvidenceHit,
    GraphIndexRuntime,
    GraphQueryPlan,
    GraphRetrievalResult,
    GraphRetrievalRequest,
    ProjectionEvidenceGroup,
    RelationProjectionHit,
    SourceChunkVectorHit,
)
from app.core.rag.knowledge_graph.prompts import (
    QUERY_ANALYSIS_SYSTEM_PROMPT,
    build_query_analysis_prompt,
)
from app.core.rag.knowledge_graph.query_plan_cache import GraphQueryPlanCache
from app.core.rag.knowledge_graph.structured_output import (
    unwrap_structured_result,
)
from app.core.rag.models.chunk import DocumentChunk
from app.core.rag.retrieval.elasticsearch_queries import normalize_vector


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _DirectSeed:
    branch: str
    seed_type: str
    key: str
    label: str
    score: float
    rank: int
    query_keywords: tuple[str, ...]


@dataclass(frozen=True)
class _EvidencePath:
    seed: _DirectSeed
    evidence_type: str
    evidence_key: str
    evidence_confidence: float
    source_chunk_id: str
    document_id: str
    expansion_type: str
    expansion_rank: int = 0
    via_relation: str | None = None
    endpoint_entity: str | None = None

    @property
    def identity(self) -> tuple[str, ...]:
        return (
            self.seed.branch,
            self.seed.seed_type,
            self.seed.key,
            self.evidence_type,
            self.evidence_key,
            self.source_chunk_id,
            self.expansion_type,
            str(self.expansion_rank),
            self.via_relation or "",
            self.endpoint_entity or "",
        )

    def as_metadata(self) -> dict[str, Any]:
        return {
            "branch": self.seed.branch,
            "query_keywords": list(self.seed.query_keywords),
            "seed_type": self.seed.seed_type,
            "seed_key": self.seed.key,
            "seed": self.seed.label,
            "seed_score": self.seed.score,
            "expansion_type": self.expansion_type,
            "expansion_rank": self.expansion_rank,
            "via_relation": self.via_relation,
            "endpoint_entity": self.endpoint_entity,
            "evidence_type": self.evidence_type,
            "evidence_key": self.evidence_key,
            "evidence_confidence": self.evidence_confidence,
            "source_chunk_id": self.source_chunk_id,
        }


@dataclass
class _ChunkMatch:
    paths: list[_EvidencePath] = field(default_factory=list)
    _path_tokens: set[tuple[str, ...]] = field(default_factory=set)

    def add(self, path: _EvidencePath) -> None:
        if path.identity in self._path_tokens:
            return
        self._path_tokens.add(path.identity)
        self.paths.append(path)

    def merge(self, other: "_ChunkMatch") -> None:
        for path in other.paths:
            self.add(path)

    @property
    def graph_score(self) -> float:
        return max((path.seed.score for path in self.paths), default=0.0)

    @property
    def support_count(self) -> int:
        return len(
            {
                (path.seed.seed_type, path.seed.key)
                for path in self.paths
            }
        )

    @property
    def evidence_confidence(self) -> float:
        return max(
            (path.evidence_confidence for path in self.paths),
            default=0.0,
        )

    def matched_entities(self) -> list[str]:
        return self._ordered_labels("entity")

    def matched_relations(self) -> list[str]:
        return self._ordered_labels("relation")

    def expanded_relations(self) -> list[str]:
        labels: dict[str, tuple[int, int]] = {}
        for index, path in enumerate(self.paths):
            if path.expansion_type != "relation" or not path.via_relation:
                continue
            current = labels.get(path.via_relation)
            position = (path.seed.rank, index)
            if current is None or position < current:
                labels[path.via_relation] = position
        return [label for label, _ in sorted(labels.items(), key=lambda item: item[1])]

    def path_metadata(self, limit: int) -> list[dict[str, Any]]:
        ordered = sorted(
            self.paths,
            key=lambda path: (
                path.seed.rank,
                0 if path.expansion_type == "direct" else 1,
                path.expansion_rank,
                -path.evidence_confidence,
                path.evidence_type,
                path.evidence_key,
                path.source_chunk_id,
            ),
        )
        return [path.as_metadata() for path in ordered[: max(1, limit)]]

    def _ordered_labels(self, seed_type: str) -> list[str]:
        labels: dict[str, tuple[int, int]] = {}
        for index, path in enumerate(self.paths):
            if path.seed.seed_type != seed_type or not path.seed.label:
                continue
            current = labels.get(path.seed.label)
            position = (path.seed.rank, index)
            if current is None or position < current:
                labels[path.seed.label] = position
        return [label for label, _ in sorted(labels.items(), key=lambda item: item[1])]


@dataclass
class _SeedCandidates:
    seed: _DirectSeed
    source_chunk_ids: list[str] = field(default_factory=list)

    def add(self, source_chunk_id: str) -> None:
        if source_chunk_id and source_chunk_id not in self.source_chunk_ids:
            self.source_chunk_ids.append(source_chunk_id)


@dataclass
class _SourceGroup:
    key: str
    source_chunk_ids: list[str]


@dataclass
class _SourceSelection:
    source_chunk_ids: list[str] = field(default_factory=list)
    vector_scores: dict[str, float] = field(default_factory=dict)


class KnowledgeGraphRetrievalPipeline:
    def __init__(
        self,
        store: Any,
        llm: Any,
        embedding: Any,
        parent_resolver: Any,
        query_plan_cache: GraphQueryPlanCache | None = None,
    ) -> None:
        self._store = store
        self._llm = llm
        self._embedding = embedding
        self._parent_resolver = parent_resolver
        self._query_plan_cache = query_plan_cache or GraphQueryPlanCache(
            ttl_seconds=settings.KNOWLEDGE_GRAPH_QUERY_PLAN_CACHE_TTL_SECONDS,
        )

    async def retrieve(
        self,
        request: GraphRetrievalRequest,
    ) -> list[DocumentChunk]:
        result = await self.retrieve_with_graph_data(request)
        return result.chunks

    async def retrieve_with_graph_data(
        self,
        request: GraphRetrievalRequest,
    ) -> GraphRetrievalResult:
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
    ) -> GraphRetrievalResult:
        stage = "validate_filters"
        timings: dict[str, int] = {}
        counts = {
            "raw_entity_seeds": 0,
            "entity_seeds": 0,
            "raw_relation_seeds": 0,
            "relation_seeds": 0,
            "neighbor_relations": 0,
            "endpoint_entities": 0,
            "evidence_groups": 0,
            "evidence_hits": 0,
            "source_chunks": 0,
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
                    timings,
                    reason="no_allowed_documents",
                )
                return GraphRetrievalResult()

            stage = "query_plan"
            stage_started = time.perf_counter()
            plan = await self._analyze_query(request.query, request.runtime)
            timings["query_plan_ms"] = self._elapsed_ms(stage_started)
            if not self._has_query_terms(plan):
                self._log_outcome(
                    "retrieval_empty",
                    request,
                    started_at,
                    counts,
                    timings,
                    reason="no_query_keywords",
                )
                return GraphRetrievalResult()

            stage = "query_embedding"
            stage_started = time.perf_counter()
            branch_vectors = await self._embed_query_plan(
                request.query,
                plan,
            )
            timings["embedding_ms"] = self._elapsed_ms(stage_started)

            stage = "projection_search"
            stage_started = time.perf_counter()
            raw_entity_hits, raw_relation_hits = await self._search_direct_seeds(
                request,
                branch_vectors,
            )
            counts["raw_entity_seeds"] = len(raw_entity_hits)
            counts["raw_relation_seeds"] = len(raw_relation_hits)
            entity_hits = self._deduplicate_entities(
                hit
                for hit in raw_entity_hits
                if hit.score >= request.entity_similarity_threshold
            )
            relation_hits = self._deduplicate_relations(
                hit
                for hit in raw_relation_hits
                if hit.score >= request.relation_similarity_threshold
            )
            counts["entity_seeds"] = len(entity_hits)
            counts["relation_seeds"] = len(relation_hits)
            timings["projection_ms"] = self._elapsed_ms(stage_started)
            self._log_seed_outcome(
                request,
                "local",
                raw_entity_hits,
                entity_hits,
                request.entity_similarity_threshold,
            )
            self._log_seed_outcome(
                request,
                "global",
                raw_relation_hits,
                relation_hits,
                request.relation_similarity_threshold,
            )
            if not entity_hits and not relation_hits:
                self._log_outcome(
                    "retrieval_empty",
                    request,
                    started_at,
                    counts,
                    timings,
                    reason="no_valid_seeds",
                )
                return GraphRetrievalResult()

            stage = "graph_expansion"
            stage_started = time.perf_counter()
            neighbor_relations, endpoint_entities = await self._expand_graph(
                request,
                entity_hits,
                relation_hits,
            )
            counts["neighbor_relations"] = len(neighbor_relations)
            counts["endpoint_entities"] = len(endpoint_entities)
            timings["expansion_ms"] = self._elapsed_ms(stage_started)
            final_entities = self._round_robin_entities(
                entity_hits,
                endpoint_entities,
            )
            final_relations = self._round_robin_relations(
                neighbor_relations,
                relation_hits,
            )

            stage = "evidence_search"
            stage_started = time.perf_counter()
            entity_keys = tuple(
                hit.entity_key for hit in final_entities
            )
            relation_keys = tuple(
                hit.relation_key for hit in final_relations
            )
            evidence_groups = await self._store.load_evidence_groups(
                request.runtime,
                entity_keys,
                relation_keys,
                max(request.max_candidates, request.related_chunk_number),
                allowed_document_ids=request.allowed_document_ids,
            )
            counts["evidence_groups"] = len(evidence_groups)
            counts["evidence_hits"] = sum(
                len(group.evidence) for group in evidence_groups
            )
            timings["evidence_ms"] = self._elapsed_ms(stage_started)
            if not evidence_groups:
                self._log_outcome(
                    "retrieval_empty",
                    request,
                    started_at,
                    counts,
                    timings,
                    reason="no_evidence",
                )
                return GraphRetrievalResult()

            stage = "candidate_ranking"
            stage_started = time.perf_counter()
            matches: dict[str, _ChunkMatch] = {}
            group_map = {
                (group.projection_type, group.projection_key): group
                for group in evidence_groups
            }
            self._build_local_candidates(
                plan,
                entity_hits,
                neighbor_relations,
                group_map,
                matches,
                request.allowed_document_ids,
            )
            self._build_global_candidates(
                plan,
                relation_hits,
                endpoint_entities,
                group_map,
                matches,
                request.allowed_document_ids,
            )
            entity_groups = self._build_source_groups(
                tuple(hit.entity_key for hit in final_entities),
                "entity",
                group_map,
                matches,
            )
            entity_selection = await self._select_source_chunks(
                request,
                entity_groups,
                branch_vectors.get("query"),
                source_type="entity",
            )
            relation_groups = self._build_source_groups(
                tuple(hit.relation_key for hit in final_relations),
                "relation",
                group_map,
                matches,
                excluded_source_ids=set(entity_selection.source_chunk_ids),
            )
            relation_selection = await self._select_source_chunks(
                request,
                relation_groups,
                branch_vectors.get("query"),
                source_type="relation",
            )
            source_vector_scores = {
                **entity_selection.vector_scores,
                **relation_selection.vector_scores,
            }
            candidate_ids = self._merge_source_types(
                entity_selection.source_chunk_ids,
                relation_selection.source_chunk_ids,
                request.max_candidates,
            )
            candidate_id_set = set(candidate_ids)
            source_vector_scores = {
                source_id: score
                for source_id, score in source_vector_scores.items()
                if source_id in candidate_id_set
            }
            matches = {
                source_id: matches[source_id]
                for source_id in candidate_ids
                if source_id in matches
            }
            counts["source_chunks"] = len(candidate_ids)
            timings["ranking_ms"] = self._elapsed_ms(stage_started)
            if not candidate_ids:
                self._log_outcome(
                    "retrieval_empty",
                    request,
                    started_at,
                    counts,
                    timings,
                    reason="no_source_chunks",
                )
                return GraphRetrievalResult()

            stage = "chunk_hydration"
            stage_started = time.perf_counter()
            chunks = await self._store.hydrate_source_chunks(
                chunk_index_name=request.runtime.chunk_index_name,
                knowledge_id=request.runtime.knowledge_id,
                source_chunk_ids=tuple(candidate_ids),
                allowed_document_ids=request.allowed_document_ids,
                file_names=request.file_names,
            )
            counts["hydrated_chunks"] = len(chunks)
            scoped_chunks = self._scope_hydrated_chunks(
                request,
                chunks,
                matches,
                candidate_ids,
            )
            counts["scoped_chunks"] = len(scoped_chunks)
            timings["hydration_ms"] = self._elapsed_ms(stage_started)
            if not scoped_chunks:
                self._log_outcome(
                    "retrieval_empty",
                    request,
                    started_at,
                    counts,
                    timings,
                    reason="no_scoped_chunks",
                )
                return GraphRetrievalResult()

            stage = "parent_resolution"
            stage_started = time.perf_counter()
            candidate_order = {
                source_id: index
                for index, source_id in enumerate(candidate_ids)
            }
            parent_matches, parent_order, parent_vector_scores = (
                self._build_parent_matches(
                    scoped_chunks,
                    matches,
                    candidate_order,
                    source_vector_scores,
                )
            )
            for chunk in scoped_chunks:
                source_id = str((chunk.metadata or {}).get("doc_id") or "")
                match = matches.get(source_id)
                if match is not None:
                    vector_score = source_vector_scores.get(source_id)
                    chunk.metadata["graph_score"] = match.graph_score
                    if vector_score is not None:
                        chunk.metadata["chunk_vector_score"] = vector_score
                    chunk.metadata["score"] = self._metadata_score(
                        match,
                        vector_score,
                    )
            resolved = await self._parent_resolver(
                scoped_chunks,
                request.runtime.chunk_index_name,
            )
            attached = self._attach_resolved_matches(
                request,
                resolved,
                matches,
                parent_matches,
                candidate_order,
                parent_order,
                source_vector_scores,
                parent_vector_scores,
            )
            selected_attached = self._select_attached_candidates(
                attached,
                request.max_candidates,
            )
            result = self._finalize_candidates(
                selected_attached,
                request.max_paths_per_chunk,
                request.max_candidates,
            )
            counts["result_count"] = len(result)
            timings["parent_ms"] = self._elapsed_ms(stage_started)
            self._log_outcome(
                "retrieval_done" if result else "retrieval_empty",
                request,
                started_at,
                counts,
                timings,
                reason=None if result else "no_resolved_chunks",
            )
            return GraphRetrievalResult(
                chunks=result,
                entities=self._format_result_entities(
                    selected_attached,
                    final_entities,
                ),
                relationships=self._format_result_relationships(
                    selected_attached,
                    final_relations,
                ),
            )
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

    async def _embed_query_plan(
        self,
        query: str,
        plan: GraphQueryPlan,
    ) -> dict[str, list[float]]:
        texts: list[str] = []
        branches: list[str] = []
        if self._has_query_terms(plan) and query.strip():
            branches.append("query")
            texts.append(query.strip())
        if plan.low_level_keywords:
            branches.append("local")
            texts.append(" ".join(plan.low_level_keywords))
        if plan.high_level_keywords:
            branches.append("global")
            texts.append(" ".join(plan.high_level_keywords))
        if not texts:
            return {}
        query_embedder = getattr(self._embedding, "aembed_query", None)
        if callable(query_embedder):
            vectors = list(
                await asyncio.gather(
                    *(query_embedder(text) for text in texts)
                )
            )
        else:
            vectors = await self._embedding.aembed_documents(texts)
        if len(vectors) != len(texts):
            raise ValueError("query embedding count does not match graph branches")
        return {
            branch: normalize_vector(vector)
            for branch, vector in zip(branches, vectors, strict=True)
        }

    async def _search_direct_seeds(
        self,
        request: GraphRetrievalRequest,
        branch_vectors: dict[str, list[float]],
    ) -> tuple[list[EntityProjectionHit], list[RelationProjectionHit]]:
        entity_task = (
            asyncio.create_task(
                self._store.search_entity_projections(
                    request.runtime,
                    branch_vectors["local"],
                    request.entity_top_n,
                    request.entity_similarity_threshold,
                )
            )
            if "local" in branch_vectors
            else None
        )
        relation_task = (
            asyncio.create_task(
                self._store.search_relation_projections(
                    request.runtime,
                    branch_vectors["global"],
                    request.relation_top_n,
                    request.relation_similarity_threshold,
                )
            )
            if "global" in branch_vectors
            else None
        )
        try:
            entity_hits = await entity_task if entity_task is not None else []
            relation_hits = await relation_task if relation_task is not None else []
        except BaseException:
            for task in (entity_task, relation_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (entity_task, relation_task) if task is not None),
                return_exceptions=True,
            )
            raise
        return list(entity_hits), list(relation_hits)

    async def _expand_graph(
        self,
        request: GraphRetrievalRequest,
        entity_hits: list[EntityProjectionHit],
        relation_hits: list[RelationProjectionHit],
    ) -> tuple[list[RelationProjectionHit], list[EntityProjectionHit]]:
        neighbor_task = (
            asyncio.create_task(
                self._store.load_neighbor_relations(
                    request.runtime,
                    tuple(hit.entity_key for hit in entity_hits),
                    request.neighbor_top_n,
                )
            )
            if entity_hits
            else None
        )
        endpoint_keys = tuple(
            dict.fromkeys(
                key
                for relation in relation_hits
                for key in (relation.from_entity_key, relation.to_entity_key)
                if key
            )
        )
        endpoint_task = (
            asyncio.create_task(
                self._store.load_entity_projections(
                    request.runtime,
                    endpoint_keys,
                )
            )
            if endpoint_keys
            else None
        )
        try:
            neighbors = await neighbor_task if neighbor_task is not None else []
            endpoints = await endpoint_task if endpoint_task is not None else []
        except BaseException:
            for task in (neighbor_task, endpoint_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (neighbor_task, endpoint_task) if task is not None),
                return_exceptions=True,
            )
            raise
        return (
            self._deduplicate_neighbor_relations(neighbors),
            self._order_endpoint_entities(endpoints, endpoint_keys),
        )

    @classmethod
    def _build_local_candidates(
        cls,
        plan: GraphQueryPlan,
        entity_hits: Sequence[EntityProjectionHit],
        neighbor_relations: Sequence[RelationProjectionHit],
        group_map: dict[tuple[str, str], ProjectionEvidenceGroup],
        matches: dict[str, _ChunkMatch],
        allowed_document_ids: tuple[str, ...] | None,
    ) -> list[_SeedCandidates]:
        candidates: list[_SeedCandidates] = []
        for rank, entity in enumerate(entity_hits, start=1):
            seed = _DirectSeed(
                branch="local",
                seed_type="entity",
                key=entity.entity_key,
                label=entity.entity_name,
                score=entity.score,
                rank=rank,
                query_keywords=tuple(plan.low_level_keywords),
            )
            seed_candidates = _SeedCandidates(seed=seed)
            cls._add_group_paths(
                seed_candidates,
                group_map.get(("entity", entity.entity_key)),
                seed,
                matches,
                allowed_document_ids,
                expansion_type="direct",
            )
            for expansion_rank, relation in enumerate(
                neighbor_relations,
                start=1,
            ):
                if entity.entity_key not in {
                    relation.from_entity_key,
                    relation.to_entity_key,
                }:
                    continue
                cls._add_group_paths(
                    seed_candidates,
                    group_map.get(("relation", relation.relation_key)),
                    seed,
                    matches,
                    allowed_document_ids,
                    expansion_type="relation",
                    expansion_rank=expansion_rank,
                    via_relation=relation.label,
                )
            if seed_candidates.source_chunk_ids:
                candidates.append(seed_candidates)
        return candidates

    @classmethod
    def _build_global_candidates(
        cls,
        plan: GraphQueryPlan,
        relation_hits: Sequence[RelationProjectionHit],
        endpoint_entities: Sequence[EntityProjectionHit],
        group_map: dict[tuple[str, str], ProjectionEvidenceGroup],
        matches: dict[str, _ChunkMatch],
        allowed_document_ids: tuple[str, ...] | None,
    ) -> list[_SeedCandidates]:
        endpoint_map = {hit.entity_key: hit for hit in endpoint_entities}
        candidates: list[_SeedCandidates] = []
        for rank, relation in enumerate(relation_hits, start=1):
            seed = _DirectSeed(
                branch="global",
                seed_type="relation",
                key=relation.relation_key,
                label=relation.label,
                score=relation.score,
                rank=rank,
                query_keywords=tuple(plan.high_level_keywords),
            )
            seed_candidates = _SeedCandidates(seed=seed)
            cls._add_group_paths(
                seed_candidates,
                group_map.get(("relation", relation.relation_key)),
                seed,
                matches,
                allowed_document_ids,
                expansion_type="direct",
            )
            for expansion_rank, endpoint_key in enumerate(
                (
                    relation.from_entity_key,
                    relation.to_entity_key,
                ),
                start=1,
            ):
                endpoint = endpoint_map.get(endpoint_key)
                if endpoint is None:
                    continue
                cls._add_group_paths(
                    seed_candidates,
                    group_map.get(("entity", endpoint.entity_key)),
                    seed,
                    matches,
                    allowed_document_ids,
                    expansion_type="endpoint_entity",
                    expansion_rank=expansion_rank,
                    via_relation=relation.label,
                    endpoint_entity=endpoint.entity_name,
                )
            if seed_candidates.source_chunk_ids:
                candidates.append(seed_candidates)
        return candidates

    @staticmethod
    def _add_group_paths(
        seed_candidates: _SeedCandidates,
        group: ProjectionEvidenceGroup | None,
        seed: _DirectSeed,
        matches: dict[str, _ChunkMatch],
        allowed_document_ids: tuple[str, ...] | None,
        *,
        expansion_type: str,
        expansion_rank: int = 0,
        via_relation: str | None = None,
        endpoint_entity: str | None = None,
    ) -> None:
        if group is None:
            return
        allowed_documents = (
            {str(value) for value in allowed_document_ids}
            if allowed_document_ids is not None
            else None
        )
        for evidence in group.evidence:
            source_id = str(evidence.source_chunk_id).strip()
            document_id = str(evidence.document_id).strip()
            if not source_id or not document_id:
                continue
            if allowed_documents is not None and document_id not in allowed_documents:
                continue
            path = _EvidencePath(
                seed=seed,
                evidence_type=group.projection_type,
                evidence_key=group.projection_key,
                evidence_confidence=max(0.0, min(1.0, float(evidence.score))),
                source_chunk_id=source_id,
                document_id=document_id,
                expansion_type=expansion_type,
                expansion_rank=expansion_rank,
                via_relation=via_relation,
                endpoint_entity=endpoint_entity,
            )
            matches.setdefault(source_id, _ChunkMatch()).add(path)
            seed_candidates.add(source_id)

    @staticmethod
    def _build_source_groups(
        ordered_keys: Sequence[str],
        projection_type: str,
        group_map: dict[tuple[str, str], ProjectionEvidenceGroup],
        matches: dict[str, _ChunkMatch],
        excluded_source_ids: set[str] | None = None,
    ) -> list[_SourceGroup]:
        excluded = excluded_source_ids or set()
        occurrence_count: dict[str, int] = {}
        seen: set[str] = set()
        groups: list[_SourceGroup] = []

        for key in ordered_keys:
            group = group_map.get((projection_type, key))
            if group is None:
                continue
            source_ids: list[str] = []
            for evidence in group.evidence:
                source_id = str(evidence.source_chunk_id).strip()
                if (
                    not source_id
                    or source_id in excluded
                    or source_id not in matches
                ):
                    continue
                occurrence_count[source_id] = (
                    occurrence_count.get(source_id, 0) + 1
                )
                if source_id in seen:
                    continue
                seen.add(source_id)
                source_ids.append(source_id)
            if source_ids:
                groups.append(
                    _SourceGroup(
                        key=key,
                        source_chunk_ids=source_ids,
                    )
                )

        for group in groups:
            group.source_chunk_ids.sort(
                key=lambda source_id: -occurrence_count.get(source_id, 0)
            )
        return groups

    async def _select_source_chunks(
        self,
        request: GraphRetrievalRequest,
        groups: Sequence[_SourceGroup],
        query_vector: Sequence[float] | None,
        *,
        source_type: str,
    ) -> _SourceSelection:
        if not groups:
            return _SourceSelection()
        candidates = [
            source_id
            for group in groups
            for source_id in group.source_chunk_ids
        ]
        vector_limit = int(
            request.related_chunk_number * len(groups) / 2
        )
        ranker = getattr(self._store, "rank_source_chunks", None)
        if query_vector and vector_limit > 0 and callable(ranker):
            try:
                ranked = await ranker(
                    request.runtime,
                    tuple(candidates),
                    query_vector,
                    vector_limit,
                    allowed_document_ids=request.allowed_document_ids,
                    file_names=request.file_names,
                )
            except Exception as exc:
                logger.warning(
                    "[EvidenceGraph] source_vector_fallback"
                    " kb_id=%s source_type=%s error_type=%s",
                    request.runtime.knowledge_id,
                    source_type,
                    type(exc).__name__,
                )
                ranked = []
            allowed = set(candidates)
            selection = self._ranked_source_selection(ranked, allowed)
            if selection.source_chunk_ids:
                self._log_source_selection(
                    request,
                    source_type,
                    "vector",
                    len(groups),
                    len(candidates),
                    len(selection.source_chunk_ids),
                )
                return selection
        selected = self._weighted_source_poll(
            groups,
            request.related_chunk_number,
        )
        self._log_source_selection(
            request,
            source_type,
            "weight",
            len(groups),
            len(candidates),
            len(selected),
        )
        return _SourceSelection(source_chunk_ids=selected)

    @classmethod
    def _ranked_source_selection(
        cls,
        ranked_hits: Sequence[Any],
        allowed: set[str],
    ) -> _SourceSelection:
        selected: list[str] = []
        scores: dict[str, float] = {}
        seen: set[str] = set()
        for hit in ranked_hits:
            source_id, score = cls._extract_source_vector_hit(hit)
            if not source_id or source_id not in allowed or source_id in seen:
                continue
            seen.add(source_id)
            selected.append(source_id)
            if score is not None:
                scores[source_id] = score
        return _SourceSelection(
            source_chunk_ids=selected,
            vector_scores=scores,
        )

    @staticmethod
    def _extract_source_vector_hit(hit: Any) -> tuple[str, float | None]:
        if isinstance(hit, SourceChunkVectorHit):
            return hit.source_chunk_id, float(hit.score)
        if isinstance(hit, Mapping):
            source_id = str(
                hit.get("source_chunk_id")
                or hit.get("source_id")
                or hit.get("id")
                or ""
            )
            score = hit.get("score")
        elif isinstance(hit, tuple) and len(hit) >= 2:
            source_id = str(hit[0] or "")
            score = hit[1]
        else:
            return str(hit or ""), None
        try:
            return source_id, float(score)
        except (TypeError, ValueError):
            return source_id, None

    @staticmethod
    def _weighted_source_poll(
        groups: Sequence[_SourceGroup],
        max_related_chunks: int,
    ) -> list[str]:
        if not groups:
            return []
        maximum = max(1, int(max_related_chunks))
        if len(groups) == 1:
            return list(groups[0].source_chunk_ids[:maximum])

        group_count = len(groups)
        expected_counts = [
            round(
                maximum
                - (index / (group_count - 1)) * (maximum - 1)
            )
            for index in range(group_count)
        ]
        selected: list[str] = []
        used_counts: list[int] = []
        unfilled_quota = 0
        for group, expected in zip(
            groups,
            expected_counts,
            strict=True,
        ):
            used = min(expected, len(group.source_chunk_ids))
            selected.extend(group.source_chunk_ids[:used])
            used_counts.append(used)
            unfilled_quota += expected - used

        for _ in range(unfilled_quota):
            for index, group in enumerate(groups):
                if used_counts[index] >= len(group.source_chunk_ids):
                    continue
                selected.append(
                    group.source_chunk_ids[used_counts[index]]
                )
                used_counts[index] += 1
                break
            else:
                break
        return selected

    @staticmethod
    def _merge_source_types(
        entity_sequence: Sequence[str],
        relation_sequence: Sequence[str],
        limit: int,
    ) -> list[str]:
        entities = deque(entity_sequence)
        relations = deque(relation_sequence)
        selected: list[str] = []
        seen: set[str] = set()
        maximum = max(1, int(limit))
        while (entities or relations) and len(selected) < maximum:
            for queue in (entities, relations):
                while queue:
                    source_id = queue.popleft()
                    if source_id in seen:
                        continue
                    seen.add(source_id)
                    selected.append(source_id)
                    break
                if len(selected) >= maximum:
                    break
        return selected

    @staticmethod
    def _log_source_selection(
        request: GraphRetrievalRequest,
        source_type: str,
        method: str,
        group_count: int,
        candidate_count: int,
        selected_count: int,
    ) -> None:
        logger.info(
            "[EvidenceGraph] source_selection"
            " kb_id=%s source_type=%s method=%s"
            " groups=%d candidates=%d selected=%d",
            request.runtime.knowledge_id,
            source_type,
            method,
            group_count,
            candidate_count,
            selected_count,
        )

    @staticmethod
    def _scope_hydrated_chunks(
        request: GraphRetrievalRequest,
        chunks: Sequence[DocumentChunk],
        matches: dict[str, _ChunkMatch],
        candidate_ids: Sequence[str],
    ) -> list[DocumentChunk]:
        allowed_documents = (
            {str(item) for item in request.allowed_document_ids}
            if request.allowed_document_ids is not None
            else None
        )
        allowed_files = {str(item) for item in request.file_names}
        by_source_id: dict[str, DocumentChunk] = {}
        for chunk in chunks:
            metadata = chunk.metadata or {}
            source_id = str(metadata.get("doc_id") or "")
            document_id = str(metadata.get("document_id") or "")
            if not source_id or source_id not in matches:
                continue
            if str(metadata.get("knowledge_id")) != request.runtime.knowledge_id:
                continue
            if metadata.get("status") != 1:
                continue
            if allowed_documents is not None and document_id not in allowed_documents:
                continue
            if allowed_files and str(metadata.get("file_name")) not in allowed_files:
                continue
            by_source_id.setdefault(source_id, chunk)
        return [
            by_source_id[source_id]
            for source_id in candidate_ids
            if source_id in by_source_id
        ]

    @staticmethod
    def _build_parent_matches(
        chunks: Sequence[DocumentChunk],
        matches: dict[str, _ChunkMatch],
        candidate_order: dict[str, int],
        source_vector_scores: dict[str, float],
    ) -> tuple[dict[str, _ChunkMatch], dict[str, int], dict[str, float]]:
        parent_matches: dict[str, _ChunkMatch] = {}
        parent_order: dict[str, int] = {}
        parent_vector_scores: dict[str, float] = {}
        for chunk in chunks:
            metadata = chunk.metadata or {}
            if metadata.get("chunk_type") != "child":
                continue
            parent_id = str(metadata.get("parent_id") or "")
            source_id = str(metadata.get("doc_id") or "")
            match = matches.get(source_id)
            if not parent_id or match is None:
                continue
            parent_matches.setdefault(parent_id, _ChunkMatch()).merge(match)
            parent_order[parent_id] = min(
                parent_order.get(parent_id, 2**31 - 1),
                candidate_order.get(source_id, 2**31 - 1),
            )
            vector_score = source_vector_scores.get(source_id)
            if vector_score is not None:
                existing_score = parent_vector_scores.get(parent_id)
                if existing_score is None or vector_score > existing_score:
                    parent_vector_scores[parent_id] = vector_score
        return parent_matches, parent_order, parent_vector_scores

    @classmethod
    def _attach_resolved_matches(
        cls,
        request: GraphRetrievalRequest,
        chunks: Sequence[DocumentChunk],
        source_matches: dict[str, _ChunkMatch],
        parent_matches: dict[str, _ChunkMatch],
        candidate_order: dict[str, int],
        parent_order: dict[str, int],
        source_vector_scores: dict[str, float],
        parent_vector_scores: dict[str, float],
    ) -> list[tuple[DocumentChunk, _ChunkMatch, int, float | None]]:
        allowed_documents = (
            {str(item) for item in request.allowed_document_ids}
            if request.allowed_document_ids is not None
            else None
        )
        allowed_files = {str(item) for item in request.file_names}
        attached: list[tuple[DocumentChunk, _ChunkMatch, int, float | None]] = []
        for chunk in chunks:
            metadata = chunk.metadata or {}
            source_id = str(metadata.get("doc_id") or "")
            document_id = str(metadata.get("document_id") or "")
            direct_match = source_matches.get(source_id)
            parent_match = parent_matches.get(source_id)
            if direct_match is None and parent_match is None:
                continue
            if str(metadata.get("knowledge_id")) != request.runtime.knowledge_id:
                continue
            if metadata.get("status") != 1:
                continue
            if allowed_documents is not None and document_id not in allowed_documents:
                continue
            if allowed_files and str(metadata.get("file_name")) not in allowed_files:
                continue
            match = _ChunkMatch()
            if direct_match is not None:
                match.merge(direct_match)
            if parent_match is not None:
                match.merge(parent_match)
            order = min(
                candidate_order.get(source_id, 2**31 - 1),
                parent_order.get(source_id, 2**31 - 1),
            )
            vector_scores = [
                score
                for score in (
                    source_vector_scores.get(source_id),
                    parent_vector_scores.get(source_id),
                )
                if score is not None
            ]
            vector_score = max(vector_scores) if vector_scores else None
            attached.append((chunk, match, order, vector_score))
        attached.sort(
            key=lambda item: (
                item[2],
                cls._chunk_sort_id(item[0]),
                str((item[0].metadata or {}).get("doc_id") or ""),
            )
        )
        return attached

    @staticmethod
    def _select_attached_candidates(
        attached: Sequence[tuple[DocumentChunk, _ChunkMatch, int, float | None]],
        max_candidates: int,
    ) -> list[tuple[DocumentChunk, _ChunkMatch, int, float | None]]:
        selected: list[tuple[DocumentChunk, _ChunkMatch, int, float | None]] = []
        seen_source_ids: set[str] = set()
        for item in attached:
            chunk = item[0]
            source_id = str((chunk.metadata or {}).get("doc_id") or "")
            if not source_id or source_id in seen_source_ids:
                continue
            seen_source_ids.add(source_id)
            selected.append(item)
            if len(selected) >= max(1, int(max_candidates)):
                break
        return selected

    @classmethod
    def _format_result_entities(
        cls,
        selected: Sequence[tuple[DocumentChunk, _ChunkMatch, int, float | None]],
        entities: Sequence[EntityProjectionHit],
    ) -> list[dict[str, Any]]:
        entity_map = {entity.entity_key: entity for entity in entities}
        source_ids_by_key = cls._projection_source_ids(selected, "entity")
        result: list[dict[str, Any]] = []
        for entity_key, source_ids in source_ids_by_key.items():
            entity = entity_map.get(entity_key)
            if entity is None:
                continue
            result.append(
                {
                    "entity_key": entity.entity_key,
                    "entity_name": entity.entity_name,
                    "entity_type": entity.entity_type,
                    "description": entity.description,
                    "aliases": list(entity.aliases),
                    "score": entity.score,
                    "degree": entity.degree,
                    "evidence_count": entity.evidence_count,
                    "document_count": entity.document_count,
                    "source_chunk_ids": source_ids,
                }
            )
        return result

    @classmethod
    def _format_result_relationships(
        cls,
        selected: Sequence[tuple[DocumentChunk, _ChunkMatch, int, float | None]],
        relationships: Sequence[RelationProjectionHit],
    ) -> list[dict[str, Any]]:
        relationship_map = {
            relationship.relation_key: relationship
            for relationship in relationships
        }
        source_ids_by_key = cls._projection_source_ids(selected, "relation")
        result: list[dict[str, Any]] = []
        for relation_key, source_ids in source_ids_by_key.items():
            relationship = relationship_map.get(relation_key)
            if relationship is None:
                continue
            result.append(
                {
                    "relation_key": relationship.relation_key,
                    "src_id": relationship.from_entity_key,
                    "tgt_id": relationship.to_entity_key,
                    "from_entity_key": relationship.from_entity_key,
                    "from_entity_name": relationship.from_entity_name,
                    "to_entity_key": relationship.to_entity_key,
                    "to_entity_name": relationship.to_entity_name,
                    "predicate": relationship.predicate,
                    "label": relationship.label,
                    "description": relationship.description,
                    "keywords": list(relationship.keywords),
                    "directed": relationship.directed,
                    "score": relationship.score,
                    "weight": relationship.evidence_count,
                    "evidence_count": relationship.evidence_count,
                    "document_count": relationship.document_count,
                    "endpoint_degree": relationship.endpoint_degree,
                    "source_chunk_ids": source_ids,
                }
            )
        return result

    @staticmethod
    def _projection_source_ids(
        selected: Sequence[tuple[DocumentChunk, _ChunkMatch, int, float | None]],
        projection_type: str,
    ) -> dict[str, list[str]]:
        source_ids_by_key: dict[str, list[str]] = {}
        for _, match, _, _ in selected:
            for path in match.paths:
                if path.evidence_type != projection_type:
                    continue
                key = path.evidence_key
                source_ids = source_ids_by_key.setdefault(key, [])
                if path.source_chunk_id not in source_ids:
                    source_ids.append(path.source_chunk_id)
        return source_ids_by_key

    @staticmethod
    def _finalize_candidates(
        attached: Sequence[tuple[DocumentChunk, _ChunkMatch, int, float | None]],
        max_paths_per_chunk: int,
        max_candidates: int,
    ) -> list[DocumentChunk]:
        selected: list[DocumentChunk] = []
        seen_source_ids: set[str] = set()
        for chunk, match, _, vector_score in attached:
            source_id = str((chunk.metadata or {}).get("doc_id") or "")
            if not source_id or source_id in seen_source_ids:
                continue
            seen_source_ids.add(source_id)
            graph_rank = len(selected) + 1
            chunk_score = KnowledgeGraphRetrievalPipeline._metadata_score(
                match,
                vector_score,
            )
            metadata = {
                "retrieval_source": "graph",
                "graph_rank": graph_rank,
                "graph_score": match.graph_score,
                "score": chunk_score,
                "matched_entities": match.matched_entities(),
                "matched_relations": match.matched_relations(),
                "expanded_relations": match.expanded_relations(),
                "support_count": match.support_count,
                "evidence_confidence": match.evidence_confidence,
                "match_paths": match.path_metadata(max_paths_per_chunk),
            }
            if vector_score is not None:
                metadata["chunk_vector_score"] = vector_score
            chunk.metadata.update(
                metadata
            )
            selected.append(chunk)
            if len(selected) >= max(1, int(max_candidates)):
                break
        return selected

    @staticmethod
    def _metadata_score(
        match: _ChunkMatch,
        vector_score: float | None,
    ) -> float:
        return vector_score if vector_score is not None else match.graph_score

    async def _analyze_query(
        self,
        query: str,
        runtime: GraphIndexRuntime,
    ) -> GraphQueryPlan:
        cache_key = self._query_plan_cache.build_key(runtime, query)
        cached_plan = await self._query_plan_cache.get(cache_key)
        normalized_cached = self._normalize_query_plan(query, cached_plan)
        if (
            normalized_cached is not None
            and self._has_query_terms(normalized_cached)
        ):
            self._log_query_plan(
                runtime.knowledge_id,
                normalized_cached,
                status="cache_hit",
                fallback=False,
            )
            return normalized_cached
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
                GraphQueryPlan,
                include_raw=True,
            )
            raw_result = unwrap_structured_result(
                raw_result,
                GraphQueryPlan,
            )
            raw_plan = GraphQueryPlan.model_validate(raw_result)
            normalized_plan = self._normalize_query_plan(query, raw_plan)
            if (
                normalized_plan is None
                or not self._has_query_terms(normalized_plan)
            ):
                fallback_plan = self._fallback_query_plan(query)
                self._log_query_plan(
                    runtime.knowledge_id,
                    fallback_plan,
                    status="empty",
                    fallback=True,
                )
                return fallback_plan
            await self._query_plan_cache.set(cache_key, normalized_plan)
            self._log_query_plan(
                runtime.knowledge_id,
                normalized_plan,
                status="generated",
                fallback=False,
            )
            return normalized_plan
        except Exception as exc:
            logger.warning(
                "[EvidenceGraph] query_analysis_fallback"
                " kb_id=%s error_type=%s",
                runtime.knowledge_id,
                type(exc).__name__,
            )
            fallback_plan = self._fallback_query_plan(query)
            self._log_query_plan(
                runtime.knowledge_id,
                fallback_plan,
                status="error",
                fallback=True,
            )
            return fallback_plan

    @staticmethod
    def _log_query_plan(
        knowledge_id: str,
        plan: GraphQueryPlan,
        *,
        status: str,
        fallback: bool,
    ) -> None:
        logger.info(
            "[EvidenceGraph] query_plan"
            " kb_id=%s status=%s fallback=%s low_count=%d high_count=%d",
            knowledge_id,
            status,
            str(fallback).lower(),
            len(plan.low_level_keywords),
            len(plan.high_level_keywords),
        )

    @staticmethod
    def _log_seed_outcome(
        request: GraphRetrievalRequest,
        branch: str,
        raw_hits: Sequence[Any],
        passed_hits: Sequence[Any],
        threshold: float,
    ) -> None:
        scores = [float(hit.score) for hit in raw_hits]
        logger.info(
            "[EvidenceGraph] retrieval_seeds"
            " kb_id=%s branch=%s raw=%d passed=%d threshold=%.4f"
            " score_min=%s score_max=%s",
            request.runtime.knowledge_id,
            branch,
            len(raw_hits),
            len(passed_hits),
            threshold,
            f"{min(scores):.4f}" if scores else "none",
            f"{max(scores):.4f}" if scores else "none",
        )

    @staticmethod
    def _log_outcome(
        event: str,
        request: GraphRetrievalRequest,
        started_at: float,
        counts: dict[str, int],
        timings: dict[str, int],
        *,
        reason: str | None,
    ) -> None:
        reason_field = f" reason={reason}" if reason is not None else ""
        timing_fields = " ".join(
            f"{key}={value}" for key, value in sorted(timings.items())
        )
        logger.info(
            "[EvidenceGraph] %s kb_id=%s%s"
            " raw_entity_seeds=%d entity_seeds=%d"
            " raw_relation_seeds=%d relation_seeds=%d"
            " neighbor_relations=%d endpoint_entities=%d"
            " evidence_groups=%d evidence_hits=%d source_chunks=%d"
            " hydrated_chunks=%d scoped_chunks=%d result_count=%d"
            " elapsed_ms=%d%s%s",
            event,
            request.runtime.knowledge_id,
            reason_field,
            counts["raw_entity_seeds"],
            counts["entity_seeds"],
            counts["raw_relation_seeds"],
            counts["relation_seeds"],
            counts["neighbor_relations"],
            counts["endpoint_entities"],
            counts["evidence_groups"],
            counts["evidence_hits"],
            counts["source_chunks"],
            counts["hydrated_chunks"],
            counts["scoped_chunks"],
            counts["result_count"],
            KnowledgeGraphRetrievalPipeline._elapsed_ms(started_at),
            " " if timing_fields else "",
            timing_fields,
        )

    @classmethod
    def _normalize_query_plan(
        cls,
        query: str,
        raw_plan: GraphQueryPlan | None,
    ) -> GraphQueryPlan | None:
        if raw_plan is None:
            return None
        if not cls._normalize_query_text(query):
            return None

        return GraphQueryPlan(
            low_level_keywords=cls._clean_query_terms(
                raw_plan.low_level_keywords
            ),
            high_level_keywords=cls._clean_query_terms(
                raw_plan.high_level_keywords
            ),
        )

    @classmethod
    def _clean_query_terms(
        cls,
        values: list[str],
    ) -> list[str]:
        terms: list[str] = []
        for raw_value in values:
            term = cls._normalize_query_text(raw_value)
            if term:
                terms.append(term)
        return terms

    @classmethod
    def _fallback_query_plan(cls, query: str) -> GraphQueryPlan:
        normalized_query = cls._normalize_query_text(query)
        return GraphQueryPlan(
            low_level_keywords=(
                [normalized_query]
                if normalized_query and len(normalized_query) < 50
                else []
            ),
            high_level_keywords=[],
        )

    @staticmethod
    def _has_query_terms(plan: GraphQueryPlan) -> bool:
        return bool(plan.low_level_keywords or plan.high_level_keywords)

    @staticmethod
    def _normalize_query_text(value: str) -> str:
        return str(value).strip()

    @staticmethod
    def _deduplicate_entities(
        hits: Sequence[EntityProjectionHit],
    ) -> list[EntityProjectionHit]:
        by_key: dict[str, EntityProjectionHit] = {}
        for hit in hits:
            current = by_key.get(hit.entity_key)
            if current is None or hit.score > current.score:
                by_key[hit.entity_key] = hit
        return list(by_key.values())

    @staticmethod
    def _order_endpoint_entities(
        hits: Sequence[EntityProjectionHit],
        ordered_keys: Sequence[str],
    ) -> list[EntityProjectionHit]:
        by_key: dict[str, EntityProjectionHit] = {}
        for hit in hits:
            current = by_key.get(hit.entity_key)
            if current is None or hit.score > current.score:
                by_key[hit.entity_key] = hit
        return [
            by_key[key]
            for key in ordered_keys
            if key in by_key
        ]

    @staticmethod
    def _round_robin_entities(
        local_hits: Sequence[EntityProjectionHit],
        global_hits: Sequence[EntityProjectionHit],
    ) -> list[EntityProjectionHit]:
        merged: list[EntityProjectionHit] = []
        seen: set[str] = set()
        for index in range(max(len(local_hits), len(global_hits))):
            for hits in (local_hits, global_hits):
                if index >= len(hits):
                    continue
                hit = hits[index]
                if hit.entity_key in seen:
                    continue
                seen.add(hit.entity_key)
                merged.append(hit)
        return merged

    @staticmethod
    def _round_robin_relations(
        local_hits: Sequence[RelationProjectionHit],
        global_hits: Sequence[RelationProjectionHit],
    ) -> list[RelationProjectionHit]:
        merged: list[RelationProjectionHit] = []
        seen: set[str] = set()
        for index in range(max(len(local_hits), len(global_hits))):
            for hits in (local_hits, global_hits):
                if index >= len(hits):
                    continue
                hit = hits[index]
                if hit.relation_key in seen:
                    continue
                seen.add(hit.relation_key)
                merged.append(hit)
        return merged

    @staticmethod
    def _deduplicate_relations(
        hits: Sequence[RelationProjectionHit],
    ) -> list[RelationProjectionHit]:
        by_key: dict[str, RelationProjectionHit] = {}
        for hit in hits:
            current = by_key.get(hit.relation_key)
            if current is None or hit.score > current.score:
                by_key[hit.relation_key] = hit
        return list(by_key.values())

    @staticmethod
    def _deduplicate_neighbor_relations(
        hits: Sequence[RelationProjectionHit],
    ) -> list[RelationProjectionHit]:
        by_key: dict[str, int] = {}
        ordered: list[RelationProjectionHit] = []
        for hit in hits:
            position = by_key.get(hit.relation_key)
            if position is None:
                by_key[hit.relation_key] = len(ordered)
                ordered.append(hit)
                continue
            if hit.endpoint_degree > ordered[position].endpoint_degree:
                ordered[position] = hit
        return sorted(
            ordered,
            key=lambda hit: -hit.endpoint_degree,
        )

    @staticmethod
    def _chunk_sort_id(chunk: DocumentChunk) -> int:
        try:
            return int((chunk.metadata or {}).get("sort_id"))
        except (TypeError, ValueError):
            return 2**31 - 1

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return int((time.perf_counter() - started_at) * 1000)
