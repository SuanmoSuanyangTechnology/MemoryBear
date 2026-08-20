import time
from typing import Any, Dict, List, Optional, Sequence, Union
from copy import deepcopy
from langchain_core.documents import BaseDocumentCompressor, Document
from langchain_core.callbacks import Callbacks
from app.core.alert_metric_bridge import (
    report_model_gateway_failure,
    report_model_gateway_success,
)
from app.core.models.base import RedBearModelConfig, get_provider_rerank_class, RedBearModelFactory
from app.models import ModelProvider


_DEFAULT_JINA_RERANK_URL = "https://api.jina.ai/v1/rerank"
_JINA_RERANK_PROVIDERS = frozenset(
    {
        ModelProvider.XINFERENCE.value,
        ModelProvider.GPUSTACK.value,
        ModelProvider.SPEEDBEAR.value,
    }
)


def _normalize_jina_rerank_url(base_url: Optional[str]) -> str:
    if not base_url:
        return _DEFAULT_JINA_RERANK_URL
    url = base_url.rstrip("/")
    if url.endswith("/v1/rerank"):
        return url
    if url.endswith("/v1"):
        return f"{url}/rerank"
    return f"{url}/v1/rerank"


class _EndpointBoundSession:
    """Route a provider session to one immutable rerank endpoint."""

    def __init__(self, session: Any, endpoint: str) -> None:
        self._session = session
        self._endpoint = endpoint

    def post(self, _url: str, **kwargs: Any) -> Any:
        return self._session.post(self._endpoint, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)


class RedBearRerank(BaseDocumentCompressor):
    """ Rerank → 作为 Runnable 插入任意 LCEL 链"""

    def __init__(self, config: RedBearModelConfig):
        self._model = self._create_model(config)
        self._config = config

    def _create_model(self, config: RedBearModelConfig):
        """创建内部模型实例"""
        provider = config.provider.lower()
        model_class = get_provider_rerank_class(config.provider)
        model_params = RedBearModelFactory.get_rerank_model_params(config)
        instance = model_class(**model_params)
        if provider in _JINA_RERANK_PROVIDERS:
            instance.session = _EndpointBoundSession(
                instance.session,
                _normalize_jina_rerank_url(config.base_url),
            )
        # DashScopeRerank.validate_environment always overwrites `model` with the
        # default gte_rerank — restore the user-specified model name here.
        if provider == ModelProvider.DASHSCOPE and hasattr(instance, "model"):
            instance.model = config.model_name
        return instance

    def compress_documents(
            self,
            documents: Sequence[Document],
            query: str,
            callbacks: Optional[Callbacks] = None,
            *,
            top_n: Optional[int] = -1,
    ) -> Sequence[Document]:
        """
        Compress documents using Jina's Rerank API.

        Args:
            documents: A sequence of documents to compress.
            query: The query to use for compressing the documents.
            callbacks: Callbacks to run during the compression process.
            top_n: Number of top documents to return after reranking.

        Returns:
            A sequence of compressed documents.
        """
        compressed = []
        for res in self.rerank(documents, query, top_n=top_n):
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
        started = time.perf_counter()
        try:
            if provider in _JINA_RERANK_PROVIDERS:
                from langchain_community.document_compressors import JinaRerank
                model_instance: JinaRerank = self._model
                result = model_instance.rerank(documents=documents, query=query, top_n=top_n)
            elif provider == ModelProvider.DASHSCOPE:
                from langchain_community.document_compressors.dashscope_rerank import DashScopeRerank
                model_instance: DashScopeRerank = self._model
                result = model_instance.rerank(documents=documents, query=query, top_n=top_n)
            else:
                raise ValueError(f"不支持的模型提供商: {provider}")
        except Exception as exc:
            report_model_gateway_failure(self._config, "rerank", exc, started)
            raise
        report_model_gateway_success(self._config, "rerank", started)
        return result
