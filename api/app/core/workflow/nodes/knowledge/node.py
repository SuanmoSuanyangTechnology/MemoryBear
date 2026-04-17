import asyncio
import logging
import uuid
from typing import Any

from langchain_core.documents import Document

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.models import RedBearRerank, RedBearModelConfig
from app.core.rag.llm.chat_model import Base
from app.core.rag.llm.embedding_model import OpenAIEmbed
from app.core.rag.models.chunk import DocumentChunk
from app.core.rag.vdb.elasticsearch.elasticsearch_vector import ElasticSearchVectorFactory
from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.knowledge import KnowledgeRetrievalNodeConfig
from app.core.workflow.variable.base_variable import VariableType
from app.db import get_db_read
from app.models import knowledge_model, ModelType
from app.repositories import knowledge_repository
from app.schemas.chunk_schema import RetrieveType
from app.services.model_service import ModelConfigService

logger = logging.getLogger(__name__)


class KnowledgeRetrievalNode(BaseNode):
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any], down_stream_nodes: list[str]):
        super().__init__(node_config, workflow_config, down_stream_nodes)
        self.typed_config: KnowledgeRetrievalNodeConfig | None = None

    def _output_types(self) -> dict[str, VariableType]:
        return {
            "output": VariableType.ARRAY_STRING
        }

    def _extract_output(self, business_result: Any) -> Any:
        """下游节点只拿 chunks 列表"""
        if isinstance(business_result, dict) and "chunks" in business_result:
            return business_result["chunks"]
        return business_result
    
    @staticmethod
    def _extract_citations(business_result: Any) -> list:
        if isinstance(business_result, dict):
            return business_result.get("citations", [])
        return []

    def _extract_extra_fields(self, business_result: Any) -> dict:
        return {"citations": self._extract_citations(business_result)}

    def _extract_input(self, state: WorkflowState, variable_pool: VariablePool) -> dict[str, Any]:
        return {
            "query": self._render_template(self.typed_config.query, variable_pool),
            "knowledge_bases": [kb_config.model_dump(mode="json") for kb_config in self.typed_config.knowledge_bases],
        }

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

    def rerank(self, query: str, docs: list[DocumentChunk], top_k: int) -> list[DocumentChunk]:
        """
        Reorder the list of document blocks and return the top_k results most relevant to the query
        Args:
            query: query string
            docs: List of document chunk to be rearranged
            top_k: The number of top-level documents returned

        Returns:
            Rearranged document chunk list (sorted in descending order of relevance)

        Raises:
            ValueError: If the input document list is empty or top_k is invalid
        """
        reranker = self.get_reranker_model()
        # parameter validation
        if not docs:
            raise ValueError("retrieval chunks be empty")
        if top_k <= 0:
            raise ValueError("top_k must be a positive integer")
        try:
            # Convert to LangChain Document object
            documents = [
                Document(
                    page_content=doc.page_content,  # Ensure that DocumentChunk possesses this attribute
                    metadata=doc.metadata or {}  # Deal with possible None metadata
                )
                for doc in docs
            ]

            # Perform reordering (compress_documents will automatically handle relevance scores and indexing)
            reranked_docs = list(reranker.compress_documents(documents, query))

            # Sort in descending order based on relevance score
            reranked_docs.sort(
                key=lambda x: x.metadata.get("relevance_score", 0),
                reverse=True
            )
            # Convert back to a list of DocumentChunk, and save the relevance_score to metadata["score"]
            result = []
            for item in reranked_docs[:top_k]:
                for doc in docs:
                    if doc.page_content == item.page_content:
                        doc.metadata["score"] = item.metadata["relevance_score"]
                        result.append(doc)
            return result
        except Exception as e:
            raise RuntimeError(f"Failed to rerank documents: {str(e)}") from e

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

    async def knowledge_retrieval(self, db, query, db_knowledge, kb_config):
        rs = []
        if db_knowledge.type == knowledge_model.KnowledgeType.FOLDER:
            children = knowledge_repository.get_knowledges_by_parent_id(db=db, parent_id=db_knowledge.id)
            tasks = []
            for child in children:
                if not (child and child.chunk_num > 0 and child.status == 1):
                    continue
                child_kb_config = kb_config.model_copy()
                child_kb_config.kb_id = child.id
                tasks.append(self.knowledge_retrieval(db, query, child, child_kb_config))
            if tasks:
                result = await asyncio.gather(*tasks)
                for _ in result:
                    rs.extend(_)
            return rs
        vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)
        indices = f"Vector_index_{kb_config.kb_id}_Node".lower()
        match kb_config.retrieve_type:
            case RetrieveType.PARTICIPLE:
                rs.extend(
                    await asyncio.to_thread(
                        vector_service.search_by_full_text, **{
                            "query": query,
                            "top_k": kb_config.top_k,
                            "indices": indices,
                            "score_threshold": kb_config.similarity_threshold
                        }
                    )
                )
            case RetrieveType.SEMANTIC:
                rs.extend(
                    await asyncio.to_thread(
                        vector_service.search_by_vector, **{
                            "query": query,
                            "top_k": kb_config.top_k,
                            "indices": indices,
                            "score_threshold": kb_config.vector_similarity_weight
                        }
                    )
                )
            case retrieve_type if retrieve_type in (RetrieveType.HYBRID, RetrieveType.Graph):
                rs1_task = asyncio.to_thread(
                    vector_service.search_by_vector, **{
                        "query": query,
                        "top_k": kb_config.top_k,
                        "indices": indices,
                        "score_threshold": kb_config.vector_similarity_weight
                    }
                )
                rs2_task = asyncio.to_thread(
                    vector_service.search_by_full_text, **{
                        "query": query,
                        "top_k": kb_config.top_k,
                        "indices": indices,
                        "score_threshold": kb_config.similarity_threshold
                    }
                )
                rs1, rs2 = await asyncio.gather(rs1_task, rs2_task)

                # Deduplicate hybrid retrieval results
                unique_rs = self._deduplicate_docs(rs1, rs2)
                if not unique_rs:
                    return []
                if self.typed_config.reranker_id:
                    rs.extend(
                        await asyncio.to_thread(
                            self.rerank,
                            **{"query": query, "docs": unique_rs, "top_k": kb_config.top_k}
                        )
                    )
                else:
                    rs.extend(sorted(
                        unique_rs,
                        key=lambda d: d.metadata.get("score", 0),
                        reverse=True
                    )[:kb_config.top_k])
                if kb_config.retrieve_type == RetrieveType.Graph:
                    from app.core.rag.common.settings import kg_retriever
                    llm_key = self.model_balance(db_knowledge.llm)
                    emb_key = self.model_balance(db_knowledge.embedding)
                    chat_model = Base(
                        key=llm_key.api_key,
                        model_name=llm_key.model_name,
                        base_url=llm_key.api_base
                    )
                    embedding_model = OpenAIEmbed(
                        key=emb_key.api_key,
                        model_name=emb_key.model_name,
                        base_url=emb_key.api_base
                    )
                    doc = await asyncio.to_thread(
                        kg_retriever.retrieval,
                        question=query,
                        workspace_ids=[str(db_knowledge.workspace_id)],
                        kb_ids=[str(kb_config.kb_id)],
                        emb_mdl=embedding_model,
                        llm=chat_model
                    )
                    if doc:
                        rs.insert(0, DocumentChunk(
                            page_content=doc.get("page_content", ""),
                            metadata=doc.get("metadata", {})
                        ))
            case _:
                raise RuntimeError("Unknown retrieval type")
        return rs

    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> Any:
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
            variable_pool: Variable Pool

        Returns:
            Any: List of retrieved knowledge chunks (dict format).

        Raises:
            RuntimeError: If no valid knowledge base is found or access is denied.
        """
        self.typed_config = KnowledgeRetrievalNodeConfig(**self.config)
        if not self.typed_config.knowledge_bases:
            return []
        query = self._render_template(self.typed_config.query, variable_pool)
        with get_db_read() as db:
            knowledge_bases = self.typed_config.knowledge_bases

            rs = []
            tasks = []
            for kb_config in knowledge_bases:
                db_knowledge = knowledge_repository.get_knowledge_by_id(db=db, knowledge_id=kb_config.kb_id)
                if not db_knowledge:
                    raise RuntimeError("The knowledge base does not exist or access is denied.")
                tasks.append(self.knowledge_retrieval(db, query, db_knowledge, kb_config))
            if tasks:
                result = await asyncio.gather(*tasks)
                for _ in result:
                    rs.extend(_)

            if not rs:
                return []
            if self.typed_config.reranker_id:
                final_rs = await asyncio.to_thread(
                    self.rerank,
                    **{"query": query, "docs": rs, "top_k": self.typed_config.reranker_top_k}
                )
            else:
                final_rs = sorted(
                    rs,
                    key=lambda d: d.metadata.get("score", 0),
                    reverse=True
                )[:self.typed_config.reranker_top_k]

            logger.info(
                f"Node {self.node_id}: knowledge base retrieval completed, results count: {len(final_rs)}"
            )
            citations = []
            seen_doc_ids = set()
            for chunk in final_rs:
                meta = chunk.metadata or {}
                doc_id = meta.get("document_id") or meta.get("doc_id")
                if doc_id and doc_id not in seen_doc_ids:
                    seen_doc_ids.add(doc_id)
                    citations.append({
                        "document_id": str(doc_id),
                        "file_name": meta.get("file_name", ""),
                        "knowledge_id": str(meta.get("knowledge_id", kb_config.kb_id)),
                        "score": meta.get("score", 0.0),
                    })
            return {
                "chunks": [chunk.page_content for chunk in final_rs],
                "citations": citations,
            }
