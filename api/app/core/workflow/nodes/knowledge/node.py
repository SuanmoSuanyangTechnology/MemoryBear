import logging
from typing import Any

from app.core.rag.vdb.elasticsearch.elasticsearch_vector import ElasticSearchVectorFactory
from app.core.workflow.nodes.base_node import BaseNode, WorkflowState
from app.core.workflow.nodes.knowledge import KnowledgeRetrievalNodeConfig
from app.db import get_db_context
from app.models import knowledge_model, knowledgeshare_model
from app.repositories import knowledge_repository
from app.schemas.chunk_schema import RetrieveType
from app.services import knowledge_service, knowledgeshare_service

logger = logging.getLogger(__name__)


class KnowledgeRetrievalNode(BaseNode):
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any]):
        super().__init__(node_config, workflow_config)
        self.typed_config = KnowledgeRetrievalNodeConfig(**self.config)

    async def execute(self, state: WorkflowState) -> Any:
        query = self._render_template(self.typed_config.query, state)
        with get_db_context():
            filters = [
                knowledge_model.Knowledge.id.in_(self.typed_config.kb_ids),
                knowledge_model.Knowledge.permission_id == knowledge_model.PermissionType.Private,
                knowledge_model.Knowledge.chunk_num > 0,
                knowledge_model.Knowledge.status == 1
            ]
            existing_ids = knowledge_repository.get_chunked_knowledgeids(
                db=db,
                filters=filters
            )
            filters = [
                knowledge_model.Knowledge.id.in_(self.typed_config.kb_ids),
                knowledge_model.Knowledge.permission_id == knowledge_model.PermissionType.Share,
                knowledge_model.Knowledge.chunk_num > 0,
                knowledge_model.Knowledge.status == 1
            ]
            share_ids = knowledge_service.knowledge_repository.get_chunked_knowledgeids(
                db=db,
                filters=filters
            )
            if share_ids:
                filters = [
                    knowledgeshare_model.KnowledgeShare.target_kb_id.in_(self.typed_config.kb_ids)
                ]
                items = knowledgeshare_service.knowledgeshare_repository.get_source_kb_ids_by_target_kb_id(
                    db=db,
                    filters=filters
                )
                existing_ids.extend(items)

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
                    return [chunk.model_dump() for chunk in rs]
                case RetrieveType.SEMANTIC:
                    rs = vector_service.search_by_vector(query=query, top_k=self.typed_config.top_k,
                                                         indices=indices,
                                                         score_threshold=self.typed_config.vector_similarity_weight)
                    return [chunk.model_dump() for chunk in rs]
                case _:
                    rs1 = vector_service.search_by_vector(query=query, top_k=self.typed_config.top_k,
                                                          indices=indices,
                                                          score_threshold=self.typed_config.vector_similarity_weight)
                    rs2 = vector_service.search_by_full_text(query=query, top_k=self.typed_config.top_k,
                                                             indices=indices,
                                                             score_threshold=self.typed_config.similarity_threshold)
                    # Efficient deduplication
                    seen_ids = set()
                    unique_rs = []
                    for doc in rs1 + rs2:
                        if doc.metadata["doc_id"] not in seen_ids:
                            seen_ids.add(doc.metadata["doc_id"])
                            unique_rs.append(doc)
                    rs = vector_service.rerank(query=query, docs=unique_rs, top_k=self.typed_config.top_k)
                    return [chunk.model_dump() for chunk in rs]
