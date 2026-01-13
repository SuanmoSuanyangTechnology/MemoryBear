import logging
import uuid
from typing import Any

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.models import RedBearRerank, RedBearModelConfig
from app.core.rag.vdb.elasticsearch.elasticsearch_vector import ElasticSearchVectorFactory
from app.core.workflow.nodes.base_node import BaseNode, WorkflowState
from app.core.workflow.nodes.knowledge import KnowledgeRetrievalNodeConfig
from app.db import get_db_read
from app.models import knowledge_model, knowledgeshare_model, ModelType
from app.repositories import knowledge_repository
from app.schemas.chunk_schema import RetrieveType
from app.services import knowledge_service, knowledgeshare_service
from app.services.model_service import ModelConfigService

logger = logging.getLogger(__name__)


class KnowledgeRetrievalNode(BaseNode):
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any]):
        super().__init__(node_config, workflow_config)
        self.typed_config = KnowledgeRetrievalNodeConfig(**self.config)

    @staticmethod
    def _build_kb_filter(kb_ids: list[uuid.UUID], permission: knowledge_model.PermissionType):
        """
        Build SQLAlchemy filter conditions for querying valid knowledge bases.

        Filters ensure:
        - Knowledge base ID is in the provided list
        - Permission type matches (Private / Share)
        - Knowledge base has indexed chunks
        - Knowledge base is in active status

        Args:
            kb_ids (list[UUID]): Candidate knowledge base IDs.
            permission (PermissionType): Required permission type.

        Returns:
            list: SQLAlchemy filter expressions.
        """
        return [
            knowledge_model.Knowledge.id.in_(kb_ids),
            knowledge_model.Knowledge.permission_id == permission,
            knowledge_model.Knowledge.chunk_num > 0,
            knowledge_model.Knowledge.status == 1
        ]

    @staticmethod
    def _deduplicate_docs(*doc_lists):
        """
        Deduplicate documents from multiple retrieval result lists
        while preserving original order.

        Deduplication is based on `doc.metadata["doc_id"]`.

        Args:
            *doc_lists: Multiple lists of retrieved documents.

        Returns:
            list: Deduplicated document list.
        """
        seen = set()
        unique = []
        for doc in (doc for lst in doc_lists for doc in lst):
            doc_id = doc.metadata["doc_id"]
            if doc_id not in seen:
                seen.add(doc_id)
                unique.append(doc)
        return unique

    def _get_existing_kb_ids(self, db, kb_ids):
        """
        Resolve all accessible and valid knowledge base IDs for retrieval.

        This includes:
        - Private knowledge bases owned by the user
        - Shared knowledge bases
        - Source knowledge bases mapped via knowledge sharing relationships

        Args:
            db: Database session.
            kb_ids (list[UUID]): Knowledge base IDs from node configuration.

        Returns:
            list[UUID]: Final list of valid knowledge base IDs.
        """
        filters = self._build_kb_filter(kb_ids, knowledge_model.PermissionType.Private)

        existing_ids = knowledge_repository.get_chunked_knowledgeids(
            db=db,
            filters=filters
        )

        filters = self._build_kb_filter(kb_ids, knowledge_model.PermissionType.Share)

        share_ids = knowledge_service.knowledge_repository.get_chunked_knowledgeids(
            db=db,
            filters=filters
        )

        if share_ids:
            filters = [
                knowledgeshare_model.KnowledgeShare.target_kb_id.in_(kb_ids)
            ]
            items = knowledgeshare_service.knowledgeshare_repository.get_source_kb_ids_by_target_kb_id(
                db=db,
                filters=filters
            )
            existing_ids.extend(items)
        return existing_ids

    def get_reranker_model(self) -> RedBearRerank:
        """
        Retrieve and initialize a RedBear reranker model based on configuration.

        Raises:
            BusinessException: If configuration is missing or API keys are not set.
            RuntimeError: If the configured model is not of type RERANK.
        """
        with get_db_read() as db:
            config = ModelConfigService.get_model_by_id(db=db, model_id=self.typed_config.reranker_id)

            if not config:
                raise BusinessException("Configured model does not exist", BizCode.NOT_FOUND)

            if not config.api_keys or len(config.api_keys) == 0:
                raise BusinessException("Model configuration is missing API Key", BizCode.INVALID_PARAMETER)

            # 在 Session 关闭前提取所有需要的数据
            api_config = config.api_keys[0]
            model_name = api_config.model_name
            provider = api_config.provider
            api_key = api_config.api_key
            api_base = api_config.api_base
            model_type = config.type

        if model_type != ModelType.RERANK:
            raise RuntimeError("Model is not a reranker")

        reranker = RedBearRerank(
            RedBearModelConfig(
                model_name=model_name,
                provider=provider,
                api_key=api_key,
                base_url=api_base,
            )
        )
        return reranker

    async def execute(self, state: WorkflowState) -> Any:
        """
        Execute the knowledge retrieval workflow node.

        Steps:
        1. Render query template using workflow state
        2. Resolve accessible knowledge bases
        3. Initialize Elasticsearch vector service
        4. Perform retrieval based on configured retrieve type
        5. Deduplicate results if necessary
        6. Serialize and return retrieved chunks

        Args:
            state (WorkflowState): Current workflow execution state.

        Returns:
            Any: List of retrieved knowledge chunks (dict format).

        Raises:
            RuntimeError: If no valid knowledge base is found or access is denied.
        """
        query = self._render_template(self.typed_config.query, state)
        with get_db_read() as db:
            knowledge_bases = self.typed_config.knowledge_bases
            existing_ids = self._get_existing_kb_ids(db, [kb.kb_id for kb in knowledge_bases])

            if not existing_ids:
                raise RuntimeError("Knowledge base retrieval failed: the knowledge base does not exist.")

            rs = []
            for kb_config in knowledge_bases:
                db_knowledge = knowledge_repository.get_knowledge_by_id(db=db, knowledge_id=kb_config.kb_id)
                if not db_knowledge:
                    raise RuntimeError("The knowledge base does not exist or access is denied.")

                vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)
                indices = f"Vector_index_{kb_config.kb_id}_Node".lower()
                match kb_config.retrieve_type:
                    case RetrieveType.PARTICIPLE:
                        rs.extend(vector_service.search_by_full_text(query=query, top_k=kb_config.top_k,
                                                                     indices=indices,
                                                                     score_threshold=kb_config.similarity_threshold))
                    case RetrieveType.SEMANTIC:
                        rs.extend(vector_service.search_by_vector(query=query, top_k=kb_config.top_k,
                                                                  indices=indices,
                                                                  score_threshold=kb_config.vector_similarity_weight))
                    case RetrieveType.HYBRID:
                        rs1 = vector_service.search_by_vector(query=query, top_k=kb_config.top_k,
                                                              indices=indices,
                                                              score_threshold=kb_config.vector_similarity_weight)
                        rs2 = vector_service.search_by_full_text(query=query, top_k=kb_config.top_k,
                                                                 indices=indices,
                                                                 score_threshold=kb_config.similarity_threshold)

                        # Deduplicate hy    brid retrieval results
                        unique_rs = self._deduplicate_docs(rs1, rs2)
                        if not unique_rs:
                            continue
                        if self.typed_config.reranker_id:
                            vector_service.reranker = self.get_reranker_model()
                            rs.extend(vector_service.rerank(query=query, docs=unique_rs, top_k=kb_config.top_k))
                        else:
                            rs.extend(sorted(
                                unique_rs,
                                key=lambda d: d.metadata.get("score", 0),
                                reverse=True
                            )[:kb_config.top_k])
                    case _:
                        raise RuntimeError("Unknown retrieval type")
            if not rs:
                return []
            if self.typed_config.reranker_id:
                vector_service.reranker = self.get_reranker_model()
                final_rs = vector_service.rerank(query=query, docs=rs, top_k=self.typed_config.reranker_top_k)
            else:
                final_rs = sorted(
                    rs,
                    key=lambda d: d.metadata.get("score", 0),
                    reverse=True
                )[:self.typed_config.reranker_top_k]

            logger.info(
                f"Node {self.node_id}: knowledge base retrieval completed, results count: {len(final_rs)}"
            )
            return [chunk.page_content for chunk in final_rs]
