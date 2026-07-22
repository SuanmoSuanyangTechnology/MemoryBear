import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rag.knowledge_graph.config import (
    GraphPipeline,
    GraphPipelineConfigError,
    is_graph_enabled,
    resolve_graph_pipeline,
)
from app.core.rag.metadata.filter_engine import FilterGroup as EngineFilterGroup, MetadataFilterEngine
from app.core.rag.retrieval.exceptions import KnowledgeRetrievalConfigError
from app.core.rag.retrieval.models import (
    GraphRetrievalSnapshot,
    GraphTargetSnapshot,
    ModelRuntimeSnapshot,
    RetrievalParams,
    RetrievalPreparation,
    RetrievalPrincipal,
    RetrievalTarget,
)
from app.core.rag.vdb.elasticsearch.elasticsearch_vector import ElasticSearchVectorIndexOps
from app.db import get_async_db_context
from app.models import knowledge_model, knowledgeshare_model
from app.models.models_model import ModelConfig
from app.repositories import knowledge_repository
from app.schemas.chunk_schema import KnowledgeBaseConfig, RetrieveType
from app.schemas.knowledge_metadata_schema import MetadataFilterMode
from app.schemas.knowledge_retrieval_schema import KnowledgeRetrievalRequest
from app.services import knowledge_service, knowledgeshare_service
from app.services.knowledge_metadata_service import KnowledgeMetadataService
from app.services.model_service import ModelApiKeyService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _KnowledgeRetrievalRef:
    knowledge: Any
    config: KnowledgeBaseConfig | None


class KnowledgeRetrievalPreparation:
    @classmethod
    async def prepare(
        cls,
        request: KnowledgeRetrievalRequest,
        principal: RetrievalPrincipal | None,
    ) -> RetrievalPreparation:
        async with get_async_db_context() as db:
            return await cls._prepare_with_db(db, request, principal)

    @classmethod
    async def resolve_metadata_document_ids(
        cls,
        preparation: RetrievalPreparation,
        filter_groups: list[EngineFilterGroup],
    ) -> list[str] | None:
        if not filter_groups:
            return None
        async with get_async_db_context() as db:
            return await cls._query_document_ids(db, preparation, filter_groups)

    @classmethod
    async def _prepare_with_db(
        cls,
        db: AsyncSession,
        request: KnowledgeRetrievalRequest,
        principal: RetrievalPrincipal | None,
    ) -> RetrievalPreparation:
        refs = await cls._resolve_retrievable_knowledge_refs_async(db, request, principal)
        targets, tenant_id = await cls._resolve_retrieval_targets_async(
            db,
            request,
            principal,
            refs=refs,
        )
        if not targets:
            return RetrievalPreparation(
                targets=(),
                tenant_id=None,
                metadata_defs_by_kb={},
                common_metadata_defs={},
                metadata_llm=None,
                graph=None,
            )

        metadata_defs_by_kb = {
            target.knowledge_id: await KnowledgeMetadataService.get_metadata_defs_for_filtering_async(
                db,
                target.knowledge_id,
            )
            for target in targets
        }
        common_metadata_defs = cls._get_common_metadata_defs(metadata_defs_by_kb)
        metadata_llm = await cls._build_metadata_llm_snapshot(
            db,
            request,
            refs[0].knowledge,
            common_metadata_defs,
            tenant_id,
        )
        graph = await cls._build_graph_snapshot(
            db,
            request,
            refs,
            targets,
            tenant_id,
        )
        evidence_graph_only = (
            graph is not None
            and graph.pipeline is GraphPipeline.EVIDENCE
            and all(
                target.params.retrieve_type == RetrieveType.Graph
                for target in targets
            )
        )
        request_reranker = None
        if not evidence_graph_only:
            request_reranker = await cls._snapshot_model_runtime(
                db,
                request.rerank_id,
                tenant_id,
            )
        return RetrievalPreparation(
            targets=tuple(targets),
            tenant_id=tenant_id,
            metadata_defs_by_kb=metadata_defs_by_kb,
            common_metadata_defs=common_metadata_defs,
            metadata_llm=metadata_llm,
            graph=graph,
            request_reranker=request_reranker,
        )

    @classmethod
    async def _query_document_ids(
        cls,
        db: AsyncSession,
        preparation: RetrievalPreparation,
        filter_groups: list[EngineFilterGroup],
    ) -> list[str]:
        del cls
        document_ids: set[uuid.UUID] = set()
        engine = MetadataFilterEngine(db)
        for target in preparation.targets:
            metadata_defs = preparation.metadata_defs_by_kb.get(target.knowledge_id)
            if metadata_defs is None:
                continue
            matched_ids = await engine.execute_async(
                knowledge_id=target.knowledge_id,
                filter_groups=filter_groups,
                metadata_defs=metadata_defs,
            )
            document_ids.update(matched_ids)
        return [str(document_id) for document_id in document_ids]

    @classmethod
    async def _resolve_retrieval_targets_async(
        cls,
        db: AsyncSession,
        request: KnowledgeRetrievalRequest,
        principal: RetrievalPrincipal | None = None,
        *,
        refs: list[_KnowledgeRetrievalRef] | None = None,
    ) -> tuple[list[RetrievalTarget], uuid.UUID | None]:
        if refs is None:
            refs = await cls._resolve_retrievable_knowledge_refs_async(db, request, principal)
        if not refs:
            return [], None

        tenant_id = await cls._resolve_tenant_id_async(
            db,
            principal,
            getattr(refs[0].knowledge, "workspace_id", None),
        )
        targets = [
            await cls._build_retrieval_target_async(db, request, ref, tenant_id)
            for ref in refs
        ]
        return targets, tenant_id

    @staticmethod
    async def _resolve_tenant_id_async(
        db: AsyncSession,
        principal: RetrievalPrincipal | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> uuid.UUID | None:
        if principal is not None:
            if principal.tenant_id:
                return principal.tenant_id
            workspace_id = workspace_id or principal.current_workspace_id

        if not workspace_id:
            return None
        from app.models.workspace_model import Workspace

        workspace = await db.get(Workspace, workspace_id)
        return workspace.tenant_id if workspace else None

    @classmethod
    async def _build_retrieval_target_async(
        cls,
        db: AsyncSession,
        request: KnowledgeRetrievalRequest,
        ref: _KnowledgeRetrievalRef,
        tenant_id: uuid.UUID | None,
    ) -> RetrievalTarget:
        knowledge = ref.knowledge
        params = cls._build_retrieval_params(request, ref.config)
        evidence_graph = False
        if params.retrieve_type == RetrieveType.Graph:
            try:
                evidence_graph = (
                    resolve_graph_pipeline(knowledge.parser_config)
                    is GraphPipeline.EVIDENCE
                )
            except GraphPipelineConfigError as exc:
                raise KnowledgeRetrievalConfigError(str(exc)) from exc
        if not knowledge.embedding_id:
            raise KnowledgeRetrievalConfigError(f"embedding_id config error: {knowledge.id}")

        embedding = await cls._snapshot_model_runtime(db, knowledge.embedding_id, tenant_id)
        if not embedding:
            raise KnowledgeRetrievalConfigError(f"No embedding api key found for knowledge {knowledge.id}")

        reranker = None
        if not evidence_graph:
            if not knowledge.reranker_id:
                raise KnowledgeRetrievalConfigError(f"reranker_id config error: {knowledge.id}")
            reranker = await cls._snapshot_model_runtime(db, knowledge.reranker_id, tenant_id)
            if not reranker:
                raise KnowledgeRetrievalConfigError(f"No reranker api key found for knowledge {knowledge.id}")

        return RetrievalTarget(
            knowledge_id=knowledge.id,
            workspace_id=knowledge.workspace_id,
            index_name=ElasticSearchVectorIndexOps.collection_name_for_knowledge(knowledge.id),
            params=params,
            embedding=embedding,
            reranker=reranker,
        )

    @staticmethod
    def _resolve_rerank_score_threshold(
        request: KnowledgeRetrievalRequest,
        config: KnowledgeBaseConfig | None = None,
    ) -> float:
        explicit_fields = config.model_fields_set if config else set()
        if config and "rerank_score_threshold" in explicit_fields and config.rerank_score_threshold is not None:
            return config.rerank_score_threshold
        if config and "vector_similarity_weight" in explicit_fields and config.vector_similarity_weight is not None:
            return config.vector_similarity_weight
        if request.rerank_score_threshold is not None:
            return request.rerank_score_threshold
        if request.vector_similarity_weight is not None:
            return request.vector_similarity_weight
        return 0.1

    @classmethod
    def _build_retrieval_params(
        cls,
        request: KnowledgeRetrievalRequest,
        config: KnowledgeBaseConfig | None = None,
    ) -> RetrievalParams:
        explicit_fields = config.model_fields_set if config else set()
        top_k = config.top_k if config and "top_k" in explicit_fields else request.top_k
        retrieve_type = config.retrieve_type if config and "retrieve_type" in explicit_fields else request.retrieve_type
        similarity_threshold = (
            config.similarity_threshold
            if config and "similarity_threshold" in explicit_fields
            else request.similarity_threshold
        )
        vector_similarity_weight = (
            config.vector_similarity_weight
            if config and "vector_similarity_weight" in explicit_fields
            else request.vector_similarity_weight
        )
        rerank_score_threshold = cls._resolve_rerank_score_threshold(request, config)
        top_n = max(top_k, request.top_n or top_k)
        return RetrievalParams(
            similarity_threshold=similarity_threshold,
            vector_similarity_weight=vector_similarity_weight,
            top_k=top_k,
            top_n=top_n,
            retrieve_type=retrieve_type,
            rerank_score_threshold=rerank_score_threshold,
        )

    @classmethod
    async def _resolve_retrievable_knowledge_refs_async(
        cls,
        db: AsyncSession,
        request: KnowledgeRetrievalRequest,
        principal: RetrievalPrincipal | None = None,
    ) -> list[_KnowledgeRetrievalRef]:
        requested_kb_ids, explicit_configs = await cls._resolve_requested_kb_ids_and_configs_async(
            db,
            request,
            principal,
        )
        if not requested_kb_ids:
            return []

        refs: list[_KnowledgeRetrievalRef] = []
        ref_positions: dict[uuid.UUID, int] = {}

        def append_refs(items: list[_KnowledgeRetrievalRef]) -> None:
            for item in items:
                knowledge_id = item.knowledge.id
                if knowledge_id in ref_positions:
                    if item.config is not None:
                        refs[ref_positions[knowledge_id]] = item
                    continue
                ref_positions[knowledge_id] = len(refs)
                refs.append(item)

        if principal is None:
            result = await db.execute(
                select(knowledge_model.Knowledge).where(
                    knowledge_model.Knowledge.id.in_(requested_kb_ids),
                    knowledge_model.Knowledge.status == 1,
                )
            )
            knowledge_by_id = {knowledge.id: knowledge for knowledge in result.scalars().all()}
            for knowledge_id in requested_kb_ids:
                knowledge = knowledge_by_id.get(knowledge_id)
                if not knowledge:
                    continue
                append_refs(
                    await cls._expand_knowledge_to_leaf_refs_async(
                        db,
                        knowledge,
                        explicit_configs.get(knowledge_id),
                        explicit_configs,
                    )
                )
            return refs

        for knowledge_id in requested_kb_ids:
            private_result = await db.execute(
                select(knowledge_model.Knowledge).where(
                    knowledge_model.Knowledge.id == knowledge_id,
                    knowledge_model.Knowledge.workspace_id == principal.current_workspace_id,
                    knowledge_model.Knowledge.permission_id == knowledge_model.PermissionType.Private,
                    knowledge_model.Knowledge.status == 1,
                )
            )
            private_target = private_result.scalars().first()
            if private_target:
                append_refs(
                    await cls._expand_knowledge_to_leaf_refs_async(
                        db,
                        private_target,
                        explicit_configs.get(knowledge_id),
                        explicit_configs,
                    )
                )
                continue

            share_result = await db.execute(
                select(knowledge_model.Knowledge).where(
                    knowledge_model.Knowledge.id == knowledge_id,
                    knowledge_model.Knowledge.workspace_id == principal.current_workspace_id,
                    knowledge_model.Knowledge.permission_id == knowledge_model.PermissionType.Share,
                    knowledge_model.Knowledge.status == 1,
                )
            )
            share_target = share_result.scalars().first()
            if not share_target:
                continue

            filters = [
                knowledgeshare_model.KnowledgeShare.target_kb_id == share_target.id,
                knowledgeshare_model.KnowledgeShare.target_workspace_id == principal.current_workspace_id,
            ]
            share_items = await knowledgeshare_service.get_source_kb_ids_by_target_kb_id_async(
                db=db,
                filters=filters,
                current_user=principal,
            )
            for source_knowledge_id, _source_workspace_id in share_items:
                source_knowledge = await knowledge_repository.get_knowledge_by_id_async(
                    db,
                    source_knowledge_id,
                )
                append_refs(
                    await cls._expand_knowledge_to_leaf_refs_async(
                        db,
                        source_knowledge,
                        explicit_configs.get(knowledge_id),
                        explicit_configs,
                    )
                )

        return refs

    @classmethod
    async def _resolve_requested_kb_ids_and_configs_async(
        cls,
        db: AsyncSession,
        request: KnowledgeRetrievalRequest,
        principal: RetrievalPrincipal | None = None,
    ) -> tuple[list[uuid.UUID], dict[uuid.UUID, KnowledgeBaseConfig]]:
        explicit_configs: dict[uuid.UUID, KnowledgeBaseConfig] = {}
        requested_kb_ids = list(request.kb_ids)

        if request.ex_ids:
            if principal is None:
                raise KnowledgeRetrievalConfigError("current_user is required to resolve ex_ids")
            resolved_ids = await knowledge_service.get_knowledge_ids_by_external_ids_async(
                db=db,
                external_ids=request.ex_ids,
                workspace_id=principal.current_workspace_id,
                current_user=principal,
            )
            requested_kb_ids.extend(resolved_ids)

        for config in request.knowledge_bases:
            if config.kb_id in explicit_configs:
                logger.warning("Duplicate KnowledgeBaseConfig found, using the last one: kb_id=%s", config.kb_id)
            explicit_configs[config.kb_id] = config
            requested_kb_ids.append(config.kb_id)

        return cls._unique_ids(requested_kb_ids), explicit_configs

    @classmethod
    async def _expand_knowledge_to_leaf_refs_async(
        cls,
        db: AsyncSession,
        knowledge: Any,
        inherited_config: KnowledgeBaseConfig | None,
        explicit_configs: dict[uuid.UUID, KnowledgeBaseConfig],
        visited: set[uuid.UUID] | None = None,
    ) -> list[_KnowledgeRetrievalRef]:
        if not knowledge or not knowledge.is_active:
            return []
        current_config = explicit_configs.get(knowledge.id) or inherited_config
        if knowledge.is_retrievable_leaf:
            return [_KnowledgeRetrievalRef(knowledge=knowledge, config=current_config)]
        if not knowledge.is_folder:
            return []

        if visited is None:
            visited = set()
        if knowledge.id in visited:
            logger.warning(
                "Detected cyclic knowledge folder while expanding retrieval targets: knowledge_id=%s",
                knowledge.id,
            )
            return []
        visited.add(knowledge.id)

        refs: list[_KnowledgeRetrievalRef] = []
        children = await knowledge_repository.get_knowledges_by_parent_id_async(db, knowledge.id)
        for child in children:
            if child.workspace_id != knowledge.workspace_id:
                logger.warning(
                    "Skipping child knowledge from another workspace while expanding folder: folder_id=%s, child_id=%s",
                    knowledge.id,
                    child.id,
                )
                continue
            refs.extend(
                await cls._expand_knowledge_to_leaf_refs_async(
                    db,
                    child,
                    current_config,
                    explicit_configs,
                    visited,
                )
            )
        return refs

    @staticmethod
    def _unique_ids(values: list[uuid.UUID]) -> list[uuid.UUID]:
        return list(dict.fromkeys(values))

    @staticmethod
    def _get_common_metadata_defs(
        metadata_defs_by_kb: dict[uuid.UUID, dict[str, dict]],
    ) -> dict[str, dict]:
        field_names = set()
        for metadata_defs in metadata_defs_by_kb.values():
            field_names.update(metadata_defs.keys())

        common_defs: dict[str, dict] = {}
        for field_name in field_names:
            common_type = None
            common_def = None
            for metadata_defs in metadata_defs_by_kb.values():
                field_def = metadata_defs.get(field_name)
                if not field_def:
                    common_def = None
                    break
                if common_type is None:
                    common_type = field_def["type"]
                    common_def = field_def
                elif common_type != field_def["type"]:
                    common_def = None
                    break
            if common_def:
                common_defs[field_name] = dict(common_def)
        return common_defs

    @classmethod
    async def _snapshot_model_runtime(
        cls,
        db: AsyncSession,
        model_id: uuid.UUID | None,
        tenant_id: uuid.UUID | None,
    ) -> ModelRuntimeSnapshot | None:
        del cls
        if model_id is None:
            return None
        model_config = await db.get(ModelConfig, model_id)
        if not model_config:
            return None
        api_key = await ModelApiKeyService.get_available_api_key_async(
            db,
            model_id,
            tenant_id=tenant_id,
        )
        if not api_key:
            return None
        return ModelRuntimeSnapshot.from_api_key(api_key, model_type=model_config.type)

    @classmethod
    async def _build_metadata_llm_snapshot(
        cls,
        db: AsyncSession,
        request: KnowledgeRetrievalRequest,
        knowledge: Any,
        common_metadata_defs: dict[str, dict],
        tenant_id: uuid.UUID | None,
    ) -> ModelRuntimeSnapshot | None:
        if (
            request.metadata_filter_mode != MetadataFilterMode.AUTO
            or request.metadata_filters
            or not common_metadata_defs
        ):
            return None
        return await cls._snapshot_model_runtime(db, knowledge.llm_id, tenant_id)

    @classmethod
    async def _build_graph_snapshot(
        cls,
        db: AsyncSession,
        request: KnowledgeRetrievalRequest,
        refs: list[_KnowledgeRetrievalRef],
        targets: list[RetrievalTarget],
        tenant_id: uuid.UUID | None,
    ) -> GraphRetrievalSnapshot | None:
        graph_targets = [
            target
            for target in targets
            if target.params.retrieve_type == RetrieveType.Graph
        ]
        if not graph_targets:
            return None

        refs_by_knowledge_id = {
            ref.knowledge.id: ref
            for ref in refs
        }
        resolved_targets: list[
            tuple[RetrievalTarget, Any, GraphPipeline]
        ] = []
        pipelines: set[GraphPipeline] = set()
        for target in graph_targets:
            ref = refs_by_knowledge_id.get(target.knowledge_id)
            if ref is None:
                raise KnowledgeRetrievalConfigError(
                    f"knowledge snapshot is missing for graph target {target.knowledge_id}"
                )
            knowledge = ref.knowledge
            if not is_graph_enabled(knowledge.parser_config):
                raise KnowledgeRetrievalConfigError(
                    f"knowledge graph is disabled: {knowledge.id}"
                )
            try:
                pipeline = resolve_graph_pipeline(knowledge.parser_config)
            except GraphPipelineConfigError as exc:
                raise KnowledgeRetrievalConfigError(str(exc)) from exc
            pipelines.add(pipeline)
            resolved_targets.append((target, knowledge, pipeline))

        if len(pipelines) != 1:
            raise KnowledgeRetrievalConfigError(
                "all graph targets must use the same graph pipeline"
            )

        target_snapshots: list[GraphTargetSnapshot] = []
        for target, knowledge, pipeline in resolved_targets:
            llm = await cls._snapshot_model_runtime(
                db,
                knowledge.llm_id,
                tenant_id,
            )
            if not llm:
                raise KnowledgeRetrievalConfigError(
                    f"No LLM api key found for knowledge {knowledge.id}",
                )
            target_snapshots.append(
                GraphTargetSnapshot(
                    knowledge_id=target.knowledge_id,
                    workspace_id=target.workspace_id,
                    chunk_index_name=target.index_name,
                    graph_index_name=f"graphrag_{target.workspace_id}",
                    pipeline=pipeline,
                    llm=llm,
                    embedding=target.embedding,
                )
            )

        pipeline = target_snapshots[0].pipeline
        return GraphRetrievalSnapshot(
            query=request.query,
            pipeline=pipeline,
            targets=tuple(target_snapshots),
        )
