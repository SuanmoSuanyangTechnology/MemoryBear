"""Native asynchronous model adapters used by the retrieval pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from langchain_core.messages import BaseMessage
from openai import AsyncOpenAI

from app.core.config import settings
from app.core.rag.models.chunk import DocumentChunk, chunk_retrieval_content
from app.core.rag.retrieval.exceptions import KnowledgeRetrievalConfigError
from app.core.rag.retrieval.models import ModelRuntimeSnapshot
from app.models.models_model import ModelProvider

logger = logging.getLogger(__name__)

_OPENAI_COMPATIBLE_PROVIDERS = frozenset(
    {
        ModelProvider.OPENAI.value,
        ModelProvider.XINFERENCE.value,
        ModelProvider.GPUSTACK.value,
        ModelProvider.SPEEDBEAR.value,
    }
)
_JINA_RERANK_PROVIDERS = frozenset(
    {
        ModelProvider.XINFERENCE.value,
        ModelProvider.GPUSTACK.value,
        ModelProvider.SPEEDBEAR.value,
    }
)
_DEFAULT_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
_DEFAULT_JINA_RERANK_URL = "https://api.jina.ai/v1/rerank"


class AsyncMetadataLLM(Protocol):
    """Minimal native-async LLM interface for metadata filtering."""

    async def invoke(self, messages: Sequence[BaseMessage]) -> str:
        """Return the generated message content."""


class AsyncRetrievalHttpClientProvider:
    """Own the shared async HTTP client used by native retrieval adapters."""

    _client: httpx.AsyncClient | None = None
    _lock: asyncio.Lock | None = None

    @classmethod
    async def get_client(cls) -> httpx.AsyncClient:
        client = cls._client
        if client is not None and not client.is_closed:
            return client

        if cls._lock is None:
            cls._lock = asyncio.Lock()
        async with cls._lock:
            client = cls._client
            if client is None or client.is_closed:
                timeout = _build_timeout()
                cls._client = httpx.AsyncClient(
                    timeout=timeout,
                    follow_redirects=True,
                    limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
                )
            return cls._client

    @classmethod
    async def aclose(cls) -> None:
        """Close the process-level client during application shutdown."""

        client = cls._client
        cls._client = None
        if client is not None and not client.is_closed:
            await client.aclose()


class AsyncRetrievalModelGateway:
    """Dispatch retrieval model calls to provider-native asynchronous clients."""

    async def embed_query(self, embedding: ModelRuntimeSnapshot, query: str) -> list[float]:
        """Embed one retrieval query without a synchronous compatibility fallback."""

        provider = _provider_name(embedding)
        if provider in _OPENAI_COMPATIBLE_PROVIDERS:
            client = await self._openai_client(embedding)
            response = await client.embeddings.create(model=embedding.model_name, input=query)
            return _embedding_from_openai_response(response)
        if provider == ModelProvider.OLLAMA.value:
            client = self._ollama_client(embedding)
            response = await client.embed(model=embedding.model_name, input=[query])
            return _embedding_from_ollama_response(response)
        if provider == ModelProvider.VOLCANO.value:
            client = await self._ark_client(embedding)
            response = await client.multimodal_embeddings.create(
                model=embedding.model_name,
                input=[{"type": "text", "text": query}],
                encoding_format="float",
            )
            return _embedding_from_volcano_response(response)
        if provider == ModelProvider.DASHSCOPE.value:
            return await self._embed_with_dashscope(embedding, query)
        if provider == ModelProvider.BEDROCK.value:
            raise KnowledgeRetrievalConfigError(
                "Bedrock retrieval embedding requires a native async SDK, which is not installed"
            )
        raise KnowledgeRetrievalConfigError(f"Unsupported retrieval embedding provider: {provider}")

    async def rerank(
        self,
        reranker: ModelRuntimeSnapshot,
        query: str,
        docs: Sequence[DocumentChunk],
        top_k: int,
    ) -> list[DocumentChunk]:
        """Rerank documents and retain the legacy degradation behavior on failure."""

        if top_k <= 0 or not docs:
            return []
        try:
            results = await self._request_rerank(reranker, query, docs, top_k)
            reranked: list[DocumentChunk] = []
            for result in sorted(results, key=lambda item: item.relevance_score, reverse=True):
                if not 0 <= result.index < len(docs):
                    continue
                document = docs[result.index]
                if document.metadata is None:
                    document.metadata = {}
                document.metadata["score"] = result.relevance_score
                reranked.append(document)
                if len(reranked) >= top_k:
                    break
            return reranked
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[AsyncRetrieval] rerank failed; using retrieval order provider=%s error_type=%s",
                _provider_name(reranker),
                type(exc).__name__,
            )
            return _rerank_fallback(docs, top_k)

    def metadata_llm(self, model: ModelRuntimeSnapshot) -> AsyncMetadataLLM:
        """Expose a model snapshot as the async LLM adapter metadata filtering needs."""

        return _GatewayMetadataLLM(gateway=self, model=model)

    async def generate_metadata_filters(
        self,
        query: str,
        metadata_defs: Mapping[str, Mapping[str, Any]],
        model: ModelRuntimeSnapshot,
    ) -> list[Any]:
        """Generate normalized metadata filters through the native async adapter."""

        from app.services.metadata_auto_filter_service import MetadataAutoFilterService

        return await MetadataAutoFilterService.generate_filter_groups_async(
            query=query,
            metadata_defs=dict(metadata_defs),
            llm=self.metadata_llm(model),
        )

    async def invoke_metadata(self, model: ModelRuntimeSnapshot, messages: Sequence[BaseMessage]) -> str:
        """Generate metadata-filter JSON through a provider-native async client."""

        provider = _provider_name(model)
        provider_messages = _provider_messages(messages)
        if provider in _OPENAI_COMPATIBLE_PROVIDERS or (
            provider == ModelProvider.DASHSCOPE.value and model.is_omni
        ):
            client = await self._openai_client(model)
            response = await client.chat.completions.create(
                model=model.model_name,
                messages=provider_messages,
                temperature=0,
            )
            return _metadata_content_from_openai_response(response)
        if provider == ModelProvider.OLLAMA.value:
            client = self._ollama_client(model)
            response = await client.chat(
                model=model.model_name,
                messages=provider_messages,
                options={"temperature": 0},
            )
            return _metadata_content_from_ollama_response(response)
        if provider == ModelProvider.VOLCANO.value:
            client = await self._ark_client(model)
            response = await client.chat.completions.create(
                model=model.model_name,
                messages=provider_messages,
                temperature=0,
            )
            return _metadata_content_from_openai_response(response)
        if provider == ModelProvider.DASHSCOPE.value:
            return await self._metadata_with_dashscope(model, provider_messages)
        if provider == ModelProvider.BEDROCK.value:
            raise KnowledgeRetrievalConfigError(
                "Bedrock metadata filtering requires a native async SDK, which is not installed"
            )
        raise KnowledgeRetrievalConfigError(f"Unsupported metadata-filter provider: {provider}")

    async def _openai_client(self, model: ModelRuntimeSnapshot) -> AsyncOpenAI:
        return AsyncOpenAI(
            api_key=model.api_key or "not-needed",
            base_url=model.api_base,
            timeout=_build_timeout(),
            max_retries=settings.LLM_MAX_RETRIES,
            http_client=await AsyncRetrievalHttpClientProvider.get_client(),
        )

    def _ollama_client(self, model: ModelRuntimeSnapshot) -> Any:
        from ollama import AsyncClient

        return AsyncClient(
            host=model.api_base or "http://localhost:11434",
            timeout=_build_timeout(),
        )

    async def _ark_client(self, model: ModelRuntimeSnapshot) -> Any:
        from volcenginesdkarkruntime import AsyncArk

        return AsyncArk(
            api_key=model.api_key,
            base_url=model.api_base,
            timeout=_build_timeout(),
            max_retries=settings.LLM_MAX_RETRIES,
            http_client=await AsyncRetrievalHttpClientProvider.get_client(),
        )

    async def _embed_with_dashscope(self, model: ModelRuntimeSnapshot, query: str) -> list[float]:
        client = await AsyncRetrievalHttpClientProvider.get_client()
        response = await client.post(
            _dashscope_endpoint(model, "services/embeddings/text-embedding/text-embedding"),
            headers=_bearer_headers(model.api_key),
            json={
                "model": model.model_name,
                "input": {"texts": [query]},
                "parameters": {"text_type": "query"},
            },
        )
        response.raise_for_status()
        payload = response.json()
        try:
            embedding = payload["output"]["embeddings"][0]["embedding"]
        except (KeyError, IndexError, TypeError) as exc:
            raise KnowledgeRetrievalConfigError("DashScope returned an invalid embedding response") from exc
        return _float_vector(embedding)

    async def _request_rerank(
        self,
        reranker: ModelRuntimeSnapshot,
        query: str,
        docs: Sequence[DocumentChunk],
        top_k: int,
    ) -> list["_RerankResult"]:
        provider = _provider_name(reranker)
        documents = [chunk_retrieval_content(doc) for doc in docs]
        client = await AsyncRetrievalHttpClientProvider.get_client()
        if provider in _JINA_RERANK_PROVIDERS:
            response = await client.post(
                normalize_jina_rerank_url(reranker.api_base),
                headers=_bearer_headers(reranker.api_key),
                json={"model": reranker.model_name, "query": query, "documents": documents, "top_n": top_k},
            )
            response.raise_for_status()
            payload = response.json()
            return _parse_rerank_results(payload.get("results"))
        if provider == ModelProvider.DASHSCOPE.value:
            response = await client.post(
                _dashscope_endpoint(reranker, "services/rerank/text-rerank/text-rerank"),
                headers=_bearer_headers(reranker.api_key),
                json={
                    "model": reranker.model_name,
                    "input": {"query": query, "documents": documents},
                    "parameters": {"top_n": top_k, "return_documents": False},
                },
            )
            response.raise_for_status()
            payload = response.json()
            output = payload.get("output") if isinstance(payload, dict) else None
            return _parse_rerank_results(output.get("results") if isinstance(output, dict) else None)
        raise KnowledgeRetrievalConfigError(f"Unsupported retrieval rerank provider: {provider}")

    async def _metadata_with_dashscope(
        self,
        model: ModelRuntimeSnapshot,
        messages: list[dict[str, str]],
    ) -> str:
        client = await AsyncRetrievalHttpClientProvider.get_client()
        response = await client.post(
            _dashscope_endpoint(model, "services/aigc/text-generation/generation"),
            headers=_bearer_headers(model.api_key),
            json={
                "model": model.model_name,
                "input": {"messages": messages},
                "parameters": {"result_format": "message", "temperature": 0},
            },
        )
        response.raise_for_status()
        payload = response.json()
        try:
            output = payload["output"]
            choices = output.get("choices") if isinstance(output, dict) else None
            if isinstance(choices, list) and choices:
                message = choices[0].get("message", {})
                content = message.get("content") if isinstance(message, dict) else None
            else:
                content = output.get("text") if isinstance(output, dict) else None
        except (KeyError, IndexError, TypeError) as exc:
            raise KnowledgeRetrievalConfigError("DashScope returned an invalid metadata response") from exc
        return content if isinstance(content, str) else ""


@dataclass(frozen=True)
class _GatewayMetadataLLM:
    gateway: AsyncRetrievalModelGateway
    model: ModelRuntimeSnapshot

    async def invoke(self, messages: Sequence[BaseMessage]) -> str:
        return await self.gateway.invoke_metadata(self.model, messages)


@dataclass(frozen=True)
class _RerankResult:
    index: int
    relevance_score: float


def normalize_jina_rerank_url(base_url: str | None) -> str:
    """Return a request-local Jina-compatible rerank endpoint."""

    if not base_url:
        return _DEFAULT_JINA_RERANK_URL
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1/rerank"):
        return normalized
    if normalized.endswith("/v1"):
        return f"{normalized}/rerank"
    return f"{normalized}/v1/rerank"


def _build_timeout() -> httpx.Timeout:
    timeout = float(settings.LLM_TIMEOUT)
    return httpx.Timeout(timeout=timeout, connect=min(timeout, 60.0))


def _provider_name(model: ModelRuntimeSnapshot) -> str:
    return model.provider.lower().strip()


def _dashscope_endpoint(model: ModelRuntimeSnapshot, path: str) -> str:
    base_url = (model.api_base or _DEFAULT_DASHSCOPE_BASE_URL).rstrip("/")
    return f"{base_url}/{path.lstrip('/')}"


def _bearer_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _float_vector(value: Any) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise KnowledgeRetrievalConfigError("Embedding provider returned an invalid vector")
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise KnowledgeRetrievalConfigError("Embedding provider returned an invalid vector") from exc


def _embedding_from_openai_response(response: Any) -> list[float]:
    try:
        return _float_vector(response.data[0].embedding)
    except (AttributeError, IndexError, TypeError) as exc:
        raise KnowledgeRetrievalConfigError("OpenAI-compatible provider returned an invalid embedding response") from exc


def _embedding_from_ollama_response(response: Any) -> list[float]:
    try:
        embeddings = response["embeddings"] if isinstance(response, Mapping) else response.embeddings
        return _float_vector(embeddings[0])
    except (AttributeError, KeyError, IndexError, TypeError) as exc:
        raise KnowledgeRetrievalConfigError("Ollama returned an invalid embedding response") from exc


def _embedding_from_volcano_response(response: Any) -> list[float]:
    try:
        return _float_vector(response.data.embedding)
    except (AttributeError, TypeError) as exc:
        raise KnowledgeRetrievalConfigError("Volcano returned an invalid embedding response") from exc


def _parse_rerank_results(value: Any) -> list[_RerankResult]:
    if not isinstance(value, list):
        raise KnowledgeRetrievalConfigError("Rerank provider returned an invalid response")
    parsed: list[_RerankResult] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        try:
            parsed.append(
                _RerankResult(index=int(item["index"]), relevance_score=float(item["relevance_score"]))
            )
        except (KeyError, TypeError, ValueError):
            continue
    return parsed


def _rerank_fallback(docs: Sequence[DocumentChunk], top_k: int) -> list[DocumentChunk]:
    fallback = list(docs[:top_k])
    for document in fallback:
        if document.metadata is None:
            document.metadata = {}
        document.metadata.setdefault("score", 0.5)
    return fallback


def _provider_messages(messages: Sequence[BaseMessage]) -> list[dict[str, str]]:
    role_mapping = {"human": "user", "ai": "assistant", "system": "system"}
    converted: list[dict[str, str]] = []
    for message in messages:
        content = message.content
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        converted.append({"role": role_mapping.get(message.type, "user"), "content": content})
    return converted


def _metadata_content_from_openai_response(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise KnowledgeRetrievalConfigError("OpenAI-compatible provider returned an invalid metadata response") from exc
    return content if isinstance(content, str) else ""


def _metadata_content_from_ollama_response(response: Any) -> str:
    try:
        message = response["message"] if isinstance(response, Mapping) else response.message
        content = message["content"] if isinstance(message, Mapping) else message.content
    except (AttributeError, KeyError, TypeError) as exc:
        raise KnowledgeRetrievalConfigError("Ollama returned an invalid metadata response") from exc
    return content if isinstance(content, str) else ""
