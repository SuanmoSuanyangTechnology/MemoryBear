import logging
import uuid
from typing import Any

from app.core.rag.vdb.elasticsearch.elasticsearch_vector import ElasticSearchVectorFactory
from app.core.workflow.nodes.base_node import BaseNode, WorkflowState
from app.core.workflow.nodes.knowledge import KnowledgeRetrievalNodeConfig
from app.db import get_db_read
from app.models import knowledge_model, knowledgeshare_model
from app.repositories import knowledge_repository
from app.schemas.chunk_schema import RetrieveType
from app.services import knowledge_service, knowledgeshare_service

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
            existing_ids = self._get_existing_kb_ids(db, self.typed_config.kb_ids)

            if not existing_ids:
                raise RuntimeError("Knowledge base retrieval failed: the knowledge base does not exist.")

            kb_id = existing_ids[0]
            uuid_strs = [f"Vector_index_{kb_id}_Node".lower() for kb_id in existing_ids]
            indices = ",".join(uuid_strs)

            db_knowledge = knowledge_repository.get_knowledge_by_id(db=db, knowledge_id=kb_id)
            if not db_knowledge:
                raise RuntimeError("The knowledge base does not exist or access is denied.")

            vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)

            match self.typed_config.retrieve_type:
                case RetrieveType.PARTICIPLE:
                    rs = vector_service.search_by_full_text(query=query, top_k=self.typed_config.top_k,
                                                            indices=indices,
                                                            score_threshold=self.typed_config.similarity_threshold)
                case RetrieveType.SEMANTIC:
                    rs = vector_service.search_by_vector(query=query, top_k=self.typed_config.top_k,
                                                         indices=indices,
                                                         score_threshold=self.typed_config.vector_similarity_weight)
                case _:
                    rs1 = vector_service.search_by_vector(query=query, top_k=self.typed_config.top_k,
                                                          indices=indices,
                                                          score_threshold=self.typed_config.vector_similarity_weight)
                    rs2 = vector_service.search_by_full_text(query=query, top_k=self.typed_config.top_k,
                                                             indices=indices,
                                                             score_threshold=self.typed_config.similarity_threshold)
                    # Deduplicate hybrid retrieval results
                    unique_rs = self._deduplicate_docs(rs1, rs2)
                    rs = vector_service.rerank(query=query, docs=unique_rs, top_k=self.typed_config.top_k)
            return [chunk.model_dump() for chunk in rs]
