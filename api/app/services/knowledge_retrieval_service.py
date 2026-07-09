import asyncio
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from langchain_core.documents import Document
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.models import RedBearLLM, RedBearRerank
from app.core.models.base import RedBearModelConfig
from app.core.rag.llm.chat_model import Base
from app.core.rag.llm.embedding_model import OpenAIEmbed
from app.core.rag.metadata.filter_engine import (
    FilterCondition as EngineFilterCondition,
    FilterGroup as EngineFilterGroup,
    MetadataFilterEngine,
)
from app.core.rag.models.chunk import DocumentChunk, chunk_retrieval_content
from app.core.rag.vdb.elasticsearch.elasticsearch_vector import (
    ElasticSearchVector,
    ElasticSearchVectorFactory,
    ElasticSearchVectorIndexOps,
)
from app.models import ModelType, knowledge_model, knowledgeshare_model
from app.models.models_model import ModelApiKey, ModelConfig
from app.repositories import knowledge_repository
from app.repositories.tool_repository import ToolRepository
from app.schemas.chunk_schema import KnowledgeBaseConfig, RetrieveType
from app.schemas.knowledge_metadata_schema import MetadataFilterMode
from app.schemas.knowledge_retrieval_schema import KnowledgeRetrievalRequest, KnowledgeRetrievalResult
from app.services import knowledge_service, knowledgeshare_service
from app.services.knowledge_metadata_service import KnowledgeMetadataService
from app.services.metadata_auto_filter_service import MetadataAutoFilterService
from app.services.model_service import ModelApiKeyService, ModelConfigService

logger = logging.getLogger(__name__)


class KnowledgeRetrievalAccessDenied(Exception):
    pass


class KnowledgeRetrievalConfigError(Exception):
    pass


@dataclass(frozen=True)
class ModelApiKeySnapshot:
    model_name: str
    provider: str
    api_key: str
    api_base: str | None

    @classmethod
    def from_api_key(cls, api_key: ModelApiKey) -> "ModelApiKeySnapshot":
        return cls(
            model_name=api_key.model_name,
            provider=api_key.provider,
            api_key=api_key.api_key,
            api_base=api_key.api_base,
        )


@dataclass(frozen=True)
class RetrievalParams:
    similarity_threshold: float
    vector_similarity_weight: float
    top_k: int
    top_n: int
    retrieve_type: RetrieveType
    rerank_score_threshold: float = 0.1


@dataclass(frozen=True)
class KnowledgeRetrievalRef:
    knowledge: Any
    config: KnowledgeBaseConfig | None


@dataclass(frozen=True)
class RetrievalTarget:
    knowledge_id: uuid.UUID
    workspace_id: uuid.UUID
    index_name: str
    params: RetrievalParams
    embedding_config: ModelApiKeySnapshot
    reranker_config: ModelApiKeySnapshot


class KnowledgeRetrievalService:
    @staticmethod
    def _new_retrieval_log_id() -> str:
        return uuid.uuid4().hex[:8]

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return max(0, int((time.perf_counter() - started_at) * 1000))

    @staticmethod
    def _compact_id(value: Any) -> str:
        text = str(value)
        return text[:8] if len(text) > 8 else text

    @classmethod
    def _compact_ids(cls, values: list[Any], limit: int = 10) -> list[str]:
        compacted = [cls._compact_id(value) for value in values[:limit]]
        if len(values) > limit:
            compacted.append(f"+{len(values) - limit}")
        return compacted

    @classmethod
    def _format_log_fields(cls, fields: dict[str, Any]) -> str:
        return " ".join(
            f"{key}={cls._format_log_value(value)}"
            for key, value in fields.items()
        )

    @classmethod
    def _format_log_value(cls, value: Any) -> str:
        if value is None:
            return "none"
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, Enum):
            return str(value.value)
        if isinstance(value, (list, tuple, set)):
            return "[" + ",".join(cls._format_log_value(item) for item in value) + "]"
        return str(value)

    @classmethod
    def _build_retrieval_start_log_fields(
            cls,
            log_id: str,
            request: KnowledgeRetrievalRequest,
            current_user: Any = None,
    ) -> dict[str, Any]:
        return {
            "id": log_id,
            "user": getattr(current_user, "username", None) or getattr(current_user, "id", None) or "anonymous",
            "kb_count": len(request.kb_ids or []),
            "ex_id_count": len(request.ex_ids or []),
            "knowledge_base_count": len(request.knowledge_bases or []),
            "query_len": len(request.query or ""),
            "type": request.retrieve_type.value,
            "top_k": request.top_k,
            "top_n": request.top_n,
            "similarity_threshold": request.similarity_threshold,
            "vector_weight": request.vector_similarity_weight,
            "rerank_threshold": request.rerank_score_threshold,
            "metadata_mode": request.metadata_filter_mode.value,
            "metadata_filter_groups": len(request.metadata_filters or []),
            "file_filter_count": len(request.file_names_filter or []),
        }

    @classmethod
    def _build_targets_log_fields(
            cls,
            log_id: str,
            request: KnowledgeRetrievalRequest,
            targets: list[RetrievalTarget],
            max_workers: int,
    ) -> dict[str, Any]:
        return {
            "id": log_id,
            "requested_kb_count": len(request.kb_ids or []) + len(request.knowledge_bases or []),
            "target_count": len(targets),
            "max_workers": max_workers,
            "target_kbs": cls._compact_ids([target.knowledge_id for target in targets]),
        }

    @staticmethod
    def _build_metadata_filter_log_fields(
            log_id: str,
            request: KnowledgeRetrievalRequest,
            effective: bool,
            reason: str,
            document_count: int | None,
            common_fields: int | None = None,
            effective_groups: int | None = None,
    ) -> dict[str, Any]:
        fields = {
            "id": log_id,
            "mode": request.metadata_filter_mode.value,
            "provided_groups": len(request.metadata_filters or []),
            "effective": effective,
            "reason": reason,
            "matched_documents": document_count if document_count is not None else "none",
        }
        if common_fields is not None:
            fields["common_fields"] = common_fields
        if effective_groups is not None:
            fields["effective_groups"] = effective_groups
        return fields

    @classmethod
    def _build_target_done_log_fields(
            cls,
            log_id: str,
            target: RetrievalTarget,
            target_position: int,
            vector_count: int,
            full_text_count: int,
            merged_count: int,
            result_count: int,
            elapsed_ms: int,
            local_rerank: bool,
    ) -> dict[str, Any]:
        return {
            "id": log_id,
            "target": target_position,
            "kb_id": cls._compact_id(target.knowledge_id),
            "index": target.index_name,
            "type": target.params.retrieve_type.value,
            "top_k": target.params.top_k,
            "top_n": target.params.top_n,
            "vector_kept": vector_count,
            "fulltext_kept": full_text_count,
            "merged": merged_count,
            "local_rerank": local_rerank,
            "result_count": result_count,
            "elapsed_ms": elapsed_ms,
        }

    @staticmethod
    def _resolve_tenant_id(
            db: Session,
            current_user: Any = None,
            workspace_id: uuid.UUID | None = None,
    ) -> uuid.UUID | None:
        if current_user is not None:
            tenant_id = getattr(current_user, "tenant_id", None)
            if tenant_id:
                return tenant_id
            workspace_id = workspace_id or getattr(current_user, "current_workspace_id", None)

        if not workspace_id:
            return None
        return ToolRepository.get_tenant_id_by_workspace_id(db, str(workspace_id))

    @classmethod
    def retrieve(
            cls,
            db: Session,
            request: KnowledgeRetrievalRequest,
            current_user: Any = None,
    ) -> KnowledgeRetrievalResult:
        log_id = cls._new_retrieval_log_id()
        started_at = time.perf_counter()
        logger.info(
            "[Retrieval] start %s",
            cls._format_log_fields(cls._build_retrieval_start_log_fields(log_id, request, current_user)),
        )
        targets, tenant_id = cls._resolve_retrieval_targets(
            db=db,
            request=request,
            current_user=current_user,
        )
        if not targets:
            logger.info(
                "[Retrieval] finish %s",
                cls._format_log_fields({
                    "id": log_id,
                    "reason": "no_targets",
                    "target_count": 0,
                    "final_count": 0,
                    "elapsed_ms": cls._elapsed_ms(started_at),
                }),
            )
            return KnowledgeRetrievalResult(chunks=[])

        knowledge_ids = [target.knowledge_id for target in targets]
        workspace_ids = [target.workspace_id for target in targets]
        db_knowledge = cls._get_first_knowledge(
            db=db,
            knowledge_id=targets[0].knowledge_id,
            current_user=current_user,
        )
        if not db_knowledge:
            raise KnowledgeRetrievalAccessDenied("The knowledge base does not exist or access is denied")

        document_ids_include = cls._build_metadata_document_filter(
            db=db,
            request=request,
            knowledge_ids=knowledge_ids,
            tenant_id=tenant_id,
            log_id=log_id,
        )
        if document_ids_include == []:
            logger.info(
                "[Retrieval] finish %s",
                cls._format_log_fields({
                    "id": log_id,
                    "reason": "metadata_filter_empty",
                    "target_count": len(targets),
                    "final_count": 0,
                    "elapsed_ms": cls._elapsed_ms(started_at),
                }),
            )
            return KnowledgeRetrievalResult(chunks=[])

        chunks = cls._retrieve_targets(
            db=db,
            request=request,
            targets=targets,
            document_ids_include=document_ids_include,
            tenant_id=tenant_id,
            log_id=log_id,
        )
        if any(target.params.retrieve_type == RetrieveType.Graph for target in targets):
            graph_doc = cls._retrieve_graph(
                db=db,
                request=request,
                knowledge_ids=knowledge_ids,
                workspace_ids=workspace_ids,
                db_knowledge=db_knowledge,
                tenant_id=tenant_id,
            )
            if graph_doc:
                chunks.insert(0, graph_doc)
        chunks = cls._include_document_ids(chunks, document_ids_include)
        logger.info(
            "[Retrieval] finish %s",
            cls._format_log_fields({
                "id": log_id,
                "reason": "ok",
                "target_count": len(targets),
                "document_filter_count": len(document_ids_include or []),
                "final_count": len(chunks),
                "elapsed_ms": cls._elapsed_ms(started_at),
            }),
        )
        return KnowledgeRetrievalResult(chunks=chunks)

    @classmethod
    async def retrieve_async(
            cls,
            db: AsyncSession,
            request: KnowledgeRetrievalRequest,
            current_user: Any = None,
    ) -> KnowledgeRetrievalResult:
        """Async retrieval entrypoint for async request chains."""
        if not isinstance(db, AsyncSession):
            return await asyncio.to_thread(cls.retrieve, db, request, current_user)

        log_id = cls._new_retrieval_log_id()
        started_at = time.perf_counter()
        logger.info(
            "[Retrieval] start %s",
            cls._format_log_fields(cls._build_retrieval_start_log_fields(log_id, request, current_user)),
        )
        targets, tenant_id = await cls._resolve_retrieval_targets_async(
            db=db,
            request=request,
            current_user=current_user,
        )
        if not targets:
            logger.info(
                "[Retrieval] finish %s",
                cls._format_log_fields({
                    "id": log_id,
                    "reason": "no_targets",
                    "target_count": 0,
                    "final_count": 0,
                    "elapsed_ms": cls._elapsed_ms(started_at),
                }),
            )
            return KnowledgeRetrievalResult(chunks=[])

        knowledge_ids = [target.knowledge_id for target in targets]
        workspace_ids = [target.workspace_id for target in targets]
        db_knowledge = await cls._get_first_knowledge_async(
            db=db,
            knowledge_id=targets[0].knowledge_id,
            current_user=current_user,
        )
        if not db_knowledge:
            raise KnowledgeRetrievalAccessDenied("The knowledge base does not exist or access is denied")

        document_ids_include = await cls._build_metadata_document_filter_async(
            db=db,
            request=request,
            knowledge_ids=knowledge_ids,
            tenant_id=tenant_id,
            log_id=log_id,
        )
        if document_ids_include == []:
            logger.info(
                "[Retrieval] finish %s",
                cls._format_log_fields({
                    "id": log_id,
                    "reason": "metadata_filter_empty",
                    "target_count": len(targets),
                    "final_count": 0,
                    "elapsed_ms": cls._elapsed_ms(started_at),
                }),
            )
            return KnowledgeRetrievalResult(chunks=[])

        chunks = await cls._retrieve_targets_async(
            db=db,
            request=request,
            targets=targets,
            document_ids_include=document_ids_include,
            tenant_id=tenant_id,
            log_id=log_id,
        )
        if any(target.params.retrieve_type == RetrieveType.Graph for target in targets):
            graph_doc = await cls._retrieve_graph_async(
                db=db,
                request=request,
                knowledge_ids=knowledge_ids,
                workspace_ids=workspace_ids,
                db_knowledge=db_knowledge,
                tenant_id=tenant_id,
            )
            if graph_doc:
                chunks.insert(0, graph_doc)
        chunks = cls._include_document_ids(chunks, document_ids_include)
        logger.info(
            "[Retrieval] finish %s",
            cls._format_log_fields({
                "id": log_id,
                "reason": "ok",
                "target_count": len(targets),
                "document_filter_count": len(document_ids_include or []),
                "final_count": len(chunks),
                "elapsed_ms": cls._elapsed_ms(started_at),
            }),
        )
        return KnowledgeRetrievalResult(chunks=chunks)

    @classmethod
    def _resolve_retrieval_targets(
            cls,
            db: Session,
            request: KnowledgeRetrievalRequest,
            current_user: Any = None,
    ) -> tuple[list[RetrievalTarget], uuid.UUID | None]:
        refs = cls._resolve_retrievable_knowledge_refs(
            db=db,
            request=request,
            current_user=current_user,
        )
        if not refs:
            return [], None

        tenant_id = cls._resolve_tenant_id(
            db=db,
            current_user=current_user,
            workspace_id=getattr(refs[0].knowledge, "workspace_id", None),
        )
        targets = [
            cls._build_retrieval_target(
                db=db,
                request=request,
                ref=ref,
                tenant_id=tenant_id,
            )
            for ref in refs
        ]
        return targets, tenant_id

    @classmethod
    async def _resolve_retrieval_targets_async(
            cls,
            db: AsyncSession,
            request: KnowledgeRetrievalRequest,
            current_user: Any = None,
    ) -> tuple[list[RetrievalTarget], uuid.UUID | None]:
        refs = await cls._resolve_retrievable_knowledge_refs_async(
            db=db,
            request=request,
            current_user=current_user,
        )
        if not refs:
            return [], None

        tenant_id = await cls._resolve_tenant_id_async(
            db=db,
            current_user=current_user,
            workspace_id=getattr(refs[0].knowledge, "workspace_id", None),
        )
        targets = [
            await cls._build_retrieval_target_async(
                db=db,
                request=request,
                ref=ref,
                tenant_id=tenant_id,
            )
            for ref in refs
        ]
        return targets, tenant_id

    @staticmethod
    async def _resolve_tenant_id_async(
            db: AsyncSession,
            current_user: Any = None,
            workspace_id: uuid.UUID | None = None,
    ) -> uuid.UUID | None:
        if current_user is not None:
            tenant_id = getattr(current_user, "tenant_id", None)
            if tenant_id:
                return tenant_id
            workspace_id = workspace_id or getattr(current_user, "current_workspace_id", None)

        if not workspace_id:
            return None
        from app.models.workspace_model import Workspace

        workspace = await db.get(Workspace, workspace_id)
        return workspace.tenant_id if workspace else None

    @classmethod
    def _build_retrieval_target(
            cls,
            db: Session,
            request: KnowledgeRetrievalRequest,
            ref: KnowledgeRetrievalRef,
            tenant_id: uuid.UUID | None,
    ) -> RetrievalTarget:
        knowledge = ref.knowledge
        if not knowledge.embedding_id:
            raise KnowledgeRetrievalConfigError(f"embedding_id config error: {knowledge.id}")
        if not knowledge.reranker_id:
            raise KnowledgeRetrievalConfigError(f"reranker_id config error: {knowledge.id}")

        embedding_config = ModelApiKeyService.get_available_api_key(db, knowledge.embedding_id, tenant_id=tenant_id)
        reranker_config = ModelApiKeyService.get_available_api_key(db, knowledge.reranker_id, tenant_id=tenant_id)
        if not embedding_config:
            raise KnowledgeRetrievalConfigError(f"No embedding api key found for knowledge {knowledge.id}")
        if not reranker_config:
            raise KnowledgeRetrievalConfigError(f"No reranker api key found for knowledge {knowledge.id}")

        return RetrievalTarget(
            knowledge_id=knowledge.id,
            workspace_id=knowledge.workspace_id,
            index_name=ElasticSearchVectorIndexOps.collection_name_for_knowledge(knowledge.id),
            params=cls._build_retrieval_params(request, ref.config),
            embedding_config=ModelApiKeySnapshot.from_api_key(embedding_config),
            reranker_config=ModelApiKeySnapshot.from_api_key(reranker_config),
        )

    @classmethod
    async def _build_retrieval_target_async(
            cls,
            db: AsyncSession,
            request: KnowledgeRetrievalRequest,
            ref: KnowledgeRetrievalRef,
            tenant_id: uuid.UUID | None,
    ) -> RetrievalTarget:
        knowledge = ref.knowledge
        if not knowledge.embedding_id:
            raise KnowledgeRetrievalConfigError(f"embedding_id config error: {knowledge.id}")
        if not knowledge.reranker_id:
            raise KnowledgeRetrievalConfigError(f"reranker_id config error: {knowledge.id}")

        embedding_config = await ModelApiKeyService.get_available_api_key_async(db, knowledge.embedding_id, tenant_id=tenant_id)
        reranker_config = await ModelApiKeyService.get_available_api_key_async(db, knowledge.reranker_id, tenant_id=tenant_id)
        if not embedding_config:
            raise KnowledgeRetrievalConfigError(f"No embedding api key found for knowledge {knowledge.id}")
        if not reranker_config:
            raise KnowledgeRetrievalConfigError(f"No reranker api key found for knowledge {knowledge.id}")

        return RetrievalTarget(
            knowledge_id=knowledge.id,
            workspace_id=knowledge.workspace_id,
            index_name=ElasticSearchVectorIndexOps.collection_name_for_knowledge(knowledge.id),
            params=cls._build_retrieval_params(request, ref.config),
            embedding_config=ModelApiKeySnapshot.from_api_key(embedding_config),
            reranker_config=ModelApiKeySnapshot.from_api_key(reranker_config),
        )

    @staticmethod
    def _resolve_rerank_score_threshold(
            request: KnowledgeRetrievalRequest,
            config: KnowledgeBaseConfig | None = None,
    ) -> float:
        explicit_fields = config.model_fields_set if config else set()
        if (
                config
                and "rerank_score_threshold" in explicit_fields
                and config.rerank_score_threshold is not None
        ):
            return config.rerank_score_threshold
        if config and "vector_similarity_weight" in explicit_fields:
            return config.vector_similarity_weight
        if request.rerank_score_threshold is not None:
            return request.rerank_score_threshold
        if request.vector_similarity_weight is not None:
            return request.vector_similarity_weight
        return 0.1

    @staticmethod
    def _build_retrieval_params(
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
        rerank_score_threshold = KnowledgeRetrievalService._resolve_rerank_score_threshold(request, config)
        top_n = max(top_k, request.top_n or top_k)
        return RetrievalParams(
            similarity_threshold=similarity_threshold,
            vector_similarity_weight=vector_similarity_weight,
            top_k=top_k,
            top_n=top_n,
            retrieve_type=retrieve_type,
            rerank_score_threshold=rerank_score_threshold,
        )

    @staticmethod
    def _is_single_target_type(targets: list[RetrievalTarget], retrieve_type: RetrieveType) -> bool:
        return len(targets) == 1 and targets[0].params.retrieve_type == retrieve_type

    @classmethod
    def _single_hybrid_uses_request_rerank(
            cls,
            request: KnowledgeRetrievalRequest,
            targets: list[RetrievalTarget],
    ) -> bool:
        return request.rerank_id is not None and cls._is_single_target_type(targets, RetrieveType.HYBRID)

    @classmethod
    def _resolve_retrievable_knowledge_refs(
            cls,
            db: Session,
            request: KnowledgeRetrievalRequest,
            current_user: Any = None,
    ) -> list[KnowledgeRetrievalRef]:
        requested_kb_ids, explicit_configs = cls._resolve_requested_kb_ids_and_configs(
            db=db,
            request=request,
            current_user=current_user,
        )
        if not requested_kb_ids:
            return []

        refs: list[KnowledgeRetrievalRef] = []
        ref_positions: dict[uuid.UUID, int] = {}

        def append_refs(items: list[KnowledgeRetrievalRef]) -> None:
            for item in items:
                knowledge_id = item.knowledge.id
                if knowledge_id in ref_positions:
                    if item.config is not None:
                        refs[ref_positions[knowledge_id]] = item
                    continue
                ref_positions[knowledge_id] = len(refs)
                refs.append(item)

        if current_user is None:
            knowledges = (
                db.query(knowledge_model.Knowledge)
                .filter(
                    knowledge_model.Knowledge.id.in_(requested_kb_ids),
                    knowledge_model.Knowledge.status == 1,
                )
                .all()
            )
            knowledge_by_id = {knowledge.id: knowledge for knowledge in knowledges}
            for kb_id in requested_kb_ids:
                knowledge = knowledge_by_id.get(kb_id)
                if not knowledge:
                    continue
                append_refs(cls._expand_knowledge_to_leaf_refs(
                    db=db,
                    knowledge=knowledge,
                    inherited_config=explicit_configs.get(kb_id),
                    explicit_configs=explicit_configs,
                ))
            return refs

        for kb_id in requested_kb_ids:
            private_target = (
                db.query(knowledge_model.Knowledge)
                .filter(
                    knowledge_model.Knowledge.id == kb_id,
                    knowledge_model.Knowledge.workspace_id == current_user.current_workspace_id,
                    knowledge_model.Knowledge.permission_id == knowledge_model.PermissionType.Private,
                    knowledge_model.Knowledge.status == 1,
                )
                .first()
            )
            if private_target:
                append_refs(cls._expand_knowledge_to_leaf_refs(
                    db=db,
                    knowledge=private_target,
                    inherited_config=explicit_configs.get(kb_id),
                    explicit_configs=explicit_configs,
                ))
                continue

            share_target = (
                db.query(knowledge_model.Knowledge)
                .filter(
                    knowledge_model.Knowledge.id == kb_id,
                    knowledge_model.Knowledge.workspace_id == current_user.current_workspace_id,
                    knowledge_model.Knowledge.permission_id == knowledge_model.PermissionType.Share,
                    knowledge_model.Knowledge.status == 1,
                )
                .first()
            )
            if not share_target:
                continue

            filters = [
                knowledgeshare_model.KnowledgeShare.target_kb_id == share_target.id,
                knowledgeshare_model.KnowledgeShare.target_workspace_id == current_user.current_workspace_id,
            ]
            share_items = knowledgeshare_service.get_source_kb_ids_by_target_kb_id(
                db=db,
                filters=filters,
                current_user=current_user,
            )
            for source_kb_id, _source_workspace_id in share_items:
                source_knowledge = knowledge_repository.get_knowledge_by_id(
                    db=db,
                    knowledge_id=source_kb_id,
                )
                append_refs(cls._expand_knowledge_to_leaf_refs(
                    db=db,
                    knowledge=source_knowledge,
                    inherited_config=explicit_configs.get(kb_id),
                    explicit_configs=explicit_configs,
                ))

        return refs

    @classmethod
    async def _resolve_retrievable_knowledge_refs_async(
            cls,
            db: AsyncSession,
            request: KnowledgeRetrievalRequest,
            current_user: Any = None,
    ) -> list[KnowledgeRetrievalRef]:
        requested_kb_ids, explicit_configs = await cls._resolve_requested_kb_ids_and_configs_async(
            db=db,
            request=request,
            current_user=current_user,
        )
        if not requested_kb_ids:
            return []

        refs: list[KnowledgeRetrievalRef] = []
        ref_positions: dict[uuid.UUID, int] = {}

        def append_refs(items: list[KnowledgeRetrievalRef]) -> None:
            for item in items:
                knowledge_id = item.knowledge.id
                if knowledge_id in ref_positions:
                    if item.config is not None:
                        refs[ref_positions[knowledge_id]] = item
                    continue
                ref_positions[knowledge_id] = len(refs)
                refs.append(item)

        if current_user is None:
            result = await db.execute(
                select(knowledge_model.Knowledge).where(
                    knowledge_model.Knowledge.id.in_(requested_kb_ids),
                    knowledge_model.Knowledge.status == 1,
                )
            )
            knowledge_by_id = {knowledge.id: knowledge for knowledge in result.scalars().all()}
            for kb_id in requested_kb_ids:
                knowledge = knowledge_by_id.get(kb_id)
                if not knowledge:
                    continue
                append_refs(await cls._expand_knowledge_to_leaf_refs_async(
                    db=db,
                    knowledge=knowledge,
                    inherited_config=explicit_configs.get(kb_id),
                    explicit_configs=explicit_configs,
                ))
            return refs

        for kb_id in requested_kb_ids:
            private_result = await db.execute(
                select(knowledge_model.Knowledge).where(
                    knowledge_model.Knowledge.id == kb_id,
                    knowledge_model.Knowledge.workspace_id == current_user.current_workspace_id,
                    knowledge_model.Knowledge.permission_id == knowledge_model.PermissionType.Private,
                    knowledge_model.Knowledge.status == 1,
                )
            )
            private_target = private_result.scalars().first()
            if private_target:
                append_refs(await cls._expand_knowledge_to_leaf_refs_async(
                    db=db,
                    knowledge=private_target,
                    inherited_config=explicit_configs.get(kb_id),
                    explicit_configs=explicit_configs,
                ))
                continue

            share_result = await db.execute(
                select(knowledge_model.Knowledge).where(
                    knowledge_model.Knowledge.id == kb_id,
                    knowledge_model.Knowledge.workspace_id == current_user.current_workspace_id,
                    knowledge_model.Knowledge.permission_id == knowledge_model.PermissionType.Share,
                    knowledge_model.Knowledge.status == 1,
                )
            )
            share_target = share_result.scalars().first()
            if not share_target:
                continue

            filters = [
                knowledgeshare_model.KnowledgeShare.target_kb_id == share_target.id,
                knowledgeshare_model.KnowledgeShare.target_workspace_id == current_user.current_workspace_id,
            ]
            share_items = await knowledgeshare_service.get_source_kb_ids_by_target_kb_id_async(
                db=db,
                filters=filters,
                current_user=current_user,
            )
            for source_kb_id, _source_workspace_id in share_items:
                source_knowledge = await knowledge_repository.get_knowledge_by_id_async(
                    db=db,
                    knowledge_id=source_kb_id,
                )
                append_refs(await cls._expand_knowledge_to_leaf_refs_async(
                    db=db,
                    knowledge=source_knowledge,
                    inherited_config=explicit_configs.get(kb_id),
                    explicit_configs=explicit_configs,
                ))

        return refs

    @classmethod
    def _resolve_requested_kb_ids_and_configs(
            cls,
            db: Session,
            request: KnowledgeRetrievalRequest,
            current_user: Any = None,
    ) -> tuple[list[uuid.UUID], dict[uuid.UUID, KnowledgeBaseConfig]]:
        explicit_configs: dict[uuid.UUID, KnowledgeBaseConfig] = {}
        requested_kb_ids = list(request.kb_ids)

        if request.ex_ids:
            if current_user is None:
                raise KnowledgeRetrievalConfigError("current_user is required to resolve ex_ids")
            resolved_ids = knowledge_service.get_knowledge_ids_by_external_ids(
                db=db,
                external_ids=request.ex_ids,
                workspace_id=current_user.current_workspace_id,
                current_user=current_user,
            )
            requested_kb_ids.extend(resolved_ids)

        for config in request.knowledge_bases:
            if config.kb_id in explicit_configs:
                logger.warning("Duplicate KnowledgeBaseConfig found, using the last one: kb_id=%s", config.kb_id)
            explicit_configs[config.kb_id] = config
            requested_kb_ids.append(config.kb_id)

        return cls._unique_ids(requested_kb_ids), explicit_configs

    @classmethod
    async def _resolve_requested_kb_ids_and_configs_async(
            cls,
            db: AsyncSession,
            request: KnowledgeRetrievalRequest,
            current_user: Any = None,
    ) -> tuple[list[uuid.UUID], dict[uuid.UUID, KnowledgeBaseConfig]]:
        explicit_configs: dict[uuid.UUID, KnowledgeBaseConfig] = {}
        requested_kb_ids = list(request.kb_ids)

        if request.ex_ids:
            if current_user is None:
                raise KnowledgeRetrievalConfigError("current_user is required to resolve ex_ids")
            resolved_ids = await knowledge_service.get_knowledge_ids_by_external_ids_async(
                db=db,
                external_ids=request.ex_ids,
                workspace_id=current_user.current_workspace_id,
                current_user=current_user,
            )
            requested_kb_ids.extend(resolved_ids)

        for config in request.knowledge_bases:
            if config.kb_id in explicit_configs:
                logger.warning("Duplicate KnowledgeBaseConfig found, using the last one: kb_id=%s", config.kb_id)
            explicit_configs[config.kb_id] = config
            requested_kb_ids.append(config.kb_id)

        return cls._unique_ids(requested_kb_ids), explicit_configs

    @classmethod
    def _expand_knowledge_to_leaf_refs(
            cls,
            db: Session,
            knowledge: Any,
            inherited_config: KnowledgeBaseConfig | None,
            explicit_configs: dict[uuid.UUID, KnowledgeBaseConfig],
            visited: set[uuid.UUID] | None = None,
    ) -> list[KnowledgeRetrievalRef]:
        if not knowledge or not knowledge.is_active:
            return []
        current_config = explicit_configs.get(knowledge.id) or inherited_config
        if knowledge.is_retrievable_leaf:
            return [KnowledgeRetrievalRef(knowledge=knowledge, config=current_config)]
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

        refs = []
        children = knowledge_repository.get_knowledges_by_parent_id(db=db, parent_id=knowledge.id)
        for child in children:
            if child.workspace_id != knowledge.workspace_id:
                logger.warning(
                    "Skipping child knowledge from another workspace while expanding folder: folder_id=%s, child_id=%s",
                    knowledge.id,
                    child.id,
                )
                continue
            refs.extend(cls._expand_knowledge_to_leaf_refs(
                db=db,
                knowledge=child,
                inherited_config=current_config,
                explicit_configs=explicit_configs,
                visited=visited,
            ))
        return refs

    @classmethod
    async def _expand_knowledge_to_leaf_refs_async(
            cls,
            db: AsyncSession,
            knowledge: Any,
            inherited_config: KnowledgeBaseConfig | None,
            explicit_configs: dict[uuid.UUID, KnowledgeBaseConfig],
            visited: set[uuid.UUID] | None = None,
    ) -> list[KnowledgeRetrievalRef]:
        if not knowledge or not knowledge.is_active:
            return []
        current_config = explicit_configs.get(knowledge.id) or inherited_config
        if knowledge.is_retrievable_leaf:
            return [KnowledgeRetrievalRef(knowledge=knowledge, config=current_config)]
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

        refs = []
        children = await knowledge_repository.get_knowledges_by_parent_id_async(db=db, parent_id=knowledge.id)
        for child in children:
            if child.workspace_id != knowledge.workspace_id:
                logger.warning(
                    "Skipping child knowledge from another workspace while expanding folder: folder_id=%s, child_id=%s",
                    knowledge.id,
                    child.id,
                )
                continue
            refs.extend(await cls._expand_knowledge_to_leaf_refs_async(
                db=db,
                knowledge=child,
                inherited_config=current_config,
                explicit_configs=explicit_configs,
                visited=visited,
            ))
        return refs

    @classmethod
    def _retrieve_targets(
            cls,
            db: Session,
            request: KnowledgeRetrievalRequest,
            targets: list[RetrievalTarget],
            document_ids_include: list[str] | None,
            tenant_id: uuid.UUID | None,
            log_id: str | None = None,
    ) -> list[Any]:
        if not targets:
            return []

        max_workers = max(1, min(len(targets), settings.KNOWLEDGE_RETRIEVAL_MAX_WORKERS or 3))
        if log_id:
            logger.info(
                "[Retrieval] targets %s",
                cls._format_log_fields(cls._build_targets_log_fields(log_id, request, targets, max_workers)),
            )
        if cls._single_hybrid_uses_request_rerank(request, targets):
            candidates = cls._retrieve_single_target(
                request=request,
                target=targets[0],
                document_ids_include=document_ids_include,
                log_id=log_id,
                target_position=1,
                db=db,
                tenant_id=tenant_id,
                use_request_rerank=True,
            )
            return cls._finalize_retrieval_chunks(
                db=db,
                request=request,
                targets=targets,
                chunks=candidates,
                tenant_id=tenant_id,
                log_id=log_id,
            )

        results_by_index: list[list[DocumentChunk]] = [[] for _ in targets]
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="knowledge-retrieval") as executor:
            futures = {
                executor.submit(
                    cls._retrieve_single_target,
                    request,
                    target,
                    document_ids_include,
                    log_id,
                    index + 1,
                ): index
                for index, target in enumerate(targets)
            }
            for future in as_completed(futures):
                index = futures[future]
                target = targets[index]
                try:
                    results_by_index[index] = future.result()
                except Exception:
                    logger.exception(
                        "Knowledge retrieval target failed: knowledge_id=%s index=%s retrieve_type=%s",
                        target.knowledge_id,
                        target.index_name,
                        target.params.retrieve_type,
                    )
                    raise

        candidates = [chunk for target_chunks in results_by_index for chunk in target_chunks]
        return cls._finalize_retrieval_chunks(
            db=db,
            request=request,
            targets=targets,
            chunks=candidates,
            tenant_id=tenant_id,
            log_id=log_id,
        )

    @classmethod
    async def _retrieve_targets_async(
            cls,
            db: AsyncSession,
            request: KnowledgeRetrievalRequest,
            targets: list[RetrievalTarget],
            document_ids_include: list[str] | None,
            tenant_id: uuid.UUID | None,
            log_id: str | None = None,
    ) -> list[Any]:
        if not targets:
            return []

        max_workers = max(1, min(len(targets), settings.KNOWLEDGE_RETRIEVAL_MAX_WORKERS or 3))
        if log_id:
            logger.info(
                "[Retrieval] targets %s",
                cls._format_log_fields(cls._build_targets_log_fields(log_id, request, targets, max_workers)),
            )

        if cls._single_hybrid_uses_request_rerank(request, targets):
            candidates = await cls._retrieve_single_hybrid_with_request_rerank_async(
                db=db,
                request=request,
                target=targets[0],
                document_ids_include=document_ids_include,
                tenant_id=tenant_id,
                log_id=log_id,
                target_position=1,
            )
            return await cls._finalize_retrieval_chunks_async(
                db=db,
                request=request,
                targets=targets,
                chunks=candidates,
                tenant_id=tenant_id,
                log_id=log_id,
            )

        semaphore = asyncio.Semaphore(max_workers)
        results_by_index: list[list[DocumentChunk]] = [[] for _ in targets]

        async def retrieve_one(index: int, target: RetrievalTarget) -> None:
            async with semaphore:
                try:
                    results_by_index[index] = await asyncio.to_thread(
                        cls._retrieve_single_target,
                        request,
                        target,
                        document_ids_include,
                        log_id,
                        index + 1,
                    )
                except Exception:
                    logger.exception(
                        "Knowledge retrieval target failed: knowledge_id=%s index=%s retrieve_type=%s",
                        target.knowledge_id,
                        target.index_name,
                        target.params.retrieve_type,
                    )
                    raise

        await asyncio.gather(
            *(retrieve_one(index, target) for index, target in enumerate(targets))
        )

        candidates = [chunk for target_chunks in results_by_index for chunk in target_chunks]
        return await cls._finalize_retrieval_chunks_async(
            db=db,
            request=request,
            targets=targets,
            chunks=candidates,
            tenant_id=tenant_id,
            log_id=log_id,
        )

    @classmethod
    async def _retrieve_single_hybrid_with_request_rerank_async(
            cls,
            db: AsyncSession,
            request: KnowledgeRetrievalRequest,
            target: RetrievalTarget,
            document_ids_include: list[str] | None,
            tenant_id: uuid.UUID | None,
            log_id: str | None = None,
            target_position: int = 0,
    ) -> list[DocumentChunk]:
        started_at = time.perf_counter()

        def retrieve_candidates() -> tuple[list[DocumentChunk], list[DocumentChunk], list[DocumentChunk]]:
            vector_service = ElasticSearchVectorFactory.init_vector_from_configs(
                index_name=target.index_name,
                embedding_config=target.embedding_config,
                reranker_config=target.reranker_config,
            )
            local_request = request.model_copy(update={
                "similarity_threshold": target.params.similarity_threshold,
                "vector_similarity_weight": target.params.vector_similarity_weight,
                "top_k": target.params.top_k,
                "top_n": target.params.top_n,
                "retrieve_type": target.params.retrieve_type,
            })
            vector_chunks = cls._search_vector(
                vector_service,
                local_request,
                target.index_name,
                document_ids_include,
                topk=target.params.top_n,
            )
            full_text_chunks = cls._search_full_text(
                vector_service,
                local_request,
                target.index_name,
                document_ids_include,
                topk=target.params.top_n,
            )
            unique_chunks = cls._deduplicate_chunks(vector_chunks + full_text_chunks)
            return vector_chunks, full_text_chunks, unique_chunks

        vector_chunks, full_text_chunks, unique_chunks = await asyncio.to_thread(retrieve_candidates)
        chunks = await cls.rerank_documents_async(
            db=db,
            rerank_id=request.rerank_id,
            query=request.query,
            docs=unique_chunks,
            top_k=target.params.top_k,
            tenant_id=tenant_id,
        )
        chunks = [
            chunk
            for chunk in chunks
            if (chunk.metadata or {}).get("score", 0) > target.params.rerank_score_threshold
        ]
        if log_id:
            logger.info(
                "[Retrieval] target_done %s",
                cls._format_log_fields(cls._build_target_done_log_fields(
                    log_id=log_id,
                    target=target,
                    target_position=target_position,
                    vector_count=len(vector_chunks),
                    full_text_count=len(full_text_chunks),
                    merged_count=len(unique_chunks),
                    result_count=len(chunks),
                    elapsed_ms=cls._elapsed_ms(started_at),
                    local_rerank=True,
                )),
            )
        return chunks

    @classmethod
    def _retrieve_single_target(
            cls,
            request: KnowledgeRetrievalRequest,
            target: RetrievalTarget,
            document_ids_include: list[str] | None,
            log_id: str | None = None,
            target_position: int = 0,
            db: Session | None = None,
            tenant_id: uuid.UUID | None = None,
            use_request_rerank: bool = False,
    ) -> list[DocumentChunk]:
        started_at = time.perf_counter()
        vector_service = ElasticSearchVectorFactory.init_vector_from_configs(
            index_name=target.index_name,
            embedding_config=target.embedding_config,
            reranker_config=target.reranker_config,
        )
        local_request = request.model_copy(update={
            "similarity_threshold": target.params.similarity_threshold,
            "vector_similarity_weight": target.params.vector_similarity_weight,
            "top_k": target.params.top_k,
            "top_n": target.params.top_n,
            "retrieve_type": target.params.retrieve_type,
        })

        if target.params.retrieve_type == RetrieveType.PARTICIPLE:
            chunks = cls._search_full_text(
                vector_service,
                local_request,
                target.index_name,
                document_ids_include,
                topk=target.params.top_k,
                apply_score_threshold=False,
            )
            if log_id:
                logger.info(
                    "[Retrieval] target_done %s",
                    cls._format_log_fields(cls._build_target_done_log_fields(
                        log_id=log_id,
                        target=target,
                        target_position=target_position,
                        vector_count=0,
                        full_text_count=len(chunks),
                        merged_count=len(chunks),
                        result_count=len(chunks),
                        elapsed_ms=cls._elapsed_ms(started_at),
                        local_rerank=False,
                    )),
                )
            return chunks
        if target.params.retrieve_type == RetrieveType.SEMANTIC:
            chunks = cls._search_vector(
                vector_service,
                local_request,
                target.index_name,
                document_ids_include,
                topk=target.params.top_k,
            )
            if log_id:
                logger.info(
                    "[Retrieval] target_done %s",
                    cls._format_log_fields(cls._build_target_done_log_fields(
                        log_id=log_id,
                        target=target,
                        target_position=target_position,
                        vector_count=len(chunks),
                        full_text_count=0,
                        merged_count=len(chunks),
                        result_count=len(chunks),
                        elapsed_ms=cls._elapsed_ms(started_at),
                        local_rerank=False,
                    )),
                )
            return chunks

        vector_chunks = cls._search_vector(
            vector_service,
            local_request,
            target.index_name,
            document_ids_include,
            topk=target.params.top_n,
        )
        full_text_chunks = cls._search_full_text(
            vector_service,
            local_request,
            target.index_name,
            document_ids_include,
            topk=target.params.top_n,
        )
        unique_chunks = cls._deduplicate_chunks(vector_chunks + full_text_chunks)
        # if len(unique_chunks) <= target.params.top_k:
        #     return unique_chunks
        if use_request_rerank and request.rerank_id:
            chunks = cls.rerank_documents(
                db=db,
                rerank_id=request.rerank_id,
                query=request.query,
                docs=unique_chunks,
                top_k=target.params.top_k,
                tenant_id=tenant_id,
            )
        else:
            chunks = vector_service.rerank(
                query=request.query,
                docs=unique_chunks,
                top_k=target.params.top_k,
            )
        chunks = [
            chunk
            for chunk in chunks
            if (chunk.metadata or {}).get("score", 0) > target.params.rerank_score_threshold
        ]
        if log_id:
            logger.info(
                "[Retrieval] target_done %s",
                cls._format_log_fields(cls._build_target_done_log_fields(
                    log_id=log_id,
                    target=target,
                    target_position=target_position,
                    vector_count=len(vector_chunks),
                    full_text_count=len(full_text_chunks),
                    merged_count=len(unique_chunks),
                    result_count=len(chunks),
                    elapsed_ms=cls._elapsed_ms(started_at),
                    local_rerank=True,
                )),
            )
        return chunks

    @classmethod
    def _finalize_retrieval_chunks(
            cls,
            db: Session,
            request: KnowledgeRetrievalRequest,
            targets: list[RetrievalTarget],
            chunks: list[DocumentChunk],
            tenant_id: uuid.UUID | None,
            log_id: str | None = None,
    ) -> list[DocumentChunk]:
        candidate_count = len(chunks)
        unique_chunks = cls._deduplicate_chunks(chunks)
        if not unique_chunks:
            if log_id:
                logger.info(
                    "[Retrieval] finalize %s",
                    cls._format_log_fields({
                        "id": log_id,
                        "candidates": candidate_count,
                        "deduped": 0,
                        "global_rerank": False,
                        "threshold": "none",
                        "filtered": 0,
                        "result_count": 0,
                    }),
                )
            return []

        single_hybrid_uses_request_rerank = cls._single_hybrid_uses_request_rerank(request, targets)
        needs_global_rerank = len(targets) > 1 or (
                request.rerank_id is not None and not single_hybrid_uses_request_rerank
        )
        if request.rerank_id and not single_hybrid_uses_request_rerank:
            ranked_chunks = cls.rerank_documents(
                db=db,
                rerank_id=request.rerank_id,
                query=request.query,
                docs=unique_chunks,
                top_k=request.top_k,
                tenant_id=tenant_id,
            )
        elif needs_global_rerank:
            first_target = targets[0]
            vector_service = ElasticSearchVectorFactory.init_vector_from_configs(
                index_name=first_target.index_name,
                embedding_config=first_target.embedding_config,
                reranker_config=first_target.reranker_config,
            )
            ranked_chunks = vector_service.rerank(
                query=request.query,
                docs=unique_chunks,
                top_k=request.top_k,
            )
        else:
            ranked_chunks = sorted(
                unique_chunks,
                key=lambda chunk: (chunk.metadata or {}).get("score", 0),
                reverse=True,
            )

        if needs_global_rerank:
            threshold = cls._resolve_rerank_score_threshold(request)
            filtered_chunks = [
                chunk
                for chunk in ranked_chunks
                if (chunk.metadata or {}).get("score", 0) > threshold
            ]
        else:
            threshold = None
            filtered_chunks = ranked_chunks
        result_chunks = filtered_chunks[:request.top_k]
        if log_id:
            logger.info(
                "[Retrieval] finalize %s",
                cls._format_log_fields({
                    "id": log_id,
                    "candidates": candidate_count,
                    "deduped": len(unique_chunks),
                    "global_rerank": needs_global_rerank,
                    "ranked": len(ranked_chunks),
                    "threshold": threshold if threshold is not None else "none",
                    "filtered": len(filtered_chunks),
                    "result_count": len(result_chunks),
                }),
            )
        return result_chunks

    @classmethod
    async def _finalize_retrieval_chunks_async(
            cls,
            db: AsyncSession,
            request: KnowledgeRetrievalRequest,
            targets: list[RetrievalTarget],
            chunks: list[DocumentChunk],
            tenant_id: uuid.UUID | None,
            log_id: str | None = None,
    ) -> list[DocumentChunk]:
        candidate_count = len(chunks)
        unique_chunks = cls._deduplicate_chunks(chunks)
        if not unique_chunks:
            if log_id:
                logger.info(
                    "[Retrieval] finalize %s",
                    cls._format_log_fields({
                        "id": log_id,
                        "candidates": candidate_count,
                        "deduped": 0,
                        "global_rerank": False,
                        "threshold": "none",
                        "filtered": 0,
                        "result_count": 0,
                    }),
                )
            return []

        single_hybrid_uses_request_rerank = cls._single_hybrid_uses_request_rerank(request, targets)
        needs_global_rerank = len(targets) > 1 or (
                request.rerank_id is not None and not single_hybrid_uses_request_rerank
        )
        if request.rerank_id and not single_hybrid_uses_request_rerank:
            ranked_chunks = await cls.rerank_documents_async(
                db=db,
                rerank_id=request.rerank_id,
                query=request.query,
                docs=unique_chunks,
                top_k=request.top_k,
                tenant_id=tenant_id,
            )
        elif needs_global_rerank:
            first_target = targets[0]
            vector_service = ElasticSearchVectorFactory.init_vector_from_configs(
                index_name=first_target.index_name,
                embedding_config=first_target.embedding_config,
                reranker_config=first_target.reranker_config,
            )
            ranked_chunks = await asyncio.to_thread(
                vector_service.rerank,
                query=request.query,
                docs=unique_chunks,
                top_k=request.top_k,
            )
        else:
            ranked_chunks = sorted(
                unique_chunks,
                key=lambda chunk: (chunk.metadata or {}).get("score", 0),
                reverse=True,
            )

        if needs_global_rerank:
            threshold = cls._resolve_rerank_score_threshold(request)
            filtered_chunks = [
                chunk
                for chunk in ranked_chunks
                if (chunk.metadata or {}).get("score", 0) > threshold
            ]
        else:
            threshold = None
            filtered_chunks = ranked_chunks
        result_chunks = filtered_chunks[:request.top_k]
        if log_id:
            logger.info(
                "[Retrieval] finalize %s",
                cls._format_log_fields({
                    "id": log_id,
                    "candidates": candidate_count,
                    "deduped": len(unique_chunks),
                    "global_rerank": needs_global_rerank,
                    "ranked": len(ranked_chunks),
                    "threshold": threshold if threshold is not None else "none",
                    "filtered": len(filtered_chunks),
                    "result_count": len(result_chunks),
                }),
            )
        return result_chunks

    @staticmethod
    def _resolve_global_score_threshold(
            request: KnowledgeRetrievalRequest,
            targets: list[RetrievalTarget],
            used_rerank: bool,
    ) -> float:
        if used_rerank:
            return KnowledgeRetrievalService._resolve_rerank_score_threshold(request)

        retrieve_types = {target.params.retrieve_type for target in targets}
        if retrieve_types == {RetrieveType.PARTICIPLE}:
            return request.similarity_threshold
        if retrieve_types == {RetrieveType.SEMANTIC}:
            return request.vector_similarity_weight
        return min(request.similarity_threshold, request.vector_similarity_weight)

    @classmethod
    def _retrieve_by_type(
            cls,
            db: Session,
            request: KnowledgeRetrievalRequest,
            knowledge_ids: list[uuid.UUID],
            workspace_ids: list[uuid.UUID],
            db_knowledge: Any,
            document_ids_include: list[str] | None,
            tenant_id: uuid.UUID | None,
    ) -> list[Any]:
        vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)
        indices = ",".join(f"Vector_index_{knowledge_id}_Node".lower() for knowledge_id in knowledge_ids)

        top_n = request.top_k

        if request.retrieve_type == RetrieveType.PARTICIPLE:
            return cls._search_full_text(vector_service, request, indices, document_ids_include, topk=top_n)
        if request.retrieve_type == RetrieveType.SEMANTIC:
            return cls._search_vector(vector_service, request, indices, document_ids_include, topk=top_n)

        top_n = request.top_n

        vector_chunks = cls._search_vector(vector_service, request, indices, document_ids_include, topk=top_n)
        full_text_chunks = cls._search_full_text(vector_service, request, indices, document_ids_include, topk=top_n)
        unique_chunks = cls._deduplicate_chunks(vector_chunks + full_text_chunks)
        logger.debug(f"Retrieved {len(unique_chunks)} chunks")
        if not unique_chunks:
            chunks = []
        else:
            chunks = cls._rerank_hybrid_chunks(db, request, vector_service, unique_chunks, tenant_id=tenant_id)

        if request.retrieve_type == RetrieveType.Graph:
            graph_doc = cls._retrieve_graph(
                db=db,
                request=request,
                knowledge_ids=knowledge_ids,
                workspace_ids=workspace_ids,
                db_knowledge=db_knowledge,
                tenant_id=tenant_id,
            )
            if graph_doc:
                chunks.insert(0, graph_doc)

        return chunks

    @staticmethod
    def _search_vector(
            vector_service: ElasticSearchVector,
            request: KnowledgeRetrievalRequest,
            indices: str,
            document_ids_include: list[str] | None,
            topk: Optional[int] = -1,
            apply_score_threshold: bool = True,
    ) -> list[DocumentChunk]:
        return vector_service.search_by_vector(
            query=request.query,
            top_k=topk,
            indices=indices,
            score_threshold=request.vector_similarity_weight if apply_score_threshold else None,
            document_ids_include=document_ids_include,
            file_names_filter=request.file_names_filter,
            resolve_parents=True,
        )

    @staticmethod
    def _search_full_text(
            vector_service: ElasticSearchVector,
            request: KnowledgeRetrievalRequest,
            indices: str,
            document_ids_include: list[str] | None,
            topk: Optional[int] = -1,
            apply_score_threshold: bool = True,
    ) -> list[DocumentChunk]:
        return vector_service.search_by_full_text(
            query=request.query,
            top_k=topk,
            indices=indices,
            score_threshold=request.similarity_threshold if apply_score_threshold else None,
            document_ids_include=document_ids_include,
            file_names_filter=request.file_names_filter,
            resolve_parents=True,
        )

    @classmethod
    def _rerank_hybrid_chunks(
            cls,
            db: Session,
            request: KnowledgeRetrievalRequest,
            vector_service: ElasticSearchVector,
            chunks: list[DocumentChunk],
            tenant_id: uuid.UUID | None,
    ) -> list[DocumentChunk]:
        if request.rerank_id:
            reranked_chunks = cls.rerank_documents(
                db=db,
                rerank_id=request.rerank_id,
                query=request.query,
                docs=chunks,
                top_k=request.top_k,
                tenant_id=tenant_id,
            )
        else:
            reranked_chunks = vector_service.rerank(
                query=request.query,
                docs=chunks,
                top_k=request.top_k,
            )

        logger.debug(f"[rerank]rerank_id:{request.rerank_id}, returned {len(reranked_chunks)} docs")

        rerank_score_threshold = cls._resolve_rerank_score_threshold(request)

        filtered_chunks = [
            chunk
            for chunk in reranked_chunks
            if (chunk.metadata or {}).get("score", 0) > rerank_score_threshold
        ]
        return filtered_chunks[:request.top_k]

    @staticmethod
    def _retrieve_graph(
            db: Session,
            request: KnowledgeRetrievalRequest,
            knowledge_ids: list[uuid.UUID],
            workspace_ids: list[uuid.UUID],
            db_knowledge: Any,
            tenant_id: uuid.UUID | None,
    ) -> Any | None:
        from app.core.rag.common.settings import kg_retriever

        llm_key = ModelApiKeyService.get_available_api_key(db, db_knowledge.llm_id, tenant_id=tenant_id)
        emb_key = ModelApiKeyService.get_available_api_key(db, db_knowledge.embedding_id, tenant_id=tenant_id)
        doc = kg_retriever.retrieval(
            question=request.query,
            workspace_ids=[str(workspace_id) for workspace_id in workspace_ids],
            kb_ids=[str(knowledge_id) for knowledge_id in knowledge_ids],
            emb_mdl=KnowledgeRetrievalService._build_embedding_model(emb_key),
            llm=KnowledgeRetrievalService._build_chat_model(llm_key),
        )
        if doc and str(doc.get("page_content", "")).strip():
            return doc
        return None

    @staticmethod
    async def _retrieve_graph_async(
            db: AsyncSession,
            request: KnowledgeRetrievalRequest,
            knowledge_ids: list[uuid.UUID],
            workspace_ids: list[uuid.UUID],
            db_knowledge: Any,
            tenant_id: uuid.UUID | None,
    ) -> Any | None:
        from app.core.rag.common.settings import kg_retriever

        llm_key = await ModelApiKeyService.get_available_api_key_async(db, db_knowledge.llm_id, tenant_id=tenant_id)
        emb_key = await ModelApiKeyService.get_available_api_key_async(db, db_knowledge.embedding_id, tenant_id=tenant_id)
        doc = await asyncio.to_thread(
            kg_retriever.retrieval,
            question=request.query,
            workspace_ids=[str(workspace_id) for workspace_id in workspace_ids],
            kb_ids=[str(knowledge_id) for knowledge_id in knowledge_ids],
            emb_mdl=KnowledgeRetrievalService._build_embedding_model(emb_key),
            llm=KnowledgeRetrievalService._build_chat_model(llm_key),
        )
        if doc and str(doc.get("page_content", "")).strip():
            return doc
        return None

    @classmethod
    def _resolve_retrievable_knowledge_ids(
            cls,
            db: Session,
            request: KnowledgeRetrievalRequest,
            current_user: Any = None,
    ) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
        requested_kb_ids = cls._unique_ids(request.kb_ids)
        if request.ex_ids:
            if current_user is None:
                raise KnowledgeRetrievalConfigError("current_user is required to resolve ex_ids")
            resolved_ids = knowledge_service.get_knowledge_ids_by_external_ids(
                db=db,
                external_ids=request.ex_ids,
                workspace_id=current_user.current_workspace_id,
                current_user=current_user,
            )
            requested_kb_ids = cls._unique_ids(requested_kb_ids + resolved_ids)

        if not requested_kb_ids:
            return [], []

        if current_user is None:
            knowledges = (
                db.query(knowledge_model.Knowledge)
                .filter(
                    knowledge_model.Knowledge.id.in_(requested_kb_ids),
                    knowledge_model.Knowledge.status == 1,
                )
                .all()
            )
            return cls._expand_knowledges_to_leaf_kbs(db=db, knowledges=knowledges)

        return cls._resolve_accessible_chunk_kbs(
            db=db,
            kb_ids=requested_kb_ids,
            current_user=current_user,
        )

    @staticmethod
    def _unique_ids(values: list[uuid.UUID]) -> list[uuid.UUID]:
        return list(dict.fromkeys(values))

    @classmethod
    def _expand_knowledges_to_leaf_kbs(
            cls,
            db: Session,
            knowledges: list[Any],
    ) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
        knowledge_ids = []
        workspace_ids = []
        for knowledge in knowledges:
            expanded_ids, expanded_workspace_ids = cls._expand_folder_to_leaf_kbs(
                db=db,
                knowledge=knowledge,
            )
            knowledge_ids.extend(expanded_ids)
            workspace_ids.extend(expanded_workspace_ids)
        return cls._deduplicate_knowledge_pairs(knowledge_ids, workspace_ids)

    @classmethod
    def _expand_folder_to_leaf_kbs(
            cls,
            db: Session,
            knowledge: Any,
            visited: set[uuid.UUID] | None = None,
    ) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
        if not knowledge or not knowledge.is_active:
            return [], []
        if knowledge.is_retrievable_leaf:
            return [knowledge.id], [knowledge.workspace_id]
        if not knowledge.is_folder:
            return [], []

        if visited is None:
            visited = set()
        if knowledge.id in visited:
            logger.warning(
                "Detected cyclic knowledge folder while expanding retrieval candidates: knowledge_id=%s",
                knowledge.id,
            )
            return [], []
        visited.add(knowledge.id)

        knowledge_ids = []
        workspace_ids = []
        children = knowledge_repository.get_knowledges_by_parent_id(db=db, parent_id=knowledge.id)
        for child in children:
            if child.workspace_id != knowledge.workspace_id:
                logger.warning(
                    "Skipping child knowledge from another workspace while expanding folder: folder_id=%s, child_id=%s",
                    knowledge.id,
                    child.id,
                )
                continue
            expanded_ids, expanded_workspace_ids = cls._expand_folder_to_leaf_kbs(
                db=db,
                knowledge=child,
                visited=visited,
            )
            knowledge_ids.extend(expanded_ids)
            workspace_ids.extend(expanded_workspace_ids)

        return cls._deduplicate_knowledge_pairs(knowledge_ids, workspace_ids)

    @staticmethod
    def _deduplicate_knowledge_pairs(
            knowledge_ids: list[uuid.UUID],
            workspace_ids: list[uuid.UUID],
    ) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
        seen = set()
        deduplicated_ids = []
        deduplicated_workspace_ids = []
        for knowledge_id, workspace_id in zip(knowledge_ids, workspace_ids):
            if knowledge_id in seen:
                continue
            seen.add(knowledge_id)
            deduplicated_ids.append(knowledge_id)
            deduplicated_workspace_ids.append(workspace_id)
        return deduplicated_ids, deduplicated_workspace_ids

    @classmethod
    def _resolve_accessible_chunk_kbs(
            cls,
            db: Session,
            kb_ids: list[uuid.UUID],
            current_user: Any,
    ) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
        private_targets = (
            db.query(knowledge_model.Knowledge)
            .filter(
                knowledge_model.Knowledge.id.in_(kb_ids),
                knowledge_model.Knowledge.workspace_id == current_user.current_workspace_id,
                knowledge_model.Knowledge.permission_id == knowledge_model.PermissionType.Private,
                knowledge_model.Knowledge.status == 1,
            )
            .all()
        )
        knowledge_ids, workspace_ids = cls._expand_knowledges_to_leaf_kbs(
            db=db,
            knowledges=private_targets,
        )

        share_targets = (
            db.query(knowledge_model.Knowledge)
            .filter(
                knowledge_model.Knowledge.id.in_(kb_ids),
                knowledge_model.Knowledge.workspace_id == current_user.current_workspace_id,
                knowledge_model.Knowledge.permission_id == knowledge_model.PermissionType.Share,
                knowledge_model.Knowledge.status == 1,
            )
            .all()
        )
        if share_targets:
            share_target_ids = [target.id for target in share_targets]
            filters = [
                knowledgeshare_model.KnowledgeShare.target_kb_id.in_(share_target_ids),
                knowledgeshare_model.KnowledgeShare.target_workspace_id == current_user.current_workspace_id,
            ]
            share_items = knowledgeshare_service.get_source_kb_ids_by_target_kb_id(
                db=db,
                filters=filters,
                current_user=current_user,
            )
            for source_kb_id, _source_workspace_id in share_items:
                source_knowledge = knowledge_repository.get_knowledge_by_id(
                    db=db,
                    knowledge_id=source_kb_id,
                )
                expanded_ids, expanded_workspace_ids = cls._expand_folder_to_leaf_kbs(
                    db=db,
                    knowledge=source_knowledge,
                )
                knowledge_ids.extend(expanded_ids)
                workspace_ids.extend(expanded_workspace_ids)

        return cls._deduplicate_knowledge_pairs(knowledge_ids, workspace_ids)

    @staticmethod
    def _get_first_knowledge(
            db: Session,
            knowledge_id: uuid.UUID,
            current_user: Any = None,
    ) -> Any:
        if current_user is not None:
            return knowledge_service.get_knowledge_by_id(
                db=db,
                knowledge_id=knowledge_id,
                current_user=current_user,
            )
        return knowledge_repository.get_knowledge_by_id(db=db, knowledge_id=knowledge_id)

    @staticmethod
    async def _get_first_knowledge_async(
            db: AsyncSession,
            knowledge_id: uuid.UUID,
            current_user: Any = None,
    ) -> Any:
        if current_user is not None:
            return await knowledge_service.get_knowledge_by_id_async(
                db=db,
                knowledge_id=knowledge_id,
                current_user=current_user,
            )
        return await knowledge_repository.get_knowledge_by_id_async(db=db, knowledge_id=knowledge_id)

    @classmethod
    def _build_metadata_document_filter(
            cls,
            db: Session,
            request: KnowledgeRetrievalRequest,
            knowledge_ids: list[uuid.UUID],
            tenant_id: uuid.UUID | None,
            log_id: str | None = None,
    ) -> list[str] | None:
        if request.metadata_filter_mode == MetadataFilterMode.DISABLED:
            if log_id:
                logger.info(
                    "[Retrieval] metadata_filter %s",
                    cls._format_log_fields(cls._build_metadata_filter_log_fields(
                        log_id=log_id,
                        request=request,
                        effective=False,
                        reason="mode_disabled",
                        document_count=None,
                    )),
                )
            return None

        if request.metadata_filter_mode == MetadataFilterMode.MANUAL and not request.metadata_filters:
            if log_id:
                logger.info(
                    "[Retrieval] metadata_filter %s",
                    cls._format_log_fields(cls._build_metadata_filter_log_fields(
                        log_id=log_id,
                        request=request,
                        effective=False,
                        reason="no_filters",
                        document_count=None,
                    )),
                )
            return None

        metadata_defs_by_kb = {
            knowledge_id: KnowledgeMetadataService.get_metadata_defs_for_filtering(db, knowledge_id)
            for knowledge_id in knowledge_ids
        }
        common_metadata_defs = cls._get_common_metadata_defs(metadata_defs_by_kb)
        filter_groups = cls._build_metadata_filter_groups(
            db=db,
            request=request,
            knowledge_ids=knowledge_ids,
            common_metadata_defs=common_metadata_defs,
                tenant_id=tenant_id,
        )
        if not filter_groups:
            logger.warning("[MetadataFilter] No common metadata fields matched; skipping metadata filter")
            if log_id:
                logger.info(
                    "[Retrieval] metadata_filter %s",
                    cls._format_log_fields(cls._build_metadata_filter_log_fields(
                        log_id=log_id,
                        request=request,
                        effective=False,
                        reason="no_effective_groups",
                        document_count=None,
                        common_fields=len(common_metadata_defs),
                        effective_groups=0,
                    )),
                )
            return None

        document_ids = set()
        engine = MetadataFilterEngine(db)
        for knowledge_id in knowledge_ids:
            matched_ids = engine.execute(
                knowledge_id=knowledge_id,
                filter_groups=filter_groups,
                metadata_defs=metadata_defs_by_kb[knowledge_id],
            )
            document_ids.update(matched_ids)
        if log_id:
            logger.info(
                "[Retrieval] metadata_filter %s",
                cls._format_log_fields(cls._build_metadata_filter_log_fields(
                    log_id=log_id,
                    request=request,
                    effective=True,
                    reason="matched",
                    document_count=len(document_ids),
                    common_fields=len(common_metadata_defs),
                    effective_groups=len(filter_groups),
                )),
            )
        return [str(document_id) for document_id in document_ids]

    @classmethod
    async def _build_metadata_document_filter_async(
            cls,
            db: AsyncSession,
            request: KnowledgeRetrievalRequest,
            knowledge_ids: list[uuid.UUID],
            tenant_id: uuid.UUID | None,
            log_id: str | None = None,
    ) -> list[str] | None:
        if request.metadata_filter_mode == MetadataFilterMode.DISABLED:
            if log_id:
                logger.info(
                    "[Retrieval] metadata_filter %s",
                    cls._format_log_fields(cls._build_metadata_filter_log_fields(
                        log_id=log_id,
                        request=request,
                        effective=False,
                        reason="mode_disabled",
                        document_count=None,
                    )),
                )
            return None

        if request.metadata_filter_mode == MetadataFilterMode.MANUAL and not request.metadata_filters:
            if log_id:
                logger.info(
                    "[Retrieval] metadata_filter %s",
                    cls._format_log_fields(cls._build_metadata_filter_log_fields(
                        log_id=log_id,
                        request=request,
                        effective=False,
                        reason="no_filters",
                        document_count=None,
                    )),
                )
            return None

        metadata_defs_by_kb = {
            knowledge_id: await KnowledgeMetadataService.get_metadata_defs_for_filtering_async(db, knowledge_id)
            for knowledge_id in knowledge_ids
        }
        common_metadata_defs = cls._get_common_metadata_defs(metadata_defs_by_kb)
        filter_groups = await cls._build_metadata_filter_groups_async(
            db=db,
            request=request,
            knowledge_ids=knowledge_ids,
            common_metadata_defs=common_metadata_defs,
            tenant_id=tenant_id,
        )
        if not filter_groups:
            logger.warning("[MetadataFilter] No common metadata fields matched; skipping metadata filter")
            if log_id:
                logger.info(
                    "[Retrieval] metadata_filter %s",
                    cls._format_log_fields(cls._build_metadata_filter_log_fields(
                        log_id=log_id,
                        request=request,
                        effective=False,
                        reason="no_effective_groups",
                        document_count=None,
                        common_fields=len(common_metadata_defs),
                        effective_groups=0,
                    )),
                )
            return None

        document_ids = set()
        engine = MetadataFilterEngine(db)
        for knowledge_id in knowledge_ids:
            matched_ids = await engine.execute_async(
                knowledge_id=knowledge_id,
                filter_groups=filter_groups,
                metadata_defs=metadata_defs_by_kb[knowledge_id],
            )
            document_ids.update(matched_ids)
        if log_id:
            logger.info(
                "[Retrieval] metadata_filter %s",
                cls._format_log_fields(cls._build_metadata_filter_log_fields(
                    log_id=log_id,
                    request=request,
                    effective=True,
                    reason="matched",
                    document_count=len(document_ids),
                    common_fields=len(common_metadata_defs),
                    effective_groups=len(filter_groups),
                )),
            )
        return [str(document_id) for document_id in document_ids]

    @classmethod
    def _build_metadata_filter_groups(
            cls,
            db: Session,
            request: KnowledgeRetrievalRequest,
            knowledge_ids: list[uuid.UUID],
            common_metadata_defs: dict[str, dict],
            tenant_id: uuid.UUID | None,
    ) -> list[EngineFilterGroup]:
        if request.metadata_filter_mode == MetadataFilterMode.DISABLED:
            return []

        if request.metadata_filter_mode == MetadataFilterMode.MANUAL:
            if not request.metadata_filters:
                return []
            return cls._build_common_filter_groups(
                request.metadata_filters,
                set(common_metadata_defs.keys()),
            )

        if request.metadata_filter_mode == MetadataFilterMode.AUTO:
            # 节点（knowledge node）在 auto 模式下已用配置好的模型提取出过滤条件，
            # 直接采用，跳过 service 用 knowledge.llm_id 的重复提取。
            if request.metadata_filters:
                return cls._build_common_filter_groups(
                    request.metadata_filters,
                    set(common_metadata_defs.keys()),
                )

            if not common_metadata_defs:
                return []
            llm = cls._build_metadata_auto_filter_llm(
                db=db,
                knowledge_id=knowledge_ids[0],
                tenant_id=tenant_id,
            )
            if not llm:
                logger.warning("[MetadataAutoFilter] LLM is unavailable; skipping metadata filter")
                return []
            return MetadataAutoFilterService.generate_filter_groups(
                query=request.query,
                metadata_defs=common_metadata_defs,
                llm=llm,
            )

        raise BusinessException(
            f"metadata_filter_mode 不支持: {request.metadata_filter_mode}",
            code=BizCode.INVALID_PARAMETER,
        )

    @classmethod
    async def _build_metadata_filter_groups_async(
            cls,
            db: AsyncSession,
            request: KnowledgeRetrievalRequest,
            knowledge_ids: list[uuid.UUID],
            common_metadata_defs: dict[str, dict],
            tenant_id: uuid.UUID | None,
    ) -> list[EngineFilterGroup]:
        if request.metadata_filter_mode == MetadataFilterMode.DISABLED:
            return []

        if request.metadata_filter_mode == MetadataFilterMode.MANUAL:
            if not request.metadata_filters:
                return []
            return cls._build_common_filter_groups(
                request.metadata_filters,
                set(common_metadata_defs.keys()),
            )

        if request.metadata_filter_mode == MetadataFilterMode.AUTO:
            if request.metadata_filters:
                return cls._build_common_filter_groups(
                    request.metadata_filters,
                    set(common_metadata_defs.keys()),
                )

            if not common_metadata_defs:
                return []
            llm = await cls._build_metadata_auto_filter_llm_async(
                db=db,
                knowledge_id=knowledge_ids[0],
                tenant_id=tenant_id,
            )
            if not llm:
                logger.warning("[MetadataAutoFilter] LLM is unavailable; skipping metadata filter")
                return []
            return await asyncio.to_thread(
                MetadataAutoFilterService.generate_filter_groups,
                query=request.query,
                metadata_defs=common_metadata_defs,
                llm=llm,
            )

        raise BusinessException(
            f"metadata_filter_mode 不支持: {request.metadata_filter_mode}",
            code=BizCode.INVALID_PARAMETER,
        )

    @classmethod
    def _build_metadata_auto_filter_llm(
            cls,
            db: Session,
            knowledge_id: uuid.UUID,
            tenant_id: uuid.UUID | None,
    ) -> RedBearLLM | None:
        knowledge = knowledge_repository.get_knowledge_by_id(db=db, knowledge_id=knowledge_id)
        if not knowledge or not knowledge.llm_id:
            return None

        try:
            config = ModelConfigService.get_model_by_id(db=db, model_id=knowledge.llm_id)
        except BusinessException:
            return None

        api_key = ModelApiKeyService.get_available_api_key(db, knowledge.llm_id, tenant_id=tenant_id)
        if not api_key:
            return None

        # 参数折叠进 RedBearModelConfig.extra_params（与 knowledge 节点 auto 模式、LLM 节点一致）。
        # 此兜底路径用知识库 llm_id、无 completion_params，保留原默认 temperature=0 行为。
        rb_config = RedBearModelConfig(
            model_name=api_key.model_name,
            provider=api_key.provider,
            api_key=api_key.api_key,
            base_url=api_key.api_base,
            is_omni=api_key.is_omni,
            capability=api_key.capability or [],
            extra_params={"temperature": 0},
        )
        return RedBearLLM(rb_config, type=ModelType(config.type))

    @classmethod
    async def _build_metadata_auto_filter_llm_async(
            cls,
            db: AsyncSession,
            knowledge_id: uuid.UUID,
            tenant_id: uuid.UUID | None,
    ) -> RedBearLLM | None:
        knowledge = await knowledge_repository.get_knowledge_by_id_async(db=db, knowledge_id=knowledge_id)
        if not knowledge or not knowledge.llm_id:
            return None

        config = await db.get(ModelConfig, knowledge.llm_id)
        if not config:
            return None

        api_key = await ModelApiKeyService.get_available_api_key_async(db, knowledge.llm_id, tenant_id=tenant_id)
        if not api_key:
            return None

        rb_config = RedBearModelConfig(
            model_name=api_key.model_name,
            provider=api_key.provider,
            api_key=api_key.api_key,
            base_url=api_key.api_base,
            is_omni=api_key.is_omni,
            capability=api_key.capability or [],
            extra_params={"temperature": 0},
        )
        return RedBearLLM(rb_config, type=ModelType(config.type))

    @staticmethod
    def _get_common_metadata_defs(metadata_defs_by_kb: dict[Any, dict[str, dict]]) -> dict[str, dict]:
        field_names = set()
        for metadata_defs in metadata_defs_by_kb.values():
            field_names.update(metadata_defs.keys())

        common_defs = {}
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

    @staticmethod
    def _get_common_metadata_fields(metadata_defs_by_kb: dict[Any, dict[str, dict]]) -> set[str]:
        return set(KnowledgeRetrievalService._get_common_metadata_defs(metadata_defs_by_kb).keys())

    @staticmethod
    def _build_common_filter_groups(metadata_filters: list[Any], common_fields: set[str]) -> list[EngineFilterGroup]:
        filter_groups = []
        for group in metadata_filters:
            conditions = [
                EngineFilterCondition(field=condition.field, operator=condition.operator, value=condition.value)
                for condition in group.conditions
                if condition.field in common_fields
            ]
            if conditions:
                filter_groups.append(EngineFilterGroup(conditions=conditions, logic=group.logic))
        return filter_groups

    @staticmethod
    def _deduplicate_chunks(chunks: list[DocumentChunk]) -> list[DocumentChunk]:
        seen_keys = set()
        result = []
        for chunk in chunks:
            metadata = chunk.metadata or {}
            doc_id = metadata.get("doc_id")
            document_id = metadata.get("document_id")
            sort_id = metadata.get("sort_id")
            if doc_id:
                dedupe_key = ("doc_id", doc_id)
            elif document_id is not None and sort_id is not None:
                dedupe_key = ("document_sort", document_id, sort_id)
            else:
                dedupe_key = ("content", hash(chunk.page_content))
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            result.append(chunk)
        return result

    @staticmethod
    def _include_document_ids(
            chunks: list[Any],
            document_ids_include: list[str] | None,
    ) -> list[Any]:
        if document_ids_include is None:
            return chunks
        include_ids = set(document_ids_include)
        return [
            chunk
            for chunk in chunks
            if chunk.metadata.get("document_id") in include_ids
        ]

    @staticmethod
    def _exclude_document_ids(
            chunks: list[Any],
            document_ids_filter: list[str] | None,
    ) -> list[Any]:
        return KnowledgeRetrievalService._include_document_ids(chunks, document_ids_filter)

    @staticmethod
    def _build_chat_model(api_key: ModelApiKey) -> Base:
        return Base(
            key=api_key.api_key,
            model_name=api_key.model_name,
            base_url=api_key.api_base,
        )

    @staticmethod
    def _build_embedding_model(api_key: ModelApiKey) -> OpenAIEmbed:
        return OpenAIEmbed(
            key=api_key.api_key,
            model_name=api_key.model_name,
            base_url=api_key.api_base,
        )

    @staticmethod
    def rerank_documents(
            db: Session,
            rerank_id: uuid.UUID,
            query: str,
            docs: list[DocumentChunk],
            top_k: int,
            tenant_id: uuid.UUID | None = None,
    ) -> list[DocumentChunk]:
        if not docs:
            raise ValueError("retrieval chunks cannot be empty")
        if top_k <= 0:
            raise ValueError("top_k must be a positive integer")
        try:
            api_config = ModelApiKeyService.get_available_api_key(db, rerank_id, tenant_id=tenant_id)
            if not api_config:
                raise ValueError("模型配置缺少 API Key")
            reranker = RedBearRerank(
                RedBearModelConfig(
                    model_name=api_config.model_name,
                    provider=api_config.provider,
                    api_key=api_config.api_key,
                    base_url=api_config.api_base,
                )
            )
            documents = [
                Document(
                    page_content=chunk_retrieval_content(doc),
                    metadata=doc.metadata or {},
                )
                for doc in docs
            ]
            reranked_docs = list(reranker.compress_documents(documents, query, top_n=top_k))
            reranked_docs.sort(
                key=lambda item: item.metadata.get("relevance_score", 0),
                reverse=True,
            )
            result = []
            for item in reranked_docs[:top_k]:
                for doc in docs:
                    if doc.metadata['doc_id'] == item.metadata['doc_id']:
                        doc.metadata["score"] = item.metadata["relevance_score"]
                        result.append(doc)
            return result
        except Exception as exc:
            logger.warning(f"Rerank failed, falling back to original results: {str(exc)}")
            for doc in docs[:top_k]:
                if doc.metadata is not None and "score" not in doc.metadata:
                    doc.metadata["score"] = 0.5
            return docs[:top_k]

    @staticmethod
    async def rerank_documents_async(
            db: AsyncSession,
            rerank_id: uuid.UUID,
            query: str,
            docs: list[DocumentChunk],
            top_k: int,
            tenant_id: uuid.UUID | None = None,
    ) -> list[DocumentChunk]:
        """Async DB wrapper around the synchronous rerank provider."""
        if not docs:
            raise ValueError("retrieval chunks cannot be empty")
        if top_k <= 0:
            raise ValueError("top_k must be a positive integer")
        try:
            api_config = await ModelApiKeyService.get_available_api_key_async(db, rerank_id, tenant_id=tenant_id)
            if not api_config:
                raise ValueError("模型配置缺少 API Key")

            def run_rerank() -> list[DocumentChunk]:
                reranker = RedBearRerank(
                    RedBearModelConfig(
                        model_name=api_config.model_name,
                        provider=api_config.provider,
                        api_key=api_config.api_key,
                        base_url=api_config.api_base,
                    )
                )
                documents = [
                    Document(
                        page_content=chunk_retrieval_content(doc),
                        metadata=doc.metadata or {},
                    )
                    for doc in docs
                ]
                reranked_docs = list(reranker.compress_documents(documents, query, top_n=top_k))
                reranked_docs.sort(
                    key=lambda item: item.metadata.get("relevance_score", 0),
                    reverse=True,
                )
                result = []
                for item in reranked_docs[:top_k]:
                    for doc in docs:
                        if doc.metadata['doc_id'] == item.metadata['doc_id']:
                            doc.metadata["score"] = item.metadata["relevance_score"]
                            result.append(doc)
                return result

            return await asyncio.to_thread(run_rerank)
        except Exception as exc:
            logger.warning(f"Rerank failed, falling back to original results: {str(exc)}")
            for doc in docs[:top_k]:
                if doc.metadata is not None and "score" not in doc.metadata:
                    doc.metadata["score"] = 0.5
            return docs[:top_k]
