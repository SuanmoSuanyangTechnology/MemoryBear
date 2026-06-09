import logging
import uuid
from typing import Any

from langchain_core.documents import Document
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.models import RedBearRerank
from app.core.models.base import RedBearModelConfig
from app.core.rag.llm.chat_model import Base
from app.core.rag.llm.embedding_model import OpenAIEmbed
from app.core.rag.metadata.filter_engine import (
    FilterCondition as EngineFilterCondition,
    FilterGroup as EngineFilterGroup,
    MetadataFilterEngine,
)
from app.core.rag.models.chunk import DocumentChunk
from app.core.rag.vdb.elasticsearch.elasticsearch_vector import (
    ElasticSearchVector,
    ElasticSearchVectorFactory,
)
from app.models import knowledge_model, knowledgeshare_model
from app.models.models_model import ModelApiKey
from app.repositories import knowledge_repository
from app.schemas.chunk_schema import RetrieveType
from app.schemas.knowledge_metadata_schema import MetadataFilterMode
from app.schemas.knowledge_retrieval_schema import KnowledgeRetrievalRequest, KnowledgeRetrievalResult
from app.services import knowledge_service, knowledgeshare_service
from app.services.knowledge_metadata_service import KnowledgeMetadataService
from app.services.model_service import ModelApiKeyService, ModelConfigService

logger = logging.getLogger(__name__)


class KnowledgeRetrievalAccessDenied(Exception):
    pass


class KnowledgeRetrievalConfigError(Exception):
    pass


class KnowledgeRetrievalService:
    @classmethod
    def retrieve(
            cls,
            db: Session,
            request: KnowledgeRetrievalRequest,
            current_user: Any = None,
    ) -> KnowledgeRetrievalResult:
        knowledge_ids, workspace_ids = cls._resolve_retrievable_knowledge_ids(
            db=db,
            request=request,
            current_user=current_user,
        )
        if not knowledge_ids:
            return KnowledgeRetrievalResult(chunks=[])

        db_knowledge = cls._get_first_knowledge(
            db=db,
            knowledge_id=knowledge_ids[0],
            current_user=current_user,
        )
        if not db_knowledge:
            raise KnowledgeRetrievalAccessDenied("The knowledge base does not exist or access is denied")

        document_ids_filter = cls._build_metadata_document_filter(
            db=db,
            request=request,
            knowledge_ids=knowledge_ids,
        )
        chunks = cls._retrieve_by_type(
            db=db,
            request=request,
            knowledge_ids=knowledge_ids,
            workspace_ids=workspace_ids,
            db_knowledge=db_knowledge,
            document_ids_filter=document_ids_filter,
        )
        chunks = cls._exclude_document_ids(chunks, document_ids_filter)
        return KnowledgeRetrievalResult(chunks=chunks)

    @classmethod
    def _retrieve_by_type(
            cls,
            db: Session,
            request: KnowledgeRetrievalRequest,
            knowledge_ids: list[uuid.UUID],
            workspace_ids: list[uuid.UUID],
            db_knowledge: Any,
            document_ids_filter: list[str] | None,
    ) -> list[Any]:
        vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)
        indices = ",".join(f"Vector_index_{knowledge_id}_Node".lower() for knowledge_id in knowledge_ids)

        if request.retrieve_type == RetrieveType.PARTICIPLE:
            return cls._search_full_text(vector_service, request, indices, document_ids_filter)
        if request.retrieve_type == RetrieveType.SEMANTIC:
            return cls._search_vector(vector_service, request, indices, document_ids_filter)

        vector_chunks = cls._search_vector(vector_service, request, indices, document_ids_filter)
        full_text_chunks = cls._search_full_text(vector_service, request, indices, document_ids_filter)
        unique_chunks = cls._deduplicate_chunks(vector_chunks + full_text_chunks)
        logger.debug(f"Retrieved {len(unique_chunks)} chunks")
        if not unique_chunks:
            chunks = []
        else:
            chunks = cls._rerank_hybrid_chunks(db, request, vector_service, unique_chunks)

        if request.retrieve_type == RetrieveType.Graph:
            graph_doc = cls._retrieve_graph(
                db=db,
                request=request,
                knowledge_ids=knowledge_ids,
                workspace_ids=workspace_ids,
                db_knowledge=db_knowledge,
            )
            if graph_doc:
                chunks.insert(0, graph_doc)

        return chunks

    @staticmethod
    def _search_vector(
            vector_service: ElasticSearchVector,
            request: KnowledgeRetrievalRequest,
            indices: str,
            document_ids_filter: list[str] | None,
    ) -> list[DocumentChunk]:
        return vector_service.search_by_vector(
            query=request.query,
            top_k=request.top_k,
            indices=indices,
            score_threshold=request.vector_similarity_weight,
            document_ids_filter=document_ids_filter,
            file_names_filter=request.file_names_filter,
            resolve_parents=True,
        )

    @staticmethod
    def _search_full_text(
            vector_service: ElasticSearchVector,
            request: KnowledgeRetrievalRequest,
            indices: str,
            document_ids_filter: list[str] | None,
    ) -> list[DocumentChunk]:
        return vector_service.search_by_full_text(
            query=request.query,
            top_k=request.top_k,
            indices=indices,
            score_threshold=request.similarity_threshold,
            document_ids_filter=document_ids_filter,
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
    ) -> list[DocumentChunk]:
        if request.rerank_id:
            reranked_chunks = cls.rerank_documents(
                db=db,
                rerank_id=request.rerank_id,
                query=request.query,
                docs=chunks,
                top_k=request.top_k,
            )
        else:
            reranked_chunks = vector_service.rerank(
                query=request.query,
                docs=chunks,
                top_k=request.top_k,
            )

        logger.debug(f"[rerank]rerank_id:{request.rerank_id}, returned {len(reranked_chunks)} docs")

        rerank_score_threshold = request.rerank_score_threshold or request.vector_similarity_weight or 0.1

        return [
            chunk
            for chunk in reranked_chunks
            if (chunk.metadata or {}).get("score", 0) > rerank_score_threshold
        ]

    @staticmethod
    def _retrieve_graph(
            db: Session,
            request: KnowledgeRetrievalRequest,
            knowledge_ids: list[uuid.UUID],
            workspace_ids: list[uuid.UUID],
            db_knowledge: Any,
    ) -> Any | None:
        from app.core.rag.common.settings import kg_retriever

        llm_key = ModelApiKeyService.get_available_api_key(db, db_knowledge.llm_id)
        emb_key = ModelApiKeyService.get_available_api_key(db, db_knowledge.embedding_id)
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
            rows = (
                db.query(knowledge_model.Knowledge.id, knowledge_model.Knowledge.workspace_id)
                .filter(
                    knowledge_model.Knowledge.id.in_(requested_kb_ids),
                    knowledge_model.Knowledge.chunk_num > 0,
                    knowledge_model.Knowledge.status == 1,
                )
                .all()
            )
            return [row[0] for row in rows], [row[1] for row in rows]

        return cls._resolve_accessible_chunk_kbs(
            db=db,
            kb_ids=requested_kb_ids,
            current_user=current_user,
        )

    @staticmethod
    def _unique_ids(values: list[uuid.UUID]) -> list[uuid.UUID]:
        return list(dict.fromkeys(values))

    @classmethod
    def _resolve_accessible_chunk_kbs(
            cls,
            db: Session,
            kb_ids: list[uuid.UUID],
            current_user: Any,
    ) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
        filters = [
            knowledge_model.Knowledge.id.in_(kb_ids),
            knowledge_model.Knowledge.workspace_id == current_user.current_workspace_id,
            knowledge_model.Knowledge.permission_id == knowledge_model.PermissionType.Private,
            knowledge_model.Knowledge.chunk_num > 0,
            knowledge_model.Knowledge.status == 1,
        ]
        private_items = knowledge_service.get_chunked_knowledgeids(
            db=db,
            filters=filters,
            current_user=current_user,
        )
        knowledge_ids = [item[0] for item in private_items]
        workspace_ids = [item[1] for item in private_items]

        filters = [
            knowledge_model.Knowledge.id.in_(kb_ids),
            knowledge_model.Knowledge.workspace_id == current_user.current_workspace_id,
            knowledge_model.Knowledge.permission_id == knowledge_model.PermissionType.Share,
            knowledge_model.Knowledge.chunk_num > 0,
            knowledge_model.Knowledge.status == 1,
        ]
        share_targets = knowledge_service.get_chunked_knowledgeids(
            db=db,
            filters=filters,
            current_user=current_user,
        )
        if share_targets:
            filters = [
                knowledgeshare_model.KnowledgeShare.target_kb_id.in_(kb_ids),
                knowledgeshare_model.KnowledgeShare.target_workspace_id == current_user.current_workspace_id,
            ]
            share_items = knowledgeshare_service.get_source_kb_ids_by_target_kb_id(
                db=db,
                filters=filters,
                current_user=current_user,
            )
            knowledge_ids.extend([item[0] for item in share_items])
            workspace_ids.extend([item[1] for item in share_items])

        return knowledge_ids, workspace_ids

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

    @classmethod
    def _build_metadata_document_filter(
            cls,
            db: Session,
            request: KnowledgeRetrievalRequest,
            knowledge_ids: list[uuid.UUID],
    ) -> list[str] | None:
        if not request.metadata_filters:
            return None

        if request.metadata_filter_mode != MetadataFilterMode.MANUAL:
            raise BusinessException(
                "metadata_filter_mode 暂仅支持 'manual'",
                code=BizCode.INVALID_PARAMETER,
            )

        metadata_defs_by_kb = {
            knowledge_id: KnowledgeMetadataService.get_metadata_defs_for_filtering(db, knowledge_id)
            for knowledge_id in knowledge_ids
        }
        common_fields = cls._get_common_metadata_fields(metadata_defs_by_kb)
        filter_groups = cls._build_common_filter_groups(request.metadata_filters, common_fields)
        if not filter_groups:
            logger.warning("[MetadataFilter] No common metadata fields matched; skipping metadata filter")
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
        return [str(document_id) for document_id in document_ids]

    @staticmethod
    def _get_common_metadata_fields(metadata_defs_by_kb: dict[Any, dict[str, dict]]) -> set[str]:
        field_names = set()
        for metadata_defs in metadata_defs_by_kb.values():
            field_names.update(metadata_defs.keys())

        common_fields = set()
        for field_name in field_names:
            field_types = set()
            all_have_field = True
            for metadata_defs in metadata_defs_by_kb.values():
                if field_name not in metadata_defs:
                    all_have_field = False
                    break
                field_types.add(metadata_defs[field_name]["type"])
            if all_have_field and len(field_types) == 1:
                common_fields.add(field_name)
        return common_fields

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
        seen_doc_ids = set()
        result = []
        for chunk in chunks:
            doc_id = chunk.metadata["doc_id"]
            if doc_id in seen_doc_ids:
                continue
            seen_doc_ids.add(doc_id)
            result.append(chunk)
        return result

    @staticmethod
    def _exclude_document_ids(
            chunks: list[Any],
            document_ids_filter: list[str] | None,
    ) -> list[Any]:
        if not document_ids_filter:
            return chunks
        exclude_ids = set(document_ids_filter)
        return [
            chunk
            for chunk in chunks
            if chunk.metadata.get("document_id") not in exclude_ids
        ]

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
    ) -> list[DocumentChunk]:
        if not rerank_id:
            raise ValueError("rerank_id cannot be empty")
        if not docs:
            raise ValueError("retrieval chunks cannot be empty")
        if top_k <= 0:
            raise ValueError("top_k must be a positive integer")
        try:
            config = ModelConfigService.get_model_by_id(db=db, model_id=rerank_id)
            api_config: ModelApiKey = config.api_keys[0]
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
                    page_content=doc.page_content,
                    metadata=doc.metadata or {},
                )
                for doc in docs
            ]
            reranked_docs = list(reranker.compress_documents(documents, query, topn=top_k))
            reranked_docs.sort(
                key=lambda item: item.metadata.get("relevance_score", 0),
                reverse=True,
            )
            result = []
            for item in reranked_docs[:top_k]:
                for doc in docs:
                    if doc.page_content == item.page_content:
                        doc.metadata["score"] = item.metadata["relevance_score"]
                        result.append(doc)
            return result
        except Exception as exc:
            logger.warning(f"Rerank failed, falling back to original results: {str(exc)}")
            for doc in docs[:top_k]:
                if doc.metadata is not None and "score" not in doc.metadata:
                    doc.metadata["score"] = 0.5
            return docs[:top_k]
