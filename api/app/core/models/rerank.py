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
from app.core.models.network_retry import network_retry
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


def _normalize_dashscope_rerank_url(base_url: str) -> str:
    """将 OpenAI-compatible base_url 规范化为 rerank 完整端点。"""
    url = base_url.rstrip("/")
    if url.endswith("/reranks"):
        return url
    return f"{url}/reranks"


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
            return self._rerank_with_retry(documents, query, top_n, provider)
        except Exception as exc:
            report_model_gateway_failure(self._config, "rerank", exc, started)
            raise

    def _dashscope_rerank_http(
            self,
            documents: Sequence[Union[str, Document, dict]],
            query: str,
            top_n: Optional[int],
    ) -> List[Dict[str, Any]]:
        """DashScope 配置了自定义 base_url 时，直接请求 OpenAI-compatible rerank 接口。

        DashScopeRerank 底层只读取全局服务地址，无法按实例使用数据库中的
        base_url，因此在这里显式传入真实 URL 和 API key。
        """
        import httpx

        docs = [doc.page_content if isinstance(doc, Document) else doc for doc in documents]
        effective_top_n = top_n if top_n is not None and top_n > 0 else 3
        body = {
            "model": self._config.model_name,
            "query": query,
            "documents": docs,
            "top_n": effective_top_n,
        }
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }
        url = _normalize_dashscope_rerank_url(self._config.base_url)
        with httpx.Client(timeout=self._config.timeout, follow_redirects=True) as client:
            response = client.post(url, json=body, headers=headers)
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list) or not results:
            detail = payload.get("message") if isinstance(payload, dict) else None
            raise ValueError(detail or "DashScope rerank 响应中缺少有效的 results")
        return [
            {"index": int(item.get("index")), "relevance_score": item.get("relevance_score")}
            for item in results
        ]

    @network_retry
    def _rerank_with_retry(
            self,
            documents: Sequence[Union[str, Document, dict]],
            query: str,
            top_n: int,
            provider: str,
    ) -> List[Dict[str, Any]]:
        if provider in _JINA_RERANK_PROVIDERS:
            from langchain_community.document_compressors import JinaRerank
            model_instance: JinaRerank = self._model
            return model_instance.rerank(documents=documents, query=query, top_n=top_n)
        if provider == ModelProvider.DASHSCOPE:
            if self._config.base_url:
                return self._dashscope_rerank_http(documents, query, top_n)
            from langchain_community.document_compressors.dashscope_rerank import DashScopeRerank
            model_instance: DashScopeRerank = self._model
            return model_instance.rerank(documents=documents, query=query, top_n=top_n)
        raise ValueError(f"不支持的模型提供商: {provider}")
