"""Resolve retrieval targets into request-local scalar snapshots."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from redbear_model import ResolvedModelConfig, resolve_model_async
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.dependencies import Principal
from ..api.schemas.chunk import KnowledgeBaseConfig, RetrieveType
from ..api.schemas.knowledge_metadata import MetadataFilterMode
from ..api.schemas.knowledge_retrieval import KnowledgeRetrievalRequest
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


def _model_unavailable(message: str) -> KnowledgeError:
    return KnowledgeError.from_code("KB_MODEL_UNAVAILABLE", message)


class KnowledgeRetrievalPreparation:
    @classmethod
    async def prepare_with_db(
        cls,
        db: AsyncSession,
        request: KnowledgeRetrievalRequest,
        principal: Principal,
    ) -> RetrievalPreparation:
        refs = await cls._resolve_retrievable_refs(db, request, principal)
        targets = [await cls._build_target(db, request, principal, ref) for ref in refs]
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
            and not request.metadata_filters_prepared
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
        if not single_evidence_graph_target:
            request_reranker = await cls._snapshot_model(
                db,
                request.rerank_id,
                principal.tenant_id,
            )
        return RetrievalPreparation(
            targets=tuple(targets),
            tenant_id=principal.tenant_id,
            metadata_defs_by_kb=metadata_defs_by_kb,
            common_metadata_defs=common_metadata_defs,
            metadata_llm=metadata_llm,
            graph=graph,
            request_reranker=request_reranker,
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
        request: KnowledgeRetrievalRequest,
        principal: Principal,
    ) -> list[_KnowledgeRef]:
        requested = list(request.kb_ids)
        if request.ex_ids:
            result = await db.execute(
                select(Knowledge.id).where(
                    Knowledge.external_id.in_(request.ex_ids),
                    Knowledge.workspace_id == principal.workspace_id,
                    Knowledge.status == 1,
                )
            )
            requested.extend(result.scalars().all())
        explicit = {config.kb_id: config for config in request.knowledge_bases}
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
    ) -> RetrievalTarget:
        knowledge = ref.knowledge
        if knowledge.embedding_id is None:
            raise _model_unavailable(f"embedding_id config error: {knowledge.id}")
        embedding = await cls._snapshot_model(db, knowledge.embedding_id, principal.tenant_id)
        if embedding is None:
            raise _model_unavailable(f"No embedding api key found for knowledge {knowledge.id}")
        if knowledge.reranker_id is None:
            raise _model_unavailable(f"reranker_id config error: {knowledge.id}")
        reranker = await cls._snapshot_model(db, knowledge.reranker_id, principal.tenant_id)
        if reranker is None:
            raise _model_unavailable(f"No reranker api key found for knowledge {knowledge.id}")
        return RetrievalTarget(
            knowledge_id=knowledge.id,
            workspace_id=knowledge.workspace_id,
            index_name=collection_name_for_knowledge(knowledge.id),
            params=cls._build_retrieval_params(request, ref.config),
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
        return GraphRetrievalSnapshot(request.query, snapshots[0].pipeline, tuple(snapshots))

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


__all__ = ["KnowledgeRetrievalPreparation"]
