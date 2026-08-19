"""Unified LangChain document reranking runtime."""

from __future__ import annotations

import asyncio
import time
from copy import deepcopy
from typing import Any

from langchain_core.callbacks import Callbacks
from langchain_core.documents import BaseDocumentCompressor, Document

from redbear_model.contracts import ModelProvider, ResolvedModelConfig
from redbear_model.errors import UnsupportedModelProviderError
from redbear_model.providers.dashscope import load_dashscope_rerank_class
from redbear_model.telemetry import (
    ModelTelemetry,
    NoOpModelTelemetry,
    report_failure_safely,
)

_DEFAULT_JINA_RERANK_URL = "https://api.jina.ai/v1/rerank"
_JINA_PROVIDERS = {
    ModelProvider.XINFERENCE,
    ModelProvider.GPUSTACK,
    ModelProvider.SPEEDBEAR,
}


def _normalize_jina_rerank_url(base_url: str | None) -> str:
    if not base_url:
        return _DEFAULT_JINA_RERANK_URL
    url = base_url.rstrip("/")
    if url.endswith("/v1/rerank"):
        return url
    if url.endswith("/v1"):
        return f"{url}/rerank"
    return f"{url}/v1/rerank"


class _EndpointBoundSession:
    def __init__(self, session: Any, endpoint: str):
        self._session = session
        self._endpoint = endpoint

    def post(self, _url: str, **kwargs: Any) -> Any:
        return self._session.post(self._endpoint, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)


def _load_jina_rerank_class():
    from langchain_community.document_compressors import JinaRerank

    return JinaRerank


class RedBearRerank(BaseDocumentCompressor):
    def __init__(
        self,
        config: ResolvedModelConfig,
        *,
        model: Any | None = None,
        owns_model: bool | None = None,
        telemetry: ModelTelemetry | None = None,
    ):
        object.__setattr__(self, "_config", config)
        object.__setattr__(self, "_telemetry", telemetry or NoOpModelTelemetry())
        object.__setattr__(
            self,
            "_owns_model",
            model is None if owns_model is None else owns_model,
        )
        object.__setattr__(
            self,
            "_model",
            model if model is not None else self._create_model(config),
        )

    def _create_model(self, config: ResolvedModelConfig):
        if config.provider in _JINA_PROVIDERS:
            instance = _load_jina_rerank_class()(
                model=config.model_name,
                jina_api_key=config.api_key.get_secret_value(),
            )
            instance.session = _EndpointBoundSession(
                instance.session,
                _normalize_jina_rerank_url(config.base_url),
            )
            return instance
        if config.provider is ModelProvider.DASHSCOPE:
            instance = load_dashscope_rerank_class()(
                model=config.model_name,
                dashscope_api_key=config.api_key.get_secret_value(),
                **dict(config.provider_params),
            )
            if hasattr(instance, "model"):
                instance.model = config.model_name
            return instance
        raise UnsupportedModelProviderError(config.provider.value)

    def compress_documents(
        self,
        documents: list[Document],
        query: str,
        callbacks: Callbacks | None = None,
        *,
        top_n: int | None = -1,
    ) -> list[Document]:
        compressed: list[Document] = []
        for result in self.rerank(documents, query, top_n=top_n):
            document = documents[result["index"]]
            copied = Document(
                page_content=document.page_content,
                metadata=deepcopy(document.metadata),
            )
            copied.metadata["relevance_score"] = result["relevance_score"]
            compressed.append(copied)
        return compressed

    def rerank(
        self,
        documents: list[str | Document | dict],
        query: str,
        *,
        top_n: int | None = -1,
    ) -> list[dict[str, Any]]:
        started = time.perf_counter()
        try:
            return self._model.rerank(
                documents=documents,
                query=query,
                top_n=top_n,
            )
        except Exception as exc:
            report_failure_safely(
                self._telemetry,
                self._config,
                operation="rerank",
                exc=exc,
                started_at=started,
            )
            raise

    def _close_target(self):
        close = getattr(self._model, "close", None)
        if callable(close):
            return close
        session = getattr(self._model, "session", None)
        close = getattr(session, "close", None)
        return close if callable(close) else None

    def close(self) -> None:
        if not self._owns_model:
            return
        close = self._close_target()
        if close is not None:
            close()

    async def aclose(self) -> None:
        if not self._owns_model:
            return
        aclose = getattr(self._model, "aclose", None)
        if callable(aclose):
            await aclose()
            return
        close = self._close_target()
        if close is not None:
            await asyncio.to_thread(close)
