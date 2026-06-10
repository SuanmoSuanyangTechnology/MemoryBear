import os
import logging
import threading
from typing import Any
from urllib.parse import urlparse

import requests
from elasticsearch import Elasticsearch, helpers, NotFoundError
from elasticsearch.helpers import BulkIndexError
from packaging.version import parse as parse_version
# langchain-community
# langchain-xinference
# from langchain_community.embeddings import XinferenceEmbeddings
# from langchain_xinference import XinferenceRerank
from langchain_core.documents import Document
from app.core.models.base import RedBearModelConfig
from app.core.models import RedBearRerank
from app.core.models.embedding import RedBearEmbeddings
from app.models.models_model import ModelApiKey

from app.models.knowledge_model import Knowledge
from app.core.rag.vdb.field import Field
from app.core.rag.vdb.vector_base import BaseVector
from app.core.rag.models.chunk import DocumentChunk

logger = logging.getLogger(__name__)


class ElasticSearchVector(BaseVector):
    def __init__(self, index_name: str, client: Elasticsearch,
                 embedding_config: ModelApiKey, reranker_config: ModelApiKey):
        super().__init__(index_name.lower())

        # 初始化 Embedding 模型（自动支持火山引擎多模态）
        self.embeddings = RedBearEmbeddings(RedBearModelConfig(
            model_name=embedding_config.model_name,
            provider=embedding_config.provider,
            api_key=embedding_config.api_key,
            base_url=embedding_config.api_base
        ))
        self.is_multimodal_embedding = self.embeddings.is_multimodal_supported()

        self.reranker = RedBearRerank(RedBearModelConfig(
            model_name=reranker_config.model_name,
            provider=reranker_config.provider,
            api_key=reranker_config.api_key,
            base_url=reranker_config.api_base
        ))
        # 使用外部传入的共享客户端
        self._client = client

    def get_type(self) -> str:
        return "elasticsearch"

    def add_chunks(self, chunks: list[DocumentChunk], **kwargs):
        # 仅在写入时检查并补充字段映射，避免每次检索都做冗余检查
        # ElasticSearchVectorIndexOps.ensure_parent_id_mapping(self._client, self._collection_name)

        # QA chunks: embedding 只对 question 字段做；source/parent chunks: 不做 embedding
        texts_for_embedding = []
        for chunk in chunks:
            chunk_type = (chunk.metadata or {}).get("chunk_type", "chunk")
            if chunk_type in ("source", "parent"):
                # source 和 parent chunk 不需要向量索引
                texts_for_embedding.append("")
            elif chunk_type == "qa":
                # QA chunk: 用 question 字段做 embedding
                texts_for_embedding.append((chunk.metadata or {}).get("question", chunk.page_content))
            else:
                # 普通 chunk / child chunk: 用 page_content 做 embedding
                texts_for_embedding.append(chunk.page_content)

        if self.is_multimodal_embedding:
            embeddings = self.embeddings.embed_batch(texts_for_embedding)
        else:
            embeddings = self.embeddings.embed_documents(texts_for_embedding)

        # source/parent chunk 的向量置空
        for i, chunk in enumerate(chunks):
            if (chunk.metadata or {}).get("chunk_type") in ("source", "parent"):
                embeddings[i] = None

        self.create(chunks, embeddings, **kwargs)

    def create(self, chunks: list[DocumentChunk], embeddings: list[list[float]], **kwargs):
        metadatas = [chunk.metadata if chunk.metadata is not None else {} for chunk in chunks]
        if not self._client.indices.exists(index=self._collection_name):
            self.create_collection(embeddings, metadatas)
        self.add_texts(chunks, embeddings, **kwargs)

    def add_texts(self, chunks: list[DocumentChunk], embeddings: list[list[float]], **kwargs):
        uuids = self._get_uuids(chunks)
        actions = []
        for i, chunk in enumerate(chunks):
            source = {
                Field.CONTENT_KEY.value: chunk.page_content,
                Field.METADATA_KEY.value: chunk.metadata or {},
                Field.VECTOR.value: embeddings[i] or None
            }
            # 写入 QA 相关字段
            meta = chunk.metadata or {}
            if meta.get("chunk_type"):
                source[Field.CHUNK_TYPE.value] = meta["chunk_type"]
            if meta.get("question"):
                source[Field.QUESTION.value] = meta["question"]
            if meta.get("answer"):
                source[Field.ANSWER.value] = meta["answer"]
            if meta.get("source_chunk_id"):
                source[Field.SOURCE_CHUNK_ID.value] = meta["source_chunk_id"]
            if meta.get("parent_id"):
                source[Field.PARENT_ID.value] = meta["parent_id"]

            action = {
                "_index": self._collection_name,
                "_source": source
            }
            actions.append(action)
        # using bulk mode
        try:
            result = helpers.bulk(self._client, actions)
            logger.info(f"add_texts result:{result}")
        except BulkIndexError as e:
            for error in e.errors[:3]:
                logger.error(f"ES bulk index error detail: {error}")
            raise
        return uuids

    def text_exists(self, id: str) -> bool:
        if not self._client.indices.exists(index=self._collection_name):
            return False
        result = self._client.search(
            index=self._collection_name,
            from_=0,
            size=5,
            query={
                "bool": {
                    "must": {
                        "match": {
                            Field.DOC_ID.value: id
                        }
                    }
                }
            },
        )

        if "errors" in result:
            raise ValueError(f"Error during query: {result['errors']}")

        count = result["hits"]["total"]["value"]
        if count == 0:
            return False

        return True

    def delete_by_ids(self, ids: list[str], *, refresh: bool = False):
        if not ids:
            return
        if not self._client.indices.exists(index=self._collection_name):
            logger.warning(f"Index {self._collection_name} does not exist")
            return

        # Obtaining All Actual ES _id,not metadata.doc_id
        actual_ids = []

        for doc_id in ids:
            es_ids = self.get_ids_by_metadata_field('doc_id', doc_id)
            if es_ids:
                actual_ids.extend(es_ids)
            else:
                logger.warning(f"Document with metadata doc_id {doc_id} not found for deletion")

        if actual_ids:
            actions = [{"_op_type": "delete", "_index": self._collection_name, "_id": es_id} for es_id in actual_ids]
            try:
                helpers.bulk(self._client, actions)
                if refresh:
                    self._client.indices.refresh(index=self._collection_name)
            except BulkIndexError as e:
                for error in e.errors:
                    delete_error = error.get('delete', {})
                    status = delete_error.get('status')
                    doc_id = delete_error.get('_id')

                    if status == 404:
                        logger.warning(f"Document not found for deletion: {doc_id}")
                    else:
                        logger.error(f"Error deleting document: {error}")

    def get_ids_by_metadata_field(self, key: str, value: str):
        query = {"query": {"term": {f"{Field.METADATA_KEY.value}.{key}": value}}}
        response = self._client.search(index=self._collection_name, body=query, size=10000)
        if response['hits']['hits']:
            return [hit['_id'] for hit in response['hits']['hits']]
        else:
            return None

    def delete_by_metadata_field(self, key: str, value: str, *, refresh: bool = False):
        if not self._client.indices.exists(index=self._collection_name):
            return False
        actual_ids = self.get_ids_by_metadata_field(key, value)

        if actual_ids:
            actions = [{"_op_type": "delete", "_index": self._collection_name, "_id": es_id} for es_id in actual_ids]
            try:
                helpers.bulk(self._client, actions)
                if refresh:
                    self._client.indices.refresh(index=self._collection_name)
            except BulkIndexError as e:
                for error in e.errors:
                    delete_error = error.get('delete', {})
                    status = delete_error.get('status')
                    doc_id = delete_error.get('_id')

                    if status == 404:
                        logger.warning(f"Document not found for deletion: {doc_id}")
                    else:
                        logger.error(f"Error deleting document: {error}")
                        raise

        return True

    def delete(self):
        if self._client.indices.exists(index=self._collection_name):
            self._client.indices.delete(index=self._collection_name, ignore=[400, 404])

    def search_by_segment(self, document_id: str | None = None, query: str | None = None, pagesize: int = 10, page: int = 1, asc: bool = True, chunk_types: list[str] | str | None = None, parent_ids: list[str] | str | None = None, **kwargs) -> tuple[int, list[DocumentChunk]]:  # 返回 (total, results):
        """
        Search documents by segment (pagination) with optional keyword query.

        Args:
            document_id: If provided, filter results where `metadata.document_id` matches this value.
            query: Optional keywords used to match chunk content.
            pagesize: Number of documents per page.
            page: 1-based page number.
            chunk_types: If provided, filter by chunk_type (e.g., "parent", "child", or ["parent", "child"]).
            parent_ids: If provided, filter by metadata.parent_id (for child chunks under specific parents).
            **kwargs: Additional search parameters (e.g., indices).

        Returns:
            List of DocumentChunk objects that match the query.
        """
        indices = kwargs.get("indices", self._collection_name)  # Default single index, multiple indexes are also supported, such as "index1, index2, index3"
        if not self._client.indices.exists(index=indices):
            return 0, []

        # Calculate the start position for the current page
        from_ = pagesize * (page-1)

        # Construct the query with optional keyword matching
        query_str = {
            "query": {
                "bool": {
                    "must": []
                }
            },
            "sort": [
                {Field.SORT_ID.value: "asc" if asc else "desc"}  # Sort by the specified metadata field
            ]
        }

        if document_id:
            query_str["query"]["bool"]["must"].append({
                "term": {
                    Field.DOCUMENT_ID.value: document_id  # exact match document_id
                }
            })

        if query:
            query_str["query"]["bool"]["must"].append({
                "match": {
                    Field.CONTENT_KEY.value: {
                        "query": query,
                        "analyzer": "ik_max_word"  # Use the same analyzer as in create_collection
                    }
                }
            })

        if chunk_types:
            types = chunk_types if isinstance(chunk_types, list) else [chunk_types]
            query_str["query"]["bool"]["must"].append({
                "terms": {
                    Field.CHUNK_TYPE.value: types
                }
            })

        if parent_ids:
            pids = parent_ids if isinstance(parent_ids, list) else [parent_ids]
            query_str["query"]["bool"]["must"].append({
                "terms": {
                    f"metadata.{Field.PARENT_ID.value}": pids
                }
            })

        # For simplicity, we use from/size here which has a limit (usually up to 10,000).
        try:
            result = self._client.search(
                index=indices,
                from_=from_,  # Only use from_ for the first page (simplified)
                size=pagesize,
                body=query_str,
            )
        except NotFoundError:
            return 0, []

        if "errors" in result:
            raise ValueError(f"Error during query: {result['errors']}")
        total = result["hits"]["total"]["value"]  # Get total count

        docs_and_scores = []
        for res in result["hits"]["hits"]:
            source = res["_source"]
            page_content = source.get(Field.CONTENT_KEY.value)
            vector = None
            metadata = source.get(Field.METADATA_KEY.value, {})
            chunk_type = source.get(Field.CHUNK_TYPE.value)
            score = res["_score"]

            # 将 QA 字段注入 metadata 供前端展示
            if chunk_type:
                metadata["chunk_type"] = chunk_type
            if chunk_type == "qa":
                metadata["question"] = source.get(Field.QUESTION.value, "")
                metadata["answer"] = source.get(Field.ANSWER.value, "")
                page_content = f"question: {metadata['question']}\nanswer: {metadata['answer']}"

            docs_and_scores.append((DocumentChunk(page_content=page_content, vector=vector, metadata=metadata), score))

        docs = []
        for doc, score in docs_and_scores:
            if doc.metadata is not None:
                doc.metadata["score"] = score
            docs.append(doc)

        return total, docs

    def get_by_segment(self, doc_id: str, **kwargs) -> tuple[int, list[DocumentChunk]]:  # 返回 (total, results):
        """
        Search documents by segment with optional keyword query.

        Args:
            doc_id: If provided, filter results where `metadata.doc_id` matches this value.
            **kwargs: Additional search parameters (e.g., indices).

        Returns:
            List of DocumentChunk objects that match the query.
        """
        indices = kwargs.get("indices", self._collection_name)  # Default single index, multi-index available，etc "index1,index2,index3"
        if not self._client.indices.exists(index=indices):
            return 0, []
        query_str = {"query": {"term": {f"{Field.DOC_ID.value}": doc_id}}}
        try:
            result = self._client.search(
                index=indices,
                from_=0,  # Only use from_ for the first page (simplified)
                size=1,
                body=query_str,
            )
        except NotFoundError:
            return 0, []
        # print(result)
        if "errors" in result:
            raise ValueError(f"Error during query: {result['errors']}")
        total = result["hits"]["total"]["value"]  # Get total count

        docs_and_scores = []
        for res in result["hits"]["hits"]:
            source = res["_source"]
            page_content = source.get(Field.CONTENT_KEY.value)
            vector = source.get(Field.VECTOR.value)
            metadata = source.get(Field.METADATA_KEY.value, {})
            score = res["_score"]
            docs_and_scores.append((DocumentChunk(page_content=page_content, vector=vector, metadata=metadata), score))

        docs = []
        for doc, score in docs_and_scores:
            if doc.metadata is not None:
                doc.metadata["score"] = score
            docs.append(doc)

        return total, docs

    def update_by_segment(self, chunk: DocumentChunk, **kwargs) -> str:
        """
        update documents by segment.

        Args:
            doc_id: If provided, filter results where `metadata.doc_id` matches this value.
            chunk: updated segment
            **kwargs: Additional search parameters (e.g., indices).

        Returns:
            updated count.
        """
        indices = kwargs.get("indices", self._collection_name)
        chunk_type = (chunk.metadata or {}).get("chunk_type")

        # QA chunk: embedding 基于 question；source chunk: 不更新向量
        if chunk_type == "source":
            embed_text = ""
        elif chunk_type == "qa":
            embed_text = (chunk.metadata or {}).get("question", chunk.page_content)
        else:
            embed_text = chunk.page_content

        if chunk_type != "source":
            if self.is_multimodal_embedding:
                chunk.vector = self.embeddings.embed_text(embed_text)
            else:
                chunk.vector = self.embeddings.embed_query(embed_text)

        script_source = "ctx._source.page_content = params.new_content; ctx._source.vector = params.new_vector;"
        params = {
            "new_content": chunk.page_content,
            "new_vector": chunk.vector if chunk_type != "source" else None
        }

        # QA chunk: 同时更新 question/answer 字段
        if chunk_type == "qa":
            script_source += " ctx._source.question = params.new_question; ctx._source.answer = params.new_answer;"
            params["new_question"] = (chunk.metadata or {}).get("question", "")
            params["new_answer"] = (chunk.metadata or {}).get("answer", "")

        body = {
            "script": {
                "source": script_source,
                "params": params
            },
            "query": {
                "term": {
                    Field.DOC_ID.value: chunk.metadata["doc_id"]
                }
            }
        }
        result = self._client.update_by_query(
            index=indices,
            body=body,
        )
        return result['updated']

    def change_status_by_document_id(self, document_id: str, status: int, **kwargs) -> str:
        """
        Update the metadata.status field of all documents with the specified document_id
        Args:
            document_id: Document ID to be updated
            status: The new state value to be set (0 或 1)
        """
        indices = kwargs.get("indices", self._collection_name)  # Default single index, multi-index available，etc "index1,index2,index3"
        body = {
            "script": {
                "source": "ctx._source.metadata.status = params.new_status",
                "params": {
                    "new_status": status
                }
            },
            "query": {
                "term": {
                    Field.DOCUMENT_ID.value: document_id  # exact match document_id
                }
            }
        }
        result = self._client.update_by_query(
            index=indices,
            body=body,
        )
        # Remove debug printing and use logging instead
        # print(result)
        # print(f"Update successful, number of affected documents: {result['updated']}")
        return result['updated']

    def search_by_vector(self, query: str, resolve_parents: bool = True, **kwargs: Any) -> list[DocumentChunk]:
        """Search the nearest neighbors to a vector."""
        if self.is_multimodal_embedding:
            # 火山引擎多模态 Embedding
            query_vector = self.embeddings.embed_text(query)
        else:
            query_vector = self.embeddings.embed_query(query)
        top_k = kwargs.get("top_k", 1024)
        score_threshold = float(kwargs.get("score_threshold") or 0.3)
        indices = kwargs.get("indices", self._collection_name)  # Default single index, multi-index available，etc "index1,index2,index3"
        file_names_filter = kwargs.get("file_names_filter") # ["doc1", "doc2", "doc3"]

        query_str: dict[str, Any] = {
                "bool": {
                    "must": {
                        "script_score": {
                            "query": {
                                "match_all": {}
                            },
                            "script": {
                                "source": f"cosineSimilarity(params.query_vector, '{Field.VECTOR.value}') + 1.0",
                                # The script_score query calculates the cosine similarity between the embedding field of each document and the query vector. The addition of +1.0 is to ensure that the scores returned by the script are non-negative, as the range of cosine similarity is [-1, 1]
                                "params": {"query_vector": query_vector}
                            }
                        }
                    },
                    "filter": [
                        {"term": {"metadata.status": 1}},
                        {"exists": {"field": Field.VECTOR.value}},
                    ]
                }
            }
        # If file_names_filter is passed in, merge the filtering conditions
        if file_names_filter:
            query_str = {
                "bool": {
                    "must": {
                        "script_score": {
                            "query": {
                                "match_all": {}
                            },
                            "script": {
                                "source": f"cosineSimilarity(params.query_vector, '{Field.VECTOR.value}') + 1.0",
                                "params": {"query_vector": query_vector}
                            }
                        }
                    },
                    "filter": [
                        {"term": {"metadata.status": 1}},
                        {"terms": {"metadata.file_name": file_names_filter}},
                        {"exists": {"field": Field.VECTOR.value}},
                    ],
                }
            }

        # If document_ids_filter is passed in, append to filter (blacklist: exclude these IDs)
        document_ids_filter = kwargs.get("document_ids_filter")
        if document_ids_filter:
            query_str["bool"]["filter"].append({
                "bool": {"must_not": {"terms": {"metadata.document_id": document_ids_filter}}}
            })
            logger.info(f"[ES search_by_vector] excluding document_ids: {document_ids_filter}")
        else:
            logger.info("[ES search_by_vector] no document_ids_filter")

        logger.debug(f"[ES search_by_vector] query DSL: {query_str}")

        result = self._client.search(
            index=indices,
            from_=0,
            size=top_k,
            query=query_str
        )
        # logger.info(result)

        if "errors" in result:
            raise ValueError(f"Error during query: {result['errors']}")

        docs_and_scores = []
        for res in result["hits"]["hits"]:
            source = res["_source"]
            page_content = source.get(Field.CONTENT_KEY.value)
            metadata = source.get(Field.METADATA_KEY.value, {})
            chunk_type = source.get(Field.CHUNK_TYPE.value)
            score = res["_score"]
            score = score / 2  # Normalized [0-1]

            # QA chunk: 返回 Q+A 拼接作为上下文
            if chunk_type == "qa":
                question = source.get(Field.QUESTION.value, "")
                answer = source.get(Field.ANSWER.value, "")
                page_content = f"question: {question}\nanswer: {answer}"
                metadata["chunk_type"] = "qa"
                metadata["question"] = question
                metadata["answer"] = answer

            docs_and_scores.append((DocumentChunk(page_content=page_content, metadata=metadata), score))

        # docs = [doc for doc, score in docs_and_scores]
        docs = []
        for doc, score in docs_and_scores:
            # check score threshold
            if score > score_threshold:
                if doc.metadata is not None:
                    doc.metadata["score"] = score
                    docs.append(doc)

        return self.resolve_parent_chunks(docs) if resolve_parents else docs

    def search_by_full_text(self, query: str, resolve_parents: bool = True, **kwargs: Any) -> list[DocumentChunk]:
        """Return docs using BM25F.

        Args:
            query: Text to look up documents similar to.
            k: Number of Documents to return. Defaults to 4.

        Returns:
            List of Documents most similar to the query.
        """
        top_k = kwargs.get("top_k", 1024)
        score_threshold = float(kwargs.get("score_threshold") or 0.2)
        indices = kwargs.get("indices", self._collection_name)  # Default single index, multiple indexes are also supported, such as "index1, index2, index3"
        file_names_filter = kwargs.get("file_names_filter") # ["doc1", "doc2", "doc3"]

        # Basic Query（BM25）
        query_str: dict[str, Any] = {
            "bool": {
                "must": {
                    "match": {
                        Field.CONTENT_KEY.value: {
                            "query": query,
                            "analyzer": "ik_max_word"  # tokenizer
                        }
                    }
                },
                "filter": [
                    {"term": {"metadata.status": 1}},
                ]
            }
        }

        # If file_names_filter is passed in, merge the filtering conditions
        if file_names_filter:
            query_str = {
                "bool": {
                    "must": {
                        "match": {
                            Field.CONTENT_KEY.value: {
                                "query": query,
                                "analyzer": "ik_max_word"  # tokenizer
                            }
                        }
                    },
                    "filter": [
                        {"term": {"metadata.status": 1}},
                        {"terms": {"metadata.file_name": file_names_filter}},
                    ],
                }
            }

        # If document_ids_filter is passed in, append to filter (blacklist: exclude these IDs)
        document_ids_filter = kwargs.get("document_ids_filter")
        if document_ids_filter:
            query_str["bool"]["filter"].append({
                "bool": {"must_not": {"terms": {"metadata.document_id": document_ids_filter}}}
            })
            logger.info(f"[ES search_by_full_text] excluding document_ids: {document_ids_filter}")
        else:
            logger.info("[ES search_by_full_text] no document_ids_filter")

        logger.debug(f"[ES search_by_full_text] query DSL: {query_str}")

        result = self._client.search(
            index=indices,
            from_=0,
            size=top_k,
            query=query_str,
        )
        # logger.info(result)

        if "errors" in result:
            raise ValueError(f"Error during query: {result['errors']}")

        docs_and_scores = []
        max_score = result["hits"]["max_score"] or 1.0  # Get the maximum score. If it is None, use 1.0
        for res in result["hits"]["hits"]:
            source = res["_source"]
            page_content = source.get(Field.CONTENT_KEY.value)
            metadata = source.get(Field.METADATA_KEY.value, {})
            chunk_type = source.get(Field.CHUNK_TYPE.value)

            # QA chunk: 返回 Q+A 拼接作为上下文
            if chunk_type == "qa":
                question = source.get(Field.QUESTION.value, "")
                answer = source.get(Field.ANSWER.value, "")
                page_content = f"question: {question}\nanswer: {answer}"
                metadata["chunk_type"] = "qa"
                metadata["question"] = question
                metadata["answer"] = answer

            # Normalize the score to the [0,1] interval
            normalized_score = res["_score"] / max_score
            docs_and_scores.append((DocumentChunk(page_content=page_content, metadata=metadata), normalized_score))

        # docs = [doc for doc, score in docs_and_scores]
        docs = []
        for doc, score in docs_and_scores:
            # check score threshold
            if score > score_threshold:
                if doc.metadata is not None:
                    doc.metadata["score"] = score
                    docs.append(doc)

        return self.resolve_parent_chunks(docs) if resolve_parents else docs

    def resolve_parent_chunks(self, chunks: list[DocumentChunk]) -> list[DocumentChunk]:
        """
        For child chunks (chunk_type == "child"), replace page_content with the
        parent chunk's page_content. Deduplicate when multiple children share
        the same parent.

        Non-child chunks (regular "chunk", "qa") pass through unchanged.
        """
        child_results = []
        other_results = []
        for doc in chunks:
            if (doc.metadata or {}).get("chunk_type") == "child":
                child_results.append(doc)
            else:
                other_results.append(doc)

        if not child_results:
            return chunks

        # Collect unique parent IDs
        parent_ids = list({doc.metadata.get("parent_id", "") for doc in child_results if doc.metadata.get("parent_id")})
        if not parent_ids:
            return chunks

        # Batch-fetch parent chunks from ES by doc_id
        parent_map = {}
        try:
            result = self._client.search(
                index=self._collection_name,
                body={
                    "query": {
                        "bool": {
                            "must": [
                                {"terms": {"metadata.doc_id": parent_ids}}
                            ]
                        }
                    }
                },
                size=len(parent_ids),
            )
            for hit in result.get("hits", {}).get("hits", []):
                source = hit["_source"]
                parent_doc_id = source.get("metadata", {}).get("doc_id", "")
                parent_map[parent_doc_id] = DocumentChunk(
                    page_content=source.get(Field.CONTENT_KEY.value, ""),
                    metadata=source.get(Field.METADATA_KEY.value, {}),
                )
        except Exception as e:
            logger.warning(f"Failed to resolve parent chunks: {e}")
            return chunks

        # Replace child content with parent content, dedup by parent_id
        seen_parents: dict[str, DocumentChunk] = {}
        for doc in child_results:
            parent_id = doc.metadata.get("parent_id", "")
            if parent_id in seen_parents:
                existing = seen_parents[parent_id]
                if doc.metadata.get("score", 0) > existing.metadata.get("score", 0):
                    seen_parents[parent_id] = DocumentChunk(
                        page_content=existing.page_content,
                        metadata={**existing.metadata, "score": doc.metadata.get("score", 0)},
                    )
                continue

            parent = parent_map.get(parent_id)
            if parent:
                # Replace page_content with parent's, preserve child's score
                score = doc.metadata.get("score", 0)
                merged_metadata = {**parent.metadata, "score": score, "chunk_type": "parent"}
                seen_parents[parent_id] = DocumentChunk(
                    page_content=parent.page_content,
                    metadata=merged_metadata,
                )
            else:
                # Parent not found, keep child as-is
                seen_parents[parent_id] = doc

        return list(seen_parents.values()) + other_results

    def rerank(self, query: str, docs: list[DocumentChunk], top_k: int) -> list[DocumentChunk]:
        """
        Reorder the list of document blocks and return the top_k results most relevant to the query.
        Falls back to the original docs (truncated to top_k) if reranking fails.
        """
        if not docs:
            raise ValueError("retrieval chunks be empty")
        if top_k <= 0:
            raise ValueError("top_k must be a positive integer")
        try:
            documents = [
                Document(
                    page_content=doc.page_content,
                    metadata=doc.metadata or {}
                )
                for doc in docs
            ]

            reranked_docs = list(self.reranker.compress_documents(documents, query))
            logger.debug(f"[rerank] returned {len(reranked_docs)} docs")

            reranked_docs.sort(
                key=lambda x: x.metadata.get("relevance_score", 0),
                reverse=True
            )
            result = []
            for item in reranked_docs[:top_k]:
                for doc in docs:
                    if doc.metadata["doc_id"] == item.metadata['doc_id']:
                        doc.metadata["score"] = item.metadata["relevance_score"]
                        result.append(doc)
            return result
        except Exception as e:
            logger.warning(f"Rerank failed, falling back to original results: {str(e)}")
            for doc in docs[:top_k]:
                if doc.metadata is not None and "score" not in doc.metadata:
                    doc.metadata["score"] = 0.5
            return docs[:top_k]

    def create_collection(
        self,
        embeddings: list[list[float]],
        metadatas: list[dict[Any, Any]] | None = None,
        index_params: dict | None = None,
    ):
        if not self._client.indices.exists(index=self._collection_name):
            index_mapping = {
                "mappings": {
                    "properties": {
                        Field.CONTENT_KEY.value: {
                            "type": "text",
                            "analyzer": "ik_max_word"  # tokenizer
                        },
                        Field.METADATA_KEY.value: {
                            "type": "object",
                            "properties": {
                                "doc_id": {
                                    "type": "keyword"   # Map doc_id to keyword type
                                },
                                "file_id": {
                                    "type": "keyword"
                                },
                                "file_name": {
                                    "type": "keyword"
                                },
                                "file_created_at": {
                                    "type": "date",  # Store as date type
                                    "format": "epoch_millis"  # Specify a millisecond-level Unix timestamp
                                },
                                "document_id": {
                                    "type": "keyword"
                                },
                                "knowledge_id": {
                                    "type": "keyword"
                                },
                                "sort_id": {
                                    "type": "long"  # sort field
                                },
                                "status": {
                                    "type": "integer"
                                },
                                "parent_id": {
                                    "type": "keyword"
                                }
                            }
                        },
                        Field.VECTOR.value: {
                            "type": "dense_vector",
                            "dims": len(next((e for e in embeddings if e is not None), [0]*768)),  # 跳过 None 获取向量维度，fallback 768
                            "index": True,
                            "similarity": "cosine"
                        },
                        Field.CHUNK_TYPE.value: {
                            "type": "keyword"
                        },
                        Field.QUESTION.value: {
                            "type": "text",
                            "analyzer": "ik_max_word"
                        },
                        Field.ANSWER.value: {
                            "type": "text",
                            "analyzer": "ik_max_word"
                        },
                        Field.SOURCE_CHUNK_ID.value: {
                            "type": "keyword"
                        },
                        Field.PARENT_ID.value: {
                            "type": "keyword"
                        }
                    }
                }
            }
            print(index_mapping)
            self._client.indices.create(index=self._collection_name, body=index_mapping)


class ElasticSearchVectorClientProvider:
    """Shared Elasticsearch client provider."""
    _client: Elasticsearch | None = None
    _lock = threading.Lock()
    _version_checked = False

    @classmethod
    def get_shared_client(cls) -> Elasticsearch:
        """Get a thread-safe shared Elasticsearch client."""
        if cls._client is not None:
            return cls._client

        with cls._lock:
            # 双重检查，防止并发时重复创建
            if cls._client is not None:
                return cls._client

            try:
                parsed_url = urlparse(os.getenv("ELASTICSEARCH_HOST", "127.0.0.1") or "")
                if parsed_url.scheme in {"http", "https"}:
                    hosts = f'{os.getenv("ELASTICSEARCH_HOST")}:{os.getenv("ELASTICSEARCH_PORT", 9200)}'
                    use_https = parsed_url.scheme == "https"
                else:
                    hosts = f'https://{os.getenv("ELASTICSEARCH_HOST", "127.0.0.1")}:{os.getenv("ELASTICSEARCH_PORT", 9200)}'
                    use_https = False

                client_config = {
                    "hosts": [hosts],
                    "basic_auth": (
                        os.getenv("ELASTICSEARCH_USERNAME", "elastic"),
                        os.getenv("ELASTICSEARCH_PASSWORD", "elastic"),
                    ),
                    "request_timeout": int(os.getenv("ELASTICSEARCH_REQUEST_TIMEOUT", 30)),
                    "retry_on_timeout": True,
                    "max_retries": int(os.getenv("ELASTICSEARCH_MAX_RETRIES", 3)),
                    "connections_per_node": int(os.getenv("ELASTICSEARCH_CONNECTIONS_PER_NODE", 10)),
                }

                if use_https:
                    client_config["verify_certs"] = os.getenv("ELASTICSEARCH_VERIFY_CERTS", "false") == "true"
                    ca_certs = os.getenv("ELASTICSEARCH_CA_CERTS")
                    if ca_certs:
                        client_config["ca_certs"] = str(ca_certs)

                client = Elasticsearch(**client_config)

                if not client.ping():
                    raise ConnectionError("Failed to connect to Elasticsearch")

                # 版本检查只做一次
                if not cls._version_checked:
                    info = client.info()
                    version = info["version"]["number"]
                    if parse_version(version) < parse_version("8.0.0"):
                        raise ValueError(f"Elasticsearch version must be >= 8.0.0, got {version}")
                    cls._version_checked = True
                    logger.info(f"Elasticsearch shared client initialized, version: {version}")

                cls._client = client

            except requests.ConnectionError as e:
                raise ConnectionError(f"Vector database connection error: {str(e)}")
            except Exception as e:
                raise ConnectionError(f"Elasticsearch client initialization failed: {str(e)}")

        return cls._client


class ElasticSearchVectorIndexOps:
    """Lightweight Elasticsearch index operations that do not initialize model clients."""

    def __init__(self, collection_name: str, client: Elasticsearch):
        self._collection_name = collection_name
        self._client = client

    @staticmethod
    def collection_name_for_knowledge(knowledge_id: Any) -> str:
        return f"Vector_index_{knowledge_id}_Node".lower()

    @classmethod
    def _get_shared_client(cls) -> Elasticsearch:
        return ElasticSearchVectorClientProvider.get_shared_client()

    @classmethod
    def for_knowledge(cls, knowledge_id: Any) -> "ElasticSearchVectorIndexOps":
        return cls(
            collection_name=cls.collection_name_for_knowledge(knowledge_id),
            client=cls._get_shared_client(),
        )

    def delete_index(self) -> None:
        if self._client.indices.exists(index=self._collection_name):
            self._client.indices.delete(index=self._collection_name, ignore=[400, 404])

    def _get_ids_by_metadata_field(self, key: str, value: str) -> list[str] | None:
        query = {"query": {"term": {f"{Field.METADATA_KEY.value}.{key}": value}}}
        response = self._client.search(index=self._collection_name, body=query, size=10000)
        if response["hits"]["hits"]:
            return [hit["_id"] for hit in response["hits"]["hits"]]
        return None

    def delete_by_metadata_field(
            self,
            key: str,
            value: str,
            *,
            refresh: bool = False,
    ) -> bool:
        if not self._client.indices.exists(index=self._collection_name):
            return False

        actual_ids = self._get_ids_by_metadata_field(key, value)
        if not actual_ids:
            return True

        actions = [{"_op_type": "delete", "_index": self._collection_name, "_id": es_id} for es_id in actual_ids]
        try:
            helpers.bulk(self._client, actions)
            if refresh:
                self._client.indices.refresh(index=self._collection_name)
        except BulkIndexError as e:
            for error in e.errors:
                delete_error = error.get("delete", {})
                status = delete_error.get("status")
                doc_id = delete_error.get("_id")

                if status == 404:
                    logger.warning(f"Document not found for deletion: {doc_id}")
                else:
                    logger.error(f"Error deleting document: {error}")
                    raise

        return True

    def change_document_status(self, document_id: str, status: int) -> int:
        body = {
            "script": {
                "source": "ctx._source.metadata.status = params.new_status",
                "params": {
                    "new_status": status
                }
            },
            "query": {
                "term": {
                    Field.DOCUMENT_ID.value: document_id
                }
            }
        }
        result = self._client.update_by_query(
            index=self._collection_name,
            body=body,
        )
        return result["updated"]

    @classmethod
    def ensure_parent_id_mapping(cls, client: Elasticsearch, index_name: str):
        """Add missing parent/QA mapping fields to an existing index."""
        if not client.indices.exists(index=index_name):
            return
        try:
            mapping = client.indices.get_mapping(index=index_name)
            props = mapping[index_name]["mappings"].get("properties", {})
            metadata_props = props.get("metadata", {}).get("properties", {})

            update_body: dict[str, Any] = {"properties": {}}

            if metadata_props.get("parent_id", {}).get("type") != "keyword":
                update_body["properties"]["metadata"] = {
                    "properties": {"parent_id": {"type": "keyword"}}
                }

            if props.get(Field.PARENT_ID.value, {}).get("type") != "keyword":
                update_body["properties"][Field.PARENT_ID.value] = {"type": "keyword"}

            if Field.CHUNK_TYPE.value not in props:
                update_body["properties"][Field.CHUNK_TYPE.value] = {"type": "keyword"}

            if Field.SOURCE_CHUNK_ID.value not in props:
                update_body["properties"][Field.SOURCE_CHUNK_ID.value] = {"type": "keyword"}

            if len(update_body["properties"]) > 0:
                client.indices.put_mapping(index=index_name, body=update_body)
                logger.info(f"Updated mapping for {index_name}: added {list(update_body['properties'].keys())}")
        except Exception as e:
            logger.warning(f"Failed to update mapping for {index_name}: {e}")


class ElasticSearchVectorFactory:
    """Create full vector services that require configured model clients."""

    @classmethod
    def init_vector(cls, knowledge: Knowledge) -> ElasticSearchVector:
        """Create a full vector service with configured model clients."""
        client = ElasticSearchVectorClientProvider.get_shared_client()
        collection_name = ElasticSearchVectorIndexOps.collection_name_for_knowledge(knowledge.id)

        if knowledge.embedding is None:
            raise ValueError(f"embedding_id config error: {str(knowledge.embedding_id)}")
        if knowledge.reranker is None:
            raise ValueError(f"reranker_id config error: {str(knowledge.reranker_id)}")
        if not knowledge.embedding.api_keys:
            raise ValueError(f"No embedding api key found for knowledge {knowledge.id}")
        if not knowledge.reranker.api_keys:
            raise ValueError(f"No reranker api key found for knowledge {knowledge.id}")
        embedding_config = knowledge.embedding.api_keys[0]
        reranker_config = knowledge.reranker.api_keys[0]

        return ElasticSearchVector(
            index_name=collection_name,
            client=client,
            embedding_config=embedding_config,
            reranker_config=reranker_config,
        )
