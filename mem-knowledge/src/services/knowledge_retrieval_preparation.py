"""Resolve retrieval targets into request-local scalar snapshots."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, replace
from typing import Any

from redbear_model import (
    ResolvedModelConfig,
    is_qwen3_vl_embedding,
    is_qwen3_vl_reranker,
    resolve_model_async,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.dependencies import Principal
from ..api.schemas.chunk import (
    KnowledgeBaseConfig,
    RetrievalPolicy,
    RetrievalPolicyRequest,
    RetrieveType,
)
from ..api.schemas.knowledge_metadata import MetadataFilterMode
from ..api.schemas.knowledge_retrieval import KnowledgeRetrievalRequest
from ..api.schemas.rerank import RerankMode, RerankWeights
from ..errors import KnowledgeError
from ..models.owned import Knowledge, KnowledgeShare, PermissionType
from ..rag.knowledge_graph.config import (
    GraphPipeline,
    GraphPipelineConfigError,
    is_graph_enabled,
    resolve_graph_pipeline,
)
from ..rag.retrieval.async_elasticsearch import collection_name_for_knowledge
from ..rag.retrieval.models import (
    GraphRetrievalSnapshot,
    GraphTargetSnapshot,
    ModelRuntimeSnapshot,
    RerankPlan,
    RerankWeightsSnapshot,
    RetrievalParams,
    RetrievalPreparation,
    RetrievalTarget,
)
from ..repositories.model_registry import AsyncSQLModelRegistry
from .knowledge_metadata import KnowledgeMetadataService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _KnowledgeRef:
    knowledge: Knowledge
    config: KnowledgeBaseConfig | None


@dataclass(frozen=True)
class RetrievalPolicyModelTarget:
    embedding: ModelRuntimeSnapshot
    reranker: ModelRuntimeSnapshot | None


def _supports_qwen3_vl_embedding(snapshot: ModelRuntimeSnapshot) -> bool:
    return isinstance(snapshot.resolved, ResolvedModelConfig) and is_qwen3_vl_embedding(
        snapshot.resolved
    )


def _supports_qwen3_vl_rerank(snapshot: ModelRuntimeSnapshot | None) -> bool:
    return (
        snapshot is not None
        and isinstance(snapshot.resolved, ResolvedModelConfig)
        and is_qwen3_vl_reranker(snapshot.resolved)
    )


def select_effective_reranker(
    *,
    request_has_rerank_id: bool,
    request_reranker: ModelRuntimeSnapshot | None,
    fallback_reranker: ModelRuntimeSnapshot | None,
) -> ModelRuntimeSnapshot | None:
    return request_reranker if request_has_rerank_id else fallback_reranker


def build_retrieval_policy(
    *,
    targets: tuple[RetrievalPolicyModelTarget, ...],
    request_reranker: ModelRuntimeSnapshot | None,
    request_has_rerank_id: bool,
) -> RetrievalPolicy:
    if not targets:
        raise ValueError("retrieval policy requires at least one target")
    embeddings_support_image = all(
        _supports_qwen3_vl_embedding(target.embedding) for target in targets
    )
    global_reranker = select_effective_reranker(
        request_has_rerank_id=request_has_rerank_id,
        request_reranker=request_reranker,
        fallback_reranker=targets[0].reranker,
    )
    semantic_requires_reranker = len(targets) > 1 or request_has_rerank_id
    semantic_supports_image = embeddings_support_image and (
        not semantic_requires_reranker
        or _supports_qwen3_vl_rerank(global_reranker)
    )
    if len(targets) == 1:
        local_reranker = select_effective_reranker(
            request_has_rerank_id=request_has_rerank_id,
            request_reranker=request_reranker,
            fallback_reranker=targets[0].reranker,
        )
        hybrid_supports_image = embeddings_support_image and _supports_qwen3_vl_rerank(
            local_reranker
        )
    else:
        hybrid_supports_image = (
            embeddings_support_image
            and all(_supports_qwen3_vl_rerank(target.reranker) for target in targets)
            and _supports_qwen3_vl_rerank(global_reranker)
        )
    return RetrievalPolicy(
        semantic=("text", "image") if semantic_supports_image else ("text",),
        hybrid=("text", "image") if hybrid_supports_image else ("text",),
    )


def _model_unavailable(message: str) -> KnowledgeError:
    return KnowledgeError.from_code("KB_MODEL_UNAVAILABLE", message)


class KnowledgeRetrievalPreparation:
    @classmethod
    async def prepare_policy_with_db(
        cls,
        db: AsyncSession,
        request: RetrievalPolicyRequest,
        principal: Principal,
    ) -> RetrievalPolicy:
        refs = await cls._resolve_retrievable_refs(db, request, principal)
        if not refs:
            raise KnowledgeError.from_code(
                "KB_RESOURCE_NOT_FOUND",
                "Knowledge resources were not found",
            )
        targets: list[RetrievalPolicyModelTarget] = []
        for ref in refs:
            knowledge = ref.knowledge
            if knowledge.embedding_id is None:
                raise _model_unavailable("Knowledge embedding model is unavailable")
            embedding = await cls._snapshot_model(
                db,
                knowledge.embedding_id,
                principal.tenant_id,
            )
            if embedding is None:
                raise _model_unavailable("Knowledge embedding model is unavailable")
            reranker = await cls._snapshot_model(
                db,
                knowledge.reranker_id,
                principal.tenant_id,
            )
            targets.append(
                RetrievalPolicyModelTarget(
                    embedding=embedding,
                    reranker=reranker,
                )
            )
        request_reranker = await cls._snapshot_model(
            db,
            request.rerank_id,
            principal.tenant_id,
        )
        if request.rerank_id is not None and request_reranker is None:
            raise _model_unavailable("Request rerank model is unavailable")
        return build_retrieval_policy(
            targets=tuple(targets),
            request_reranker=request_reranker,
            request_has_rerank_id=request.rerank_id is not None,
        )

    @classmethod
    async def prepare_with_db(
        cls,
        db: AsyncSession,
        request: KnowledgeRetrievalRequest,
        principal: Principal,
    ) -> RetrievalPreparation:
        refs = await cls._resolve_retrievable_refs(db, request, principal)
        target_count = len(refs)
        local_selections = [
            cls._resolve_local_selection(request, ref.config, target_count)
            for ref in refs
        ]
        single_retrieve_type = (
            cls._resolve_retrieve_type(request, refs[0].config)
            if target_count == 1
            else None
        )
        global_selection = cls._resolve_global_selection(
            request,
            target_count,
            single_retrieve_type,
        )
        global_mode = global_selection[0] if global_selection is not None else None
        cls._validate_weighted_selections(
            request,
            refs,
            local_selections,
            global_mode,
        )
        targets = [
            await cls._build_target(
                db,
                request,
                principal,
                ref,
                local_selection=local_selections[index],
                global_mode=global_mode,
                target_index=index,
                target_count=target_count,
            )
            for index, ref in enumerate(refs)
        ]
        cls._validate_weighted_targets(targets, global_mode)
        metadata_defs_by_kb = {
            target.knowledge_id: (
                await KnowledgeMetadataService.get_metadata_defs_for_filtering_async(
                    db,
                    target.knowledge_id,
                )
            )
            for target in targets
        }
        common_metadata_defs = cls._get_common_metadata_defs(metadata_defs_by_kb)
        metadata_llm = None
        if (
            refs
            and request.metadata_filter_mode is MetadataFilterMode.AUTO
            and not request.metadata_filters
            and not request.metadata_filters_resolved
            and common_metadata_defs
        ):
            metadata_llm = await cls._snapshot_model(
                db,
                refs[0].knowledge.llm_id,
                principal.tenant_id,
            )
        graph = await cls._build_graph_snapshot(db, request, principal, refs, targets)
        single_evidence_graph_target = (
            graph is not None
            and graph.pipeline is GraphPipeline.EVIDENCE
            and len(targets) == 1
            and targets[0].params.retrieve_type is RetrieveType.Graph
        )
        request_reranker = None
        single_hybrid_uses_request_model = (
            request.rerank_id is not None
            and target_count == 1
            and targets[0].params.retrieve_type is RetrieveType.HYBRID
            and targets[0].params.local_rerank is not None
            and targets[0].params.local_rerank.mode is RerankMode.RERANKING_MODEL
        )
        request_model_required = (
            request.rerank_id is not None
            and not single_evidence_graph_target
            and (
                single_hybrid_uses_request_model
                or global_mode is RerankMode.RERANKING_MODEL
            )
        )
        if request_model_required:
            request_reranker = await cls._snapshot_model(
                db,
                request.rerank_id,
                principal.tenant_id,
            )
        if single_hybrid_uses_request_model:
            local_plan = targets[0].params.local_rerank
            if local_plan is not None:
                targets[0] = replace(
                    targets[0],
                    params=replace(
                        targets[0].params,
                        local_rerank=replace(local_plan, model=request_reranker),
                    ),
                )
        global_rerank = None
        if global_selection is not None and not single_evidence_graph_target:
            mode, weights, _ = global_selection
            global_rerank = RerankPlan(
                mode=mode,
                weights=weights,
                model=select_effective_reranker(
                    request_has_rerank_id=request.rerank_id is not None,
                    request_reranker=request_reranker,
                    fallback_reranker=targets[0].reranker if targets else None,
                )
                if mode is RerankMode.RERANKING_MODEL
                else None,
                compatibility_fallback=mode is RerankMode.RERANKING_MODEL,
            )
        return RetrievalPreparation(
            targets=tuple(targets),
            tenant_id=principal.tenant_id,
            metadata_defs_by_kb=metadata_defs_by_kb,
            common_metadata_defs=common_metadata_defs,
            metadata_llm=metadata_llm,
            graph=graph,
            request_reranker=request_reranker,
            global_rerank=global_rerank,
        )

    @classmethod
    async def resolve_metadata_document_ids(
        cls,
        db: AsyncSession,
        preparation: RetrievalPreparation,
        filter_groups: list[Any],
    ) -> list[str] | None:
        if not filter_groups:
            return None
        from ..rag.metadata.filter_engine import MetadataFilterEngine

        document_ids: set[uuid.UUID] = set()
        engine = MetadataFilterEngine(db)
        for target in preparation.targets:
            metadata_defs = preparation.metadata_defs_by_kb.get(target.knowledge_id)
            if metadata_defs is None:
                continue
            matched = await engine.execute_async(
                target.knowledge_id,
                filter_groups,
                metadata_defs,
            )
            document_ids.update(matched)
        return [str(document_id) for document_id in document_ids]

    @classmethod
    async def _resolve_retrievable_refs(
        cls,
        db: AsyncSession,
        request: KnowledgeRetrievalRequest | RetrievalPolicyRequest,
        principal: Principal,
    ) -> list[_KnowledgeRef]:
        requested = list(request.kb_ids)
        ex_ids = getattr(request, "ex_ids", None)
        if ex_ids:
            result = await db.execute(
                select(Knowledge.id).where(
                    Knowledge.external_id.in_(ex_ids),
                    Knowledge.workspace_id == principal.workspace_id,
                    Knowledge.status == 1,
                )
            )
            requested.extend(result.scalars().all())
        knowledge_bases = getattr(request, "knowledge_bases", ())
        explicit = {config.kb_id: config for config in knowledge_bases}
        requested.extend(explicit)
        refs: list[_KnowledgeRef] = []
        positions: dict[uuid.UUID, int] = {}

        def append(items: list[_KnowledgeRef]) -> None:
            for item in items:
                position = positions.get(item.knowledge.id)
                if position is None:
                    positions[item.knowledge.id] = len(refs)
                    refs.append(item)
                elif item.config is not None:
                    refs[position] = item

        for knowledge_id in cls._unique_ids(requested):
            result = await db.execute(
                select(Knowledge).where(
                    Knowledge.id == knowledge_id,
                    Knowledge.workspace_id == principal.workspace_id,
                    Knowledge.status == 1,
                )
            )
            knowledge = result.scalars().first()
            if knowledge is None:
                continue
            config = explicit.get(knowledge_id)
            if knowledge.permission_id == PermissionType.Private:
                append(await cls._expand_folder(db, knowledge, config, explicit, set()))
                continue
            if knowledge.permission_id != PermissionType.Share:
                continue
            share_result = await db.execute(
                select(KnowledgeShare.source_kb_id).where(
                    KnowledgeShare.target_kb_id == knowledge.id,
                    KnowledgeShare.target_workspace_id == principal.workspace_id,
                )
            )
            for source_id in share_result.scalars().all():
                source_result = await db.execute(
                    select(Knowledge).where(Knowledge.id == source_id, Knowledge.status == 1)
                )
                source = source_result.scalars().first()
                if source is not None:
                    append(await cls._expand_folder(db, source, config, explicit, set()))
        return refs

    @classmethod
    async def _expand_folder(
        cls,
        db: AsyncSession,
        knowledge: Knowledge,
        inherited_config: KnowledgeBaseConfig | None,
        explicit: dict[uuid.UUID, KnowledgeBaseConfig],
        visited: set[uuid.UUID],
    ) -> list[_KnowledgeRef]:
        if not knowledge.is_active:
            return []
        config = explicit.get(knowledge.id) or inherited_config
        if knowledge.is_retrievable_leaf:
            return [_KnowledgeRef(knowledge, config)]
        if not knowledge.is_folder or knowledge.id in visited:
            return []
        visited = {*visited, knowledge.id}
        result = await db.execute(
            select(Knowledge).where(
                Knowledge.parent_id == knowledge.id,
                Knowledge.workspace_id == knowledge.workspace_id,
                Knowledge.status == 1,
            )
        )
        refs = []
        for child in result.scalars().all():
            refs.extend(await cls._expand_folder(db, child, config, explicit, visited))
        return refs

    @classmethod
    async def _build_target(
        cls,
        db: AsyncSession,
        request: KnowledgeRetrievalRequest,
        principal: Principal,
        ref: _KnowledgeRef,
        *,
        local_selection: tuple[RerankMode, RerankWeightsSnapshot, bool] | None = None,
        global_mode: RerankMode | None = None,
        target_index: int = 0,
        target_count: int = 1,
    ) -> RetrievalTarget:
        knowledge = ref.knowledge
        selection = local_selection or cls._resolve_local_selection(
            request,
            ref.config,
            target_count,
        )
        local_mode, local_weights, _ = selection
        retrieve_type = cls._resolve_retrieve_type(request, ref.config)
        if knowledge.embedding_id is None:
            raise _model_unavailable(f"embedding_id config error: {knowledge.id}")
        embedding = await cls._snapshot_model(db, knowledge.embedding_id, principal.tenant_id)
        if embedding is None:
            raise _model_unavailable(f"No embedding api key found for knowledge {knowledge.id}")
        reranker = None
        if cls._target_reranker_required(
            retrieve_type=retrieve_type,
            local_mode=local_mode,
            global_mode=global_mode,
            target_index=target_index,
            request_has_rerank_id=request.rerank_id is not None,
        ):
            if knowledge.reranker_id is None:
                raise _model_unavailable(f"reranker_id config error: {knowledge.id}")
            reranker = await cls._snapshot_model(
                db,
                knowledge.reranker_id,
                principal.tenant_id,
            )
            if reranker is None:
                raise _model_unavailable(
                    f"No reranker api key found for knowledge {knowledge.id}"
                )
        local_plan = RerankPlan(
            mode=local_mode,
            weights=local_weights,
            model=reranker if local_mode is RerankMode.RERANKING_MODEL else None,
            compatibility_fallback=local_mode is RerankMode.RERANKING_MODEL,
        )
        return RetrievalTarget(
            knowledge_id=knowledge.id,
            workspace_id=knowledge.workspace_id,
            index_name=collection_name_for_knowledge(knowledge.id),
            params=cls._build_retrieval_params(
                request,
                ref.config,
                local_rerank=local_plan,
            ),
            embedding=embedding,
            reranker=reranker,
        )

    @classmethod
    async def _build_graph_snapshot(
        cls,
        db: AsyncSession,
        request: KnowledgeRetrievalRequest,
        principal: Principal,
        refs: list[_KnowledgeRef],
        targets: list[RetrievalTarget],
    ) -> GraphRetrievalSnapshot | None:
        query_text = request.query_text
        if query_text is None:
            return None
        knowledge_by_id = {ref.knowledge.id: ref.knowledge for ref in refs}
        snapshots = []
        pipelines: set[GraphPipeline] = set()
        for target in targets:
            if not (
                target.params.retrieve_type is RetrieveType.Graph
                or (
                    target.params.retrieve_type is RetrieveType.HYBRID
                    and target.params.enable_graph_retrieval
                )
            ):
                continue
            knowledge = knowledge_by_id[target.knowledge_id]
            if not is_graph_enabled(knowledge.parser_config):
                if target.params.retrieve_type is RetrieveType.Graph:
                    raise KnowledgeError.from_code(
                        "KB_VALIDATION_ERROR",
                        f"knowledge graph is disabled: {knowledge.id}",
                    )
                continue
            try:
                pipeline = resolve_graph_pipeline(knowledge.parser_config)
            except GraphPipelineConfigError as exc:
                raise KnowledgeError.from_code(
                    "KB_VALIDATION_ERROR",
                    str(exc),
                ) from exc
            if (
                target.params.retrieve_type is RetrieveType.HYBRID
                and pipeline is not GraphPipeline.EVIDENCE
            ):
                continue
            llm = await cls._snapshot_model(db, knowledge.llm_id, principal.tenant_id)
            if llm is None:
                raise _model_unavailable(f"No LLM api key found for knowledge {knowledge.id}")
            pipelines.add(pipeline)
            snapshots.append(
                GraphTargetSnapshot(
                    knowledge_id=knowledge.id,
                    workspace_id=knowledge.workspace_id,
                    chunk_index_name=target.index_name,
                    graph_index_name=f"graphrag_{knowledge.workspace_id}",
                    pipeline=pipeline,
                    llm=llm,
                    embedding=target.embedding,
                )
            )
        if not snapshots:
            return None
        if len(pipelines) != 1:
            raise KnowledgeError.from_code(
                "KB_VALIDATION_ERROR",
                "all graph targets must use the same graph pipeline",
            )
        return GraphRetrievalSnapshot(query_text, snapshots[0].pipeline, tuple(snapshots))

    @staticmethod
    async def _snapshot_model(
        db: AsyncSession,
        model_id: uuid.UUID | None,
        tenant_id: uuid.UUID,
    ) -> ModelRuntimeSnapshot | None:
        if model_id is None:
            return None
        try:
            resolved: ResolvedModelConfig = await resolve_model_async(
                AsyncSQLModelRegistry(db),
                model_config_id=model_id,
                tenant_id=tenant_id,
            )
        except Exception:
            return None
        return ModelRuntimeSnapshot(
            model_name=resolved.model_name,
            provider=resolved.provider.value,
            api_key=resolved.api_key.get_secret_value(),
            api_base=resolved.base_url,
            capability=tuple(capability.value for capability in resolved.capabilities),
            is_omni=resolved.is_omni,
            model_type=resolved.model_type.value,
            resolved=resolved,
        )

    @staticmethod
    def _build_retrieval_params(
        request: KnowledgeRetrievalRequest,
        config: KnowledgeBaseConfig | None,
        *,
        local_rerank: RerankPlan | None = None,
    ) -> RetrievalParams:
        fields = config.model_fields_set if config else set()

        def value(name: str, fallback: Any) -> Any:
            if config is not None and name in fields and getattr(config, name) is not None:
                return getattr(config, name)
            return fallback

        vector_weight = value("vector_similarity_weight", request.vector_similarity_weight)
        rerank_threshold = value("rerank_score_threshold", request.rerank_score_threshold)
        if rerank_threshold is None:
            rerank_threshold = vector_weight if vector_weight is not None else 0.1
        top_k = int(value("top_k", request.top_k))
        return RetrievalParams(
            similarity_threshold=float(value("similarity_threshold", request.similarity_threshold)),
            vector_similarity_weight=vector_weight,
            top_k=top_k,
            top_n=max(top_k, int(request.top_n or 20)),
            retrieve_type=value("retrieve_type", request.retrieve_type),
            rerank_score_threshold=float(rerank_threshold),
            enable_graph_retrieval=(
                request.graph_retrieval_mix_enabled_for(config)
                if value("retrieve_type", request.retrieve_type) is RetrieveType.HYBRID
                else False
            ),
            local_rerank=local_rerank,
        )

    @staticmethod
    def _resolve_weights(weights: RerankWeights | None) -> RerankWeightsSnapshot:
        effective = weights or RerankWeights()
        return RerankWeightsSnapshot(
            semantic_weight=effective.semantic_weight,
            participle_weight=effective.participle_weight,
        )

    @classmethod
    def _resolve_local_selection(
        cls,
        request: KnowledgeRetrievalRequest,
        config: KnowledgeBaseConfig | None,
        target_count: int,
    ) -> tuple[RerankMode, RerankWeightsSnapshot, bool]:
        if config is not None and config.rerank_mode is not None:
            return config.rerank_mode, cls._resolve_weights(config.rerank_weights), True
        if target_count == 1 and request.rerank_mode is not None:
            return request.rerank_mode, cls._resolve_weights(request.rerank_weights), True
        return RerankMode.RERANKING_MODEL, cls._resolve_weights(None), False

    @classmethod
    def _resolve_global_selection(
        cls,
        request: KnowledgeRetrievalRequest,
        target_count: int,
        single_retrieve_type: RetrieveType | None,
    ) -> tuple[RerankMode, RerankWeightsSnapshot, bool] | None:
        if target_count == 1 and single_retrieve_type is RetrieveType.HYBRID:
            return None
        if target_count == 1 and request.rerank_id is None:
            return None
        mode = request.rerank_mode or RerankMode.RERANKING_MODEL
        return mode, cls._resolve_weights(request.rerank_weights), request.rerank_mode is not None

    @staticmethod
    def _target_reranker_required(
        *,
        retrieve_type: RetrieveType,
        local_mode: RerankMode,
        global_mode: RerankMode | None,
        target_index: int,
        request_has_rerank_id: bool,
    ) -> bool:
        if (
            retrieve_type is RetrieveType.HYBRID
            and local_mode is RerankMode.RERANKING_MODEL
        ):
            return True
        return (
            global_mode is RerankMode.RERANKING_MODEL
            and target_index == 0
            and not request_has_rerank_id
        )

    @staticmethod
    def _resolve_retrieve_type(
        request: KnowledgeRetrievalRequest,
        config: KnowledgeBaseConfig | None,
    ) -> RetrieveType:
        if (
            config is not None
            and "retrieve_type" in config.model_fields_set
            and config.retrieve_type is not None
        ):
            return config.retrieve_type
        return request.retrieve_type

    @staticmethod
    def _embedding_space_key(
        snapshot: ModelRuntimeSnapshot,
    ) -> tuple[str, str, str]:
        return (
            snapshot.provider.strip().lower(),
            snapshot.model_name.strip(),
            (snapshot.api_base or "").rstrip("/"),
        )

    @classmethod
    def _validate_weighted_targets(
        cls,
        targets: list[RetrievalTarget],
        global_mode: RerankMode | None,
    ) -> None:
        for target in targets:
            local_plan = target.params.local_rerank
            if local_plan is None or local_plan.mode is not RerankMode.WEIGHTED_SCORE:
                continue
            cls._validate_weighted_target(target)
        if global_mode is not RerankMode.WEIGHTED_SCORE:
            return
        for target in targets:
            cls._validate_weighted_target(target)
        embedding_spaces = {
            cls._embedding_space_key(target.embedding) for target in targets
        }
        if len(embedding_spaces) > 1:
            raise KnowledgeError.from_code(
                "KB_VALIDATION_ERROR",
                "Weighted rerank requires matching embedding spaces",
            )

    @classmethod
    def _validate_weighted_selections(
        cls,
        request: KnowledgeRetrievalRequest,
        refs: list[_KnowledgeRef],
        local_selections: list[tuple[RerankMode, RerankWeightsSnapshot, bool]],
        global_mode: RerankMode | None,
    ) -> None:
        for ref, selection in zip(refs, local_selections, strict=True):
            if (
                selection[0] is not RerankMode.WEIGHTED_SCORE
                and global_mode is not RerankMode.WEIGHTED_SCORE
            ):
                continue
            params = cls._build_retrieval_params(request, ref.config)
            cls._validate_weighted_params(params)

    @staticmethod
    def _validate_weighted_target(target: RetrievalTarget) -> None:
        KnowledgeRetrievalPreparation._validate_weighted_params(target.params)

    @staticmethod
    def _validate_weighted_params(params: RetrievalParams) -> None:
        if params.retrieve_type is not RetrieveType.HYBRID:
            raise KnowledgeError.from_code(
                "KB_VALIDATION_ERROR",
                "Weighted rerank requires hybrid retrieval",
            )
        if params.enable_graph_retrieval:
            raise KnowledgeError.from_code(
                "KB_VALIDATION_ERROR",
                "Weighted rerank does not support graph retrieval",
            )

    @staticmethod
    def _get_common_metadata_defs(
        definitions: dict[uuid.UUID, dict[str, dict[str, Any]]],
    ) -> dict[str, dict[str, Any]]:
        if not definitions:
            return {}
        values = list(definitions.values())
        common = {}
        for name, definition in values[0].items():
            if all(
                name in item and item[name].get("type") == definition.get("type")
                for item in values[1:]
            ):
                common[name] = dict(definition)
        return common

    @staticmethod
    def _unique_ids(values: list[uuid.UUID]) -> list[uuid.UUID]:
        return list(dict.fromkeys(values))


__all__ = [
    "KnowledgeRetrievalPreparation",
    "RetrievalPolicyModelTarget",
    "build_retrieval_policy",
    "select_effective_reranker",
]
