"""Unified text and multimodal embedding runtime."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from langchain_core.embeddings import Embeddings

from redbear_model.contracts import (
    EmbeddingPurpose,
    EmbeddingRequest,
    EmbeddingResult,
    ModelProvider,
    ResolvedModelConfig,
    TextEmbeddingContent,
)
from redbear_model.errors import UnsupportedModelProviderError
from redbear_model.providers.bedrock import (
    build_bedrock_params,
    load_bedrock_embedding_class,
)
from redbear_model.providers.dashscope import (
    is_qwen3_vl_embedding,
    load_dashscope_embedding_class,
)
from redbear_model.providers.dashscope_multimodal_embedding import (
    DashScopeMultimodalEmbeddingAdapter,
)
from redbear_model.providers.ollama import (
    build_ollama_params,
    load_ollama_embedding_class,
)
from redbear_model.providers.openai import (
    build_openai_embedding_params,
    load_openai_embedding_class,
)
from redbear_model.providers.volcengine import load_ark_class
from redbear_model.telemetry import (
    ModelTelemetry,
    NoOpModelTelemetry,
    report_failure_safely,
)

from .client_pool import ModelClientPool


class RedBearEmbeddings(Embeddings):
    def __init__(
        self,
        config: ResolvedModelConfig,
        *,
        model: Any | None = None,
        telemetry: ModelTelemetry | None = None,
        client_pool: ModelClientPool | None = None,
        multimodal_adapter: Any | None = None,
    ):
        self._config = config
        self._telemetry = telemetry or NoOpModelTelemetry()
        self._client_pool = client_pool or ModelClientPool(config.runtime)
        self._owns_pool = client_pool is None
        self._is_volcano = config.provider is ModelProvider.VOLCANO
        self._is_qwen3_vl = is_qwen3_vl_embedding(config)
        self._multimodal_adapter = (
            multimodal_adapter
            if multimodal_adapter is not None
            else (
                DashScopeMultimodalEmbeddingAdapter(config)
                if self._is_qwen3_vl
                else None
            )
        )
        self._owns_provider_client = model is None and self._is_volcano
        self._closed = False
        if model is not None:
            self._model = model
            self._client = None
            self._is_volcano = False
        elif self._is_qwen3_vl:
            self._model = None
            self._client = None
        elif self._is_volcano:
            self._model = None
            self._client = load_ark_class()(
                api_key=config.api_key.get_secret_value(),
                base_url=config.base_url,
            )
        else:
            self._model = self._create_model(config)
            self._client = None

    def _create_model(self, config: ResolvedModelConfig):
        if config.provider in {
            ModelProvider.OPENAI,
            ModelProvider.XINFERENCE,
            ModelProvider.GPUSTACK,
            ModelProvider.SPEEDBEAR,
        }:
            params = build_openai_embedding_params(
                config,
                self._client_pool.get_http_clients(),
            )
            params["chunk_size"] = config.runtime.embedding_batch_size
            return load_openai_embedding_class()(**params)
        if config.provider is ModelProvider.DASHSCOPE:
            return load_dashscope_embedding_class()(
                model=config.model_name,
                dashscope_api_key=config.api_key.get_secret_value(),
                max_retries=config.runtime.max_retries,
            )
        if config.provider is ModelProvider.OLLAMA:
            return load_ollama_embedding_class()(**build_ollama_params(config))
        if config.provider is ModelProvider.BEDROCK:
            return load_bedrock_embedding_class()(**build_bedrock_params(config))
        raise UnsupportedModelProviderError(config.provider.value)

    def _observe(self, operation: str, call):
        started = time.perf_counter()
        try:
            return call()
        except Exception as exc:
            report_failure_safely(
                self._telemetry,
                self._config,
                operation=operation,
                exc=exc,
                started_at=started,
            )
            raise

    async def _observe_async(self, operation: str, call):
        started = time.perf_counter()
        try:
            return await call()
        except Exception as exc:
            report_failure_safely(
                self._telemetry,
                self._config,
                operation=operation,
                exc=exc,
                started_at=started,
            )
            raise

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self._is_qwen3_vl:
            return [
                list(
                    self.embed_contents(
                        EmbeddingRequest(
                            purpose=EmbeddingPurpose.INDEX,
                            contents=(TextEmbeddingContent(text=text),),
                        )
                    ).vector
                )
                for text in texts
            ]
        if self._is_volcano:
            contents = [{"type": "text", "text": text} for text in texts]
            return self.embed_multimodal(contents, encoding_format="float")
        return self._observe(
            "embed_documents",
            lambda: self._model.embed_documents(texts),
        )

    def embed_query(self, text: str) -> list[float]:
        if self._is_qwen3_vl:
            return list(
                self.embed_contents(
                    EmbeddingRequest(
                        purpose=EmbeddingPurpose.RETRIEVAL,
                        contents=(TextEmbeddingContent(text=text),),
                    )
                ).vector
            )
        if self._is_volcano:
            result = self.embed_documents([text])
            return result[0] if result else []
        return self._observe("embed_query", lambda: self._model.embed_query(text))

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        if self._is_qwen3_vl or self._is_volcano:
            return await asyncio.to_thread(self.embed_documents, texts)
        return await self._observe_async(
            "aembed_documents",
            lambda: self._model.aembed_documents(texts),
        )

    async def aembed_query(self, text: str) -> list[float]:
        if self._is_qwen3_vl or self._is_volcano:
            return await asyncio.to_thread(self.embed_query, text)
        return await self._observe_async(
            "aembed_query",
            lambda: self._model.aembed_query(text),
        )

    def embed_multimodal(
        self,
        contents: list[dict[str, Any]],
        **kwargs: Any,
    ) -> list[list[float]]:
        if not self._is_volcano:
            raise NotImplementedError(
                f"Multimodal embeddings are not supported by {self._config.provider.value}"
            )

        def invoke():
            response = self._client.multimodal_embeddings.create(
                model=self._config.model_name,
                input=contents,
                **kwargs,
            )
            return [response.data.embedding]

        return self._observe("embed_multimodal", invoke)

    async def aembed_multimodal(
        self,
        contents: list[dict[str, Any]],
        **kwargs: Any,
    ) -> list[list[float]]:
        return await asyncio.to_thread(self.embed_multimodal, contents, **kwargs)

    def embed_contents(self, request: EmbeddingRequest) -> EmbeddingResult:
        if not self._is_qwen3_vl or self._multimodal_adapter is None:
            raise NotImplementedError(
                f"Structured embedding contents are not supported by {self._config.model_name}"
            )
        return self._observe(
            "embed_contents",
            lambda: self._multimodal_adapter.embed(request),
        )

    async def aembed_contents(self, request: EmbeddingRequest) -> EmbeddingResult:
        return await asyncio.to_thread(self.embed_contents, request)

    def embed_text(self, text: str, **kwargs: Any) -> list[float]:
        if not self._is_volcano:
            return self.embed_query(text)
        result = self.embed_multimodal(
            [{"type": "text", "text": text}],
            **kwargs,
        )
        return result[0] if result else []

    def embed_image(self, image_url: str, **kwargs: Any) -> list[float]:
        result = self.embed_multimodal(
            [{"type": "image_url", "image_url": {"url": image_url}}],
            **kwargs,
        )
        return result[0] if result else []

    def embed_video(self, video_url: str, **kwargs: Any) -> list[float]:
        result = self.embed_multimodal(
            [{"type": "video_url", "video_url": {"url": video_url}}],
            **kwargs,
        )
        return result[0] if result else []

    def embed_batch(
        self,
        items: list[str | dict[str, Any]],
        **kwargs: Any,
    ) -> list[list[float]]:
        if all(isinstance(item, str) for item in items):
            return self.embed_documents([str(item) for item in items])
        contents = [
            {"type": "text", "text": item} if isinstance(item, str) else item
            for item in items
        ]
        return self.embed_multimodal(contents, **kwargs)

    def is_multimodal_supported(self) -> bool:
        return self._is_qwen3_vl or self._is_volcano

    def get_provider(self) -> str:
        return self._config.provider.value

    def close(self) -> None:
        if self._closed:
            return
        if self._owns_provider_client:
            close = getattr(self._client, "close", None)
            if callable(close):
                close()
        if self._owns_pool:
            self._client_pool.close()
        self._closed = True

    async def aclose(self) -> None:
        if self._closed:
            return
        if self._owns_provider_client:
            aclose = getattr(self._client, "aclose", None)
            if callable(aclose):
                await aclose()
            else:
                close = getattr(self._client, "close", None)
                if callable(close):
                    await asyncio.to_thread(close)
        if self._owns_pool:
            await self._client_pool.aclose()
        self._closed = True


RedBearMultimodalEmbeddings = RedBearEmbeddings
