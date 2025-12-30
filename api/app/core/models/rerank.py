from typing import Any, Dict, List, Optional, Sequence, Type, Union
from copy import deepcopy
from urllib.parse import urlparse
from langchain_core.documents import BaseDocumentCompressor, Document
from langchain_core.runnables import RunnableSerializable
from langchain_core.callbacks import Callbacks
from app.core.models.base import RedBearModelConfig, get_provider_rerank_class, RedBearModelFactory
from app.models import ModelProvider


class RedBearRerank(BaseDocumentCompressor):
    """ Rerank → 作为 Runnable 插入任意 LCEL 链"""

    def __init__(self, config: RedBearModelConfig):
        self._model = self._create_model(config)
        self._config = config

    def _create_model(self, config: RedBearModelConfig):
        """创建内部模型实例"""
        model_class = get_provider_rerank_class(config.provider)
        model_params = RedBearModelFactory.get_rerank_model_params(config)
        print(model_params)
        return model_class(**model_params)

    def compress_documents(
            self,
            documents: Sequence[Document],
            query: str,
            callbacks: Optional[Callbacks] = None,
    ) -> Sequence[Document]:
        """
        Compress documents using Jina's Rerank API.

        Args:
            documents: A sequence of documents to compress.
            query: The query to use for compressing the documents.
            callbacks: Callbacks to run during the compression process.

        Returns:
            A sequence of compressed documents.
        """
        compressed = []
        for res in self.rerank(documents, query):
            doc = documents[res["index"]]
            doc_copy = Document(doc.page_content, metadata=deepcopy(doc.metadata))
            doc_copy.metadata["relevance_score"] = res["relevance_score"]
            compressed.append(doc_copy)
        return compressed

    def rerank(
            self,
            documents: Sequence[Union[str, Document, dict]],
            query: str,
            *,
            top_n: Optional[int] = -1,
    ) -> List[Dict[str, Any]]:
        provider = self._config.provider.lower()
        if provider in [ModelProvider.XINFERENCE, ModelProvider.GPUSTACK]:
            import langchain_community.document_compressors.jina_rerank as jina_mod

            # 规范化：如果不以 /v1/rerank 结尾，则补齐；若已以 /v1 结尾，则补 /rerank
            def _normalize_jina_base(base_url: Optional[str]) -> Optional[str]:
                if not base_url:
                    return None
                url = base_url.rstrip('/')
                if url.endswith("/v1/rerank"):
                    return url
                if url.endswith("/v1"):
                    return url + "/rerank"
                return url + "/v1/rerank"

            jina_base = _normalize_jina_base(self._config.base_url)
            if jina_base:
                # 设置完整的 rerank 端点，例如 http://host:port/v1/rerank
                jina_mod.JINA_API_URL = jina_base
            from langchain_community.document_compressors import JinaRerank
            model_instance: JinaRerank = self._model
            return model_instance.rerank(documents=documents, query=query, top_n=top_n)
        else:
            raise ValueError(f"不支持的模型提供商: {provider}")
