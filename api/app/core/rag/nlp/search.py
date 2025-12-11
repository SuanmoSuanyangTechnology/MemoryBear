import uuid
from typing import Dict, List, Any
from sqlalchemy.orm import Session

from langchain_core.documents import Document
from app.db import get_db
from app.core.models.base import RedBearModelConfig
from app.core.models import RedBearLLM, RedBearRerank
from app.models.models_model import ModelApiKey
from app.models import knowledge_model
from app.core.rag.models.chunk import DocumentChunk
from app.repositories import knowledge_repository, knowledgeshare_repository
from app.services.model_service import ModelConfigService
from app.core.rag.vdb.elasticsearch.elasticsearch_vector import ElasticSearchVectorFactory


def knowledge_retrieval(
        query: str,
        config: Dict[str, Any],
        user_ids: List[str] = None,
) -> list[DocumentChunk]:
    """
    Knowledge retrieval with multiple knowledge bases and reranking

    Args:
        query: Search query string
        config: Configuration dictionary containing:
            - knowledge_bases: List of knowledge base configs with:
                - kb_id: Knowledge base ID
                - similarity_threshold: float
                - vector_similarity_weight: float
                - top_k: int
                - retrieve_type: "participle" or "semantic" or "hybrid"
            - merge_strategy: "weight" or other strategies
            - reranker_id: UUID of the reranker to use
            - reranker_top_k: int

    Returns:
        Rearranged document block list (in descending order of relevance)
    """
    db = next(get_db())  # Manually call the generator
    try:
        # parse configuration
        knowledge_bases = config.get("knowledge_bases", [])
        merge_strategy = config.get("merge_strategy", "weight")
        reranker_id = config.get("reranker_id")
        reranker_top_k = config.get("reranker_top_k", 1024)

        file_names_filter=[]
        if user_ids:
            file_names_filter.extend([f"{user_id}.txt" for user_id in user_ids])

        if not knowledge_bases:
            return []

        all_results = []
        # Search each knowledge base
        for kb_config in knowledge_bases:
            kb_id = kb_config["kb_id"]
            try:
                # Check whether the knowledge base exists and is available
                db_knowledge = knowledge_repository.get_knowledge_by_id(db, knowledge_id=kb_id)
                if db_knowledge and db_knowledge.chunk_num > 0 and db_knowledge.status == 1:
                    # Process shared knowledge base
                    if db_knowledge.permission_id.lower() == knowledge_model.PermissionType.Share:
                        knowledgeshare = knowledgeshare_repository.get_knowledgeshare_by_id(db=db,
                                                                                            knowledgeshare_id=db_knowledge.id)
                        if knowledgeshare:
                            db_knowledge = knowledge_repository.get_knowledge_by_id(db,
                                                                                    knowledge_id=knowledgeshare.source_kb_id)
                            if not (db_knowledge and db_knowledge.chunk_num > 0 and db_knowledge.status == 1):
                                continue
                        else:
                            continue

                    vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)
                    # Retrieve according to the configured retrieval type
                    match kb_config["retrieve_type"]:
                        case "participle":
                            rs = vector_service.search_by_full_text(
                                query=query,
                                top_k=kb_config["top_k"],
                                score_threshold=kb_config["similarity_threshold"],
                                file_names_filter=file_names_filter
                            )
                        case "semantic":
                            rs = vector_service.search_by_vector(
                                query=query,
                                top_k=kb_config["top_k"],
                                score_threshold=kb_config["vector_similarity_weight"],
                                file_names_filter=file_names_filter
                            )
                        case _:  # hybrid
                            rs1 = vector_service.search_by_vector(
                                query=query,
                                top_k=kb_config["top_k"],
                                score_threshold=kb_config["vector_similarity_weight"],
                                file_names_filter=file_names_filter
                            )
                            rs2 = vector_service.search_by_full_text(
                                query=query,
                                top_k=kb_config["top_k"],
                                score_threshold=kb_config["similarity_threshold"],
                                file_names_filter=file_names_filter
                            )

                            # Deduplication of merge results
                            seen_ids = set()
                            unique_rs = []
                            for doc in rs1 + rs2:
                                if doc.metadata["doc_id"] not in seen_ids:
                                    seen_ids.add(doc.metadata["doc_id"])
                                    unique_rs.append(doc)
                            rs = unique_rs

                    all_results.extend(rs)
            except Exception as e:
                # Failure of retrieval in a single knowledge base does not affect other knowledge bases
                print(f"retrieval knowledge({kb_id}) failed: {str(e)}")
                continue

        # Use the specified reranker for re-ranking
        if reranker_id:
            return rerank(db=db, reranker_id=reranker_id, query=query, docs=all_results, top_k=reranker_top_k)
        return all_results

    except Exception as e:
        print(f"retrieval knowledge failed: {str(e)}")
    finally:
        db.close()


def rerank(db: Session, reranker_id: uuid, query: str, docs: list[DocumentChunk], top_k: int) -> list[DocumentChunk]:
    """
    Reorder the list of document blocks and return the top_k results most relevant to the query
    Args:
        reranker_id: reranker model id
        query: query string
        docs: List of document blocks to be rearranged
        top_k: Number of top-level documents returned

    Returns:
        Rearranged document block list (in descending order of relevance)

    Raises:
        ValueError: If the input document list is empty or top_k is invalid
    """
    # 参数校验
    if not reranker_id:
        raise ValueError("reranker_id be empty")
    if not docs:
        raise ValueError("retrieval chunks be empty")
    if top_k <= 0:
        raise ValueError("top_k must be a positive integer")
    try:
        # initialize reranker
        config = ModelConfigService.get_model_by_id(db=db, model_id=reranker_id)
        apiConfig: ModelApiKey = config.api_keys[0]
        reranker = RedBearRerank(RedBearModelConfig(
            model_name=apiConfig.model_name,
            provider=apiConfig.provider,
            api_key=apiConfig.api_key,
            base_url=apiConfig.api_base
        ))
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
        print(reranked_docs)

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
