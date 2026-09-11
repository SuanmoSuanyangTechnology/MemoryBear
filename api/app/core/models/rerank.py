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

    @staticmethod
    def _dashscope_response_value(response: Any, key: str, default: Any = None) -> Any:
        """同时兼容 DashScope 响应对象和普通字典的字段读取。"""
        if response is None:
            return default
        if isinstance(response, dict):
            return response.get(key, default)

        try:
            getter = getattr(response, "get", None)
        except (AttributeError, KeyError):
            getter = None
        if callable(getter):
            try:
                return getter(key, default)
            except (AttributeError, KeyError, TypeError):
                pass

        try:
            return getattr(response, key)
        except (AttributeError, KeyError):
            return default

    @classmethod
    def _dashscope_error_message(cls, response: Any, detail: Optional[str] = None) -> str:
        """保留 DashScope 失败响应中的状态码、错误码和错误信息。"""
        fields = []
        for key in ("status_code", "code", "message"):
            value = cls._dashscope_response_value(response, key)
            if value not in (None, ""):
                fields.append(f"{key}: {value}")
        if detail:
            fields.append(f"detail: {detail}")
        return " \n ".join(fields) if fields else (
            f"DashScope rerank 请求失败: {detail or '未返回可用的错误信息'}"
        )

    def _rerank_with_dashscope(
            self,
            documents: Sequence[Union[str, Document, dict]],
            query: str,
            top_n: int,
    ) -> List[Dict[str, Any]]:
        """直接解析 DashScope 响应，避免第三方适配器掩盖供应商错误。"""
        from dashscope import TextReRank

        if not documents:
            return []

        normalized_documents = [
            document.page_content if isinstance(document, Document) else document
            for document in documents
        ]
        effective_top_n = (
            top_n
            if top_n is None or top_n > 0
            else self._model.top_n
        )
        response = TextReRank.call(
            model=self._config.model_name,
            query=query,
            documents=normalized_documents,
            top_n=effective_top_n,
            return_documents=False,
            api_key=self._config.api_key,
        )

        status_code = self._dashscope_response_value(response, "status_code")
        if status_code not in (None, 200, "200"):
            raise RuntimeError(self._dashscope_error_message(response))

        output = self._dashscope_response_value(response, "output")
        results = self._dashscope_response_value(output, "results")
        if results is None:
            raise RuntimeError(
                self._dashscope_error_message(
                    response,
                    "响应中缺少 output.results",
                )
            )

        parsed_results = []
        for result in results:
            index = self._dashscope_response_value(result, "index")
            relevance_score = self._dashscope_response_value(
                result,
                "relevance_score",
            )
            if index is None or relevance_score is None:
                raise RuntimeError(
                    self._dashscope_error_message(
                        response,
                        "响应中的 rerank 结果缺少 index 或 relevance_score",
                    )
                )
            parsed_results.append(
                {
                    "index": index,
                    "relevance_score": relevance_score,
                }
            )
        return parsed_results

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
            return self._rerank_with_dashscope(documents, query, top_n)
        raise ValueError(f"不支持的模型提供商: {provider}")
