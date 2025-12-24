import os
import logging
from typing import Any, cast
from urllib.parse import urlparse
import uuid

import requests
from elasticsearch import Elasticsearch, helpers
from elasticsearch.helpers import BulkIndexError
from packaging.version import parse as parse_version
from pydantic import BaseModel, model_validator
from abc import ABC
# langchain-community
# langchain-xinference
# from langchain_community.embeddings import XinferenceEmbeddings
# from langchain_xinference import XinferenceRerank
from langchain_core.documents import Document
from app.core.models.base import RedBearModelConfig
from app.core.models import RedBearLLM, RedBearRerank
from app.core.models.embedding import RedBearEmbeddings
from app.models.models_model import ModelConfig, ModelApiKey
from app.services.model_service import ModelConfigService

from app.models.knowledge_model import Knowledge
from app.core.rag.vdb.field import Field
from app.core.rag.vdb.vector_base import BaseVector
from app.core.rag.models.chunk import DocumentChunk

logger = logging.getLogger(__name__)


class ElasticSearchConfig(BaseModel):
    # Regular Elasticsearch config
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None

    # Common config
    ca_certs: str | None = None
    verify_certs: bool = False
    request_timeout: int = 100000
    retry_on_timeout: bool = True
    max_retries: int = 10000

    @model_validator(mode="before")
    @classmethod
    def validate_config(cls, values: dict):
        # Regular Elasticsearch validation
        if not values.get("host"):
            raise ValueError("config HOST is required for regular Elasticsearch")
        if not values.get("port"):
            raise ValueError("config PORT is required for regular Elasticsearch")
        if not values.get("username"):
            raise ValueError("config USERNAME is required for regular Elasticsearch")
        if not values.get("password"):
            raise ValueError("config PASSWORD is required for regular Elasticsearch")
        return values


class ElasticSearchVector(BaseVector):
    def __init__(self, index_name: str, config: ElasticSearchConfig, embedding_config: ModelApiKey, reranker_config: ModelApiKey):
        super().__init__(index_name.lower())
        # self.embeddings = XinferenceEmbeddings(
        #     server_url=os.getenv("XINFERENCE_URL", "http://127.0.0.1"),  # Default Xinference port
        #     model_uid="bge-m3"  # replace model_uid with the model UID return from launching the model
        # )
        # Remove debug printing to avoid leaking sensitive information
        # print("embedding:" + embedding_config.model_name + "|" + embedding_config.provider + "|" + embedding_config.api_key + "|" + embedding_config.api_base)
        self.embeddings = RedBearEmbeddings(RedBearModelConfig(
            model_name=embedding_config.model_name,
            provider=embedding_config.provider,
            api_key=embedding_config.api_key,
            base_url=embedding_config.api_base
        ))
        # self.reranker = XinferenceRerank(
        #     server_url=os.getenv("XINFERENCE_URL", "http://127.0.0.1"),
        #     model_uid="bge-reranker-large"
        # )
        # Remove debug printing to avoid leaking sensitive information
        # print("reranker:"+ reranker_config.model_name + "|" + reranker_config.provider + "|" + reranker_config.api_key + "|" + reranker_config.api_base)
        self.reranker = RedBearRerank(RedBearModelConfig(
            model_name=reranker_config.model_name,
            provider=reranker_config.provider,
            api_key=reranker_config.api_key,
            base_url=reranker_config.api_base
        ))
        self._client = self._init_client(config)
        self._version = self._get_version()
        self._check_version()

    def _init_client(self, config: ElasticSearchConfig) -> Elasticsearch:
        """
        Initialize Elasticsearch client for regular Elasticsearch.
        """
        try:
            # Regular Elasticsearch configuration
            parsed_url = urlparse(config.host or "")
            if parsed_url.scheme in {"http", "https"}:
                hosts = f"{config.host}:{config.port}"
                use_https = parsed_url.scheme == "https"
            else:
                hosts = f"https://{config.host}:{config.port}"
                use_https = False

            client_config = {
                "hosts": [hosts],
                "basic_auth": (config.username, config.password),
                "request_timeout": config.request_timeout,
                "retry_on_timeout": config.retry_on_timeout,
                "max_retries": config.max_retries,
            }

            # Only add SSL settings if using HTTPS
            if use_https:
                client_config["verify_certs"] = config.verify_certs
                if config.ca_certs:
                    client_config["ca_certs"] = config.ca_certs

            client = Elasticsearch(**client_config)

            # Test connection
            if not client.ping():
                raise ConnectionError("Failed to connect to Elasticsearch")

        except requests.ConnectionError as e:
            raise ConnectionError(f"Vector database connection error: {str(e)}")
        except Exception as e:
            raise ConnectionError(f"Elasticsearch client initialization failed: {str(e)}")

        return client

    def _get_version(self) -> str:
        info = self._client.info()
        return cast(str, info["version"]["number"])

    def _check_version(self):
        if parse_version(self._version) < parse_version("8.0.0"):
            raise ValueError("Elasticsearch vector database version must be greater than 8.0.0")

    def get_type(self) -> str:
        return "elasticsearch"

    def add_chunks(self, chunks: list[DocumentChunk], **kwargs):
        # 实现 Elasticsearch 保存向量
        texts = [chunk.page_content for chunk in chunks]
        embeddings = self.embeddings.embed_documents(list(texts))
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
            action = {
                "_index": self._collection_name,
                "_source": {
                    Field.CONTENT_KEY.value: chunk.page_content,
                    Field.METADATA_KEY.value: chunk.metadata or {},
                    Field.VECTOR.value: embeddings[i] or None
                }
            }
            actions.append(action)
        # using bulk mode
        result = helpers.bulk(self._client, actions)
        logger.info(f"add_texts result:{result}")
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

    def delete_by_ids(self, ids: list[str]):
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

    def delete_by_metadata_field(self, key: str, value: str):
        if not self._client.indices.exists(index=self._collection_name):
            return False
        actual_ids = self.get_ids_by_metadata_field(key, value)

        if actual_ids:
            actions = [{"_op_type": "delete", "_index": self._collection_name, "_id": es_id} for es_id in actual_ids]
            try:
                helpers.bulk(self._client, actions)
            except BulkIndexError as e:
                for error in e.errors:
                    delete_error = error.get('delete', {})
                    status = delete_error.get('status')
                    doc_id = delete_error.get('_id')

                    if status == 404:
                        logger.warning(f"Document not found for deletion: {doc_id}")
                    else:
                        logger.error(f"Error deleting document: {error}")

    def delete(self):
        if self._client.indices.exists(index=self._collection_name):
            self._client.indices.delete(index=self._collection_name, ignore=[400, 404])

    def search_by_segment(self, document_id: str | None = None, query: str | None = None, pagesize: int = 10, page: int = 1, asc: bool = True, **kwargs) -> tuple[int, list[DocumentChunk]]:  # 返回 (total, results):
        """
        Search documents by segment (pagination) with optional keyword query.

        Args:
            document_id: If provided, filter results where `metadata.document_id` matches this value.
            query: Optional keywords used to match chunk content.
            pagesize: Number of documents per page.
            page: 1-based page number.
            **kwargs: Additional search parameters (e.g., indices).

        Returns:
            List of DocumentChunk objects that match the query.
        """
        indices = kwargs.get("indices", self._collection_name)  # Default single index, multiple indexes are also supported, such as "index1, index2, index3"

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

        # For simplicity, we use from/size here which has a limit (usually up to 10,000).
        result = self._client.search(
            index=indices,
            from_=from_,  # Only use from_ for the first page (simplified)
            size=pagesize,
            body=query_str,
        )

        if "errors" in result:
            raise ValueError(f"Error during query: {result['errors']}")
        total = result["hits"]["total"]["value"]  # Get total count

        docs_and_scores = []
        for res in result["hits"]["hits"]:
            source = res["_source"]
            page_content = source.get(Field.CONTENT_KEY.value)
            # vector = source.get(Field.VECTOR.value)
            vector = None
            metadata = source.get(Field.METADATA_KEY.value, {})
            score = res["_score"]
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
        query_str = {"query": {"term": {f"{Field.DOC_ID.value}": doc_id}}}
        result = self._client.search(
            index=indices,
            from_=0,  # Only use from_ for the first page (simplified)
            size=1,
            body=query_str,
        )
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
        indices = kwargs.get("indices", self._collection_name)  # Default single index, multi-index available，etc "index1,index2,index3"
        chunk.vector = self.embeddings.embed_query(chunk.page_content)

        body = {
            "script": {
                "source": """
                        ctx._source.page_content = params.new_content;
                        ctx._source.vector = params.new_vector;
                    """,
                "params": {
                    "new_content": chunk.page_content,
                    "new_vector": chunk.vector
                }
            },
            "query": {
                "term": {
                    Field.DOC_ID.value: chunk.metadata["doc_id"]  # exact match doc_id
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

    def search_by_vector(self, query: str, **kwargs: Any) -> list[DocumentChunk]:
        """Search the nearest neighbors to a vector."""
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
                    "filter": {  # Add the filter condition of status=1
                        "term": {
                            "metadata.status": 1
                        }
                    }
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
                                # The script_score query calculates the cosine similarity between the embedding field of each document and the query vector. The addition of +1.0 is to ensure that the scores returned by the script are non-negative, as the range of cosine similarity is [-1, 1]
                                "params": {"query_vector": query_vector}
                            }
                        }
                    },
                    "filter": [
                        {
                            "term": {
                                "metadata.status": 1
                            }
                        },
                        {
                            "terms": {
                                "metadata.file_name": file_names_filter  # Additional file_name filtering
                            }
                        }
                    ],
                }
            }

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
            score = res["_score"]
            score = score / 2  # Normalized [0-1]
            docs_and_scores.append((DocumentChunk(page_content=page_content, metadata=metadata), score))

        docs = []
        for doc, score in docs_and_scores:
            # check score threshold
            if score > score_threshold:
                if doc.metadata is not None:
                    doc.metadata["score"] = score
                    docs.append(doc)

        return docs

    def search_by_full_text(self, query: str, **kwargs: Any) -> list[DocumentChunk]:
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
                "filter": {  # Add the filter condition of status=1
                    "term": {
                        "metadata.status": 1
                    }
                }
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
                        {
                            "term": {
                                "metadata.status": 1
                            }
                        },
                        {
                            "terms": {
                                "metadata.file_name": file_names_filter  # Additional file_name filtering
                            }
                        }
                    ],
                }
            }

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
            # Normalize the score to the [0,1] interval
            normalized_score = res["_score"] / max_score
            docs_and_scores.append((DocumentChunk(page_content=page_content, metadata=metadata), normalized_score))
        
        docs = []
        for doc, score in docs_and_scores:
            # check score threshold
            if score > score_threshold:
                if doc.metadata is not None:
                    doc.metadata["score"] = score
                    docs.append(doc)

        return docs

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
            reranked_docs = list(self.reranker.compress_documents(documents, query))
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
                                }
                            }
                        },
                        Field.VECTOR.value: {
                            "type": "dense_vector",
                            "dims": len(embeddings[0]),  # Make sure the dimension is correct here,The dimension size of the vector. When index is true, it cannot exceed 1024; when index is false or not specified, it cannot exceed 2048, which can improve retrieval efficiency
                            "index": True,
                            "similarity": "cosine"
                        }
                    }
                }
            }
            print(index_mapping)
            self._client.indices.create(index=self._collection_name, body=index_mapping)


class ElasticSearchVectorFactory:
    @staticmethod
    def init_vector(knowledge: Knowledge) -> ElasticSearchVector:
        collection_name = f"Vector_index_{knowledge.id}_Node"

        # Use regular Elasticsearch with config values
        config_dict = {
            "host": os.getenv("ELASTICSEARCH_HOST", "127.0.0.1"),
            "port": os.getenv("ELASTICSEARCH_PORT", 9200),
            "username": os.getenv("ELASTICSEARCH_USERNAME", "elastic"),
            "password": os.getenv("ELASTICSEARCH_PASSWORD", "elastic"),
        }

        # Common configuration
        config_dict.update(
            {
                "ca_certs": str(os.getenv("ELASTICSEARCH_CA_CERTS")) if os.getenv("ELASTICSEARCH_CA_CERTS") else None,
                "verify_certs": os.getenv("ELASTICSEARCH_VERIFY_CERTS", False) == "true",
                "request_timeout": int(os.getenv("ELASTICSEARCH_REQUEST_TIMEOUT", 100000)),
                "retry_on_timeout": os.getenv("ELASTICSEARCH_RETRY_ON_TIMEOUT", True) == "true",
                "max_retries": int(os.getenv("ELASTICSEARCH_MAX_RETRIES", 10000)),
            }
        )

        if knowledge.embedding is None:
            raise ValueError(f"embedding_id config error: {str(knowledge.embedding_id)}")
        if knowledge.reranker is None:
            raise ValueError(f"reranker_id config error: {str(knowledge.reranker_id)}")

        return ElasticSearchVector(
            index_name=collection_name,
            config=ElasticSearchConfig(**config_dict),
            embedding_config=knowledge.embedding.api_keys[0],
            reranker_config=knowledge.reranker.api_keys[0]
        )



