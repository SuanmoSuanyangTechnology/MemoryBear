"""Native asynchronous model adapters used by the retrieval pipeline."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from botocore.loaders import create_loader
from botocore.regions import EndpointResolver
from langchain_aws.chat_models.bedrock import ChatPromptAdapter
from langchain_aws.llms.bedrock import LLMInputOutputAdapter
from langchain_core.messages import BaseMessage
from openai import AsyncOpenAI

from app.core.config import settings
from app.core.models.bedrock_model_mapper import normalize_bedrock_model_id
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
_DEFAULT_DASHSCOPE_COMPATIBLE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_DEFAULT_JINA_RERANK_URL = "https://api.jina.ai/v1/rerank"
_BEDROCK_ENDPOINT_RESOLVER = EndpointResolver(create_loader().load_data("endpoints"))
_BEDROCK_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
_BEDROCK_CROSS_REGION_PREFIXES = frozenset(
    {"eu", "us", "us-gov", "apac", "sa", "amer", "global", "jp", "au"}
)


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
            return await self._embed_with_ollama(embedding, query)
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
            return await self._embed_with_bedrock(embedding, query)
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

    def metadata_llm(
        self,
        model: ModelRuntimeSnapshot,
        generation_options: Mapping[str, Any] | None = None,
    ) -> AsyncMetadataLLM:
        """Expose a model snapshot as the async LLM adapter metadata filtering needs."""

        return _GatewayMetadataLLM(
            gateway=self,
            model=model,
            generation_options=generation_options,
        )

    async def generate_metadata_filters(
        self,
        query: str,
        metadata_defs: Mapping[str, Mapping[str, Any]],
        model: ModelRuntimeSnapshot,
        generation_options: Mapping[str, Any] | None = None,
    ) -> list[Any]:
        """Generate normalized metadata filters through the native async adapter."""

        from app.services.metadata_auto_filter_service import MetadataAutoFilterService

        return await MetadataAutoFilterService.generate_filter_groups_async(
            query=query,
            metadata_defs=dict(metadata_defs),
            llm=self.metadata_llm(model, generation_options),
        )

    async def invoke_metadata(
        self,
        model: ModelRuntimeSnapshot,
        messages: Sequence[BaseMessage],
        generation_options: Mapping[str, Any] | None = None,
    ) -> str:
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
                **_openai_metadata_generation_options(model, generation_options),
            )
            return _metadata_content_from_openai_response(response)
        if provider == ModelProvider.OLLAMA.value:
            return await self._metadata_with_ollama(
                model,
                provider_messages,
                generation_options,
            )
        if provider == ModelProvider.VOLCANO.value:
            client = await self._ark_client(model)
            response = await client.chat.completions.create(
                model=model.model_name,
                messages=provider_messages,
                **_openai_metadata_generation_options(model, generation_options),
            )
            return _metadata_content_from_openai_response(response)
        if provider == ModelProvider.DASHSCOPE.value:
            return await self._metadata_with_dashscope(
                model,
                provider_messages,
                generation_options,
            )
        if provider == ModelProvider.BEDROCK.value:
            return await self._metadata_with_bedrock(
                model,
                messages,
                generation_options,
            )
        raise KnowledgeRetrievalConfigError(f"Unsupported metadata-filter provider: {provider}")

    async def _openai_client(self, model: ModelRuntimeSnapshot) -> AsyncOpenAI:
        base_url = model.api_base
        if (
            _provider_name(model) == ModelProvider.DASHSCOPE.value
            and model.is_omni
            and not base_url
        ):
            base_url = _DEFAULT_DASHSCOPE_COMPATIBLE_BASE_URL
        return AsyncOpenAI(
            api_key=model.api_key or "not-needed",
            base_url=base_url,
            timeout=_build_timeout(),
            max_retries=settings.LLM_MAX_RETRIES,
            http_client=await AsyncRetrievalHttpClientProvider.get_client(),
        )

    async def _embed_with_ollama(self, model: ModelRuntimeSnapshot, query: str) -> list[float]:
        client = await AsyncRetrievalHttpClientProvider.get_client()
        response = await client.post(
            _ollama_endpoint(model, "api/embed"),
            headers=_ollama_headers(),
            json={"model": model.model_name, "input": [query]},
        )
        response.raise_for_status()
        return _embedding_from_ollama_response(response.json())

    async def _metadata_with_ollama(
        self,
        model: ModelRuntimeSnapshot,
        messages: Sequence[dict[str, str]],
        generation_options: Mapping[str, Any] | None,
    ) -> str:
        client = await AsyncRetrievalHttpClientProvider.get_client()
        response = await client.post(
            _ollama_endpoint(model, "api/chat"),
            headers=_ollama_headers(),
            json={
                "model": model.model_name,
                "messages": messages,
                "tools": [],
                "stream": False,
                "options": _ollama_metadata_generation_options(generation_options),
            },
        )
        response.raise_for_status()
        return _metadata_content_from_ollama_response(response.json())

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

    async def _embed_with_bedrock(self, model: ModelRuntimeSnapshot, query: str) -> list[float]:
        model_id = normalize_bedrock_model_id(model.model_name)
        provider = _bedrock_provider(model_id)
        if provider == "cohere":
            payload = {"input_type": "search_query", "texts": [query]}
        elif provider == "amazon" and "nova" in model_id and "embed" in model_id:
            payload = {
                "taskType": "SINGLE_EMBEDDING",
                "singleEmbeddingParams": {
                    "embeddingPurpose": "GENERIC_INDEX",
                    "text": {"truncationMode": "END", "value": query},
                },
            }
        else:
            payload = {"inputText": query}
        response = await self._bedrock_request(model, model_id, "invoke", payload)
        return _embedding_from_bedrock_response(response)

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
        generation_options: Mapping[str, Any] | None = None,
    ) -> str:
        client = await AsyncRetrievalHttpClientProvider.get_client()
        response = await client.post(
            _dashscope_endpoint(model, "services/aigc/text-generation/generation"),
            headers=_metadata_headers(model.api_key, generation_options),
            json={
                "model": model.model_name,
                "input": {"messages": messages},
                "parameters": _dashscope_metadata_generation_options(generation_options),
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

    async def _metadata_with_bedrock(
        self,
        model: ModelRuntimeSnapshot,
        messages: Sequence[BaseMessage],
        generation_options: Mapping[str, Any] | None = None,
    ) -> str:
        model_id = normalize_bedrock_model_id(model.model_name)
        provider = _bedrock_provider(model_id)
        response = await self._bedrock_request(
            model,
            model_id,
            "invoke",
            _bedrock_metadata_payload(provider, model_id, messages, generation_options),
        )
        return _metadata_content_from_bedrock_response(provider, response)

    async def _bedrock_request(
        self,
        model: ModelRuntimeSnapshot,
        model_id: str,
        operation: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        credentials = _bedrock_credentials(model.api_key)
        region = _bedrock_region(model.api_base)
        endpoint = _bedrock_runtime_endpoint(region)
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        client = await AsyncRetrievalHttpClientProvider.get_client()
        max_retries = _bedrock_max_retries()
        for attempt in range(max_retries + 1):
            request = AWSRequest(
                method="POST",
                url=f"{endpoint}/model/{quote(model_id, safe=':.')}/{operation}",
                data=body,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
            SigV4Auth(credentials, "bedrock", region).add_auth(request)
            try:
                response = await client.post(
                    request.url,
                    headers=dict(request.headers.items()),
                    content=body,
                )
            except httpx.TransportError:
                if attempt >= max_retries:
                    raise
                await asyncio.sleep(_bedrock_retry_delay(attempt))
                continue
            if response.status_code in _BEDROCK_RETRYABLE_STATUS_CODES and attempt < max_retries:
                await response.aclose()
                await asyncio.sleep(_bedrock_retry_delay(attempt))
                continue
            response.raise_for_status()
            try:
                result = response.json()
            except json.JSONDecodeError as exc:
                raise KnowledgeRetrievalConfigError("Bedrock returned an invalid JSON response") from exc
            if not isinstance(result, Mapping):
                raise KnowledgeRetrievalConfigError("Bedrock returned an invalid response")
            return result
        raise RuntimeError("Bedrock native async request exhausted without a response")


@dataclass(frozen=True)
class _GatewayMetadataLLM:
    gateway: AsyncRetrievalModelGateway
    model: ModelRuntimeSnapshot
    generation_options: Mapping[str, Any] | None = None

    async def invoke(self, messages: Sequence[BaseMessage]) -> str:
        return await self.gateway.invoke_metadata(
            self.model,
            messages,
            self.generation_options,
        )


@dataclass(frozen=True)
class _RerankResult:
    index: int
    relevance_score: float


def _metadata_generation_values(
    generation_options: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if generation_options is None:
        return {"temperature": 0}
    return {
        key: value
        for key, value in generation_options.items()
        if isinstance(key, str) and value is not None
    }


def _openai_metadata_generation_options(
    model: ModelRuntimeSnapshot,
    generation_options: Mapping[str, Any] | None,
) -> dict[str, Any]:
    values = _metadata_generation_values(generation_options)
    allowed = {
        "temperature",
        "max_tokens",
        "top_p",
        "seed",
        "frequency_penalty",
        "presence_penalty",
        "stop",
        "response_format",
    }
    request_options = {
        key: value
        for key, value in values.items()
        if key in allowed
    }
    if _provider_name(model) == ModelProvider.VOLCANO.value:
        request_options.pop("seed", None)
    default_headers = values.get("default_headers")
    if isinstance(default_headers, Mapping):
        request_options["extra_headers"] = {
            str(key): str(value)
            for key, value in default_headers.items()
        }
    _apply_openai_thinking_options(model, values, request_options)
    return request_options


def _apply_openai_thinking_options(
    model: ModelRuntimeSnapshot,
    values: Mapping[str, Any],
    request_options: dict[str, Any],
) -> None:
    if "deep_thinking" not in values or "thinking" not in model.capability:
        return

    enabled = bool(values["deep_thinking"])
    budget = values.get("thinking_budget_tokens")
    provider = _provider_name(model)
    if provider == ModelProvider.VOLCANO.value:
        request_options["extra_body"] = {"thinking": {"type": "enabled" if enabled else "disabled"}}
        effort = _reasoning_effort(budget)
        if enabled and effort is not None:
            request_options["reasoning_effort"] = effort
        return
    if provider == ModelProvider.SPEEDBEAR.value:
        if not enabled:
            request_options["reasoning_effort"] = "none"
            return
        request_options["reasoning_effort"] = _reasoning_effort(budget) or "minimal"
        return

    extra_body: dict[str, Any] = {"enable_thinking": enabled}
    if enabled and budget is not None:
        extra_body["thinking_budget"] = budget
    request_options["extra_body"] = extra_body


def _ollama_metadata_generation_options(
    generation_options: Mapping[str, Any] | None,
) -> dict[str, Any]:
    values = _metadata_generation_values(generation_options)
    options = {
        key: values[key]
        for key in ("temperature", "top_p", "top_k", "seed", "stop")
        if key in values
    }
    if "max_tokens" in values:
        options["num_predict"] = values["max_tokens"]
    if "repetition_penalty" in values:
        options["repeat_penalty"] = values["repetition_penalty"]
    return options


def _dashscope_metadata_generation_options(
    generation_options: Mapping[str, Any] | None,
) -> dict[str, Any]:
    values = _metadata_generation_values(generation_options)
    options = {"result_format": "message"}
    for key in (
        "temperature",
        "max_tokens",
        "top_p",
        "top_k",
        "seed",
        "repetition_penalty",
        "enable_search",
        "stop",
    ):
        if key in values:
            options[key] = values[key]
    if "deep_thinking" in values:
        options["enable_thinking"] = bool(values["deep_thinking"])
    if values.get("deep_thinking") and "thinking_budget_tokens" in values:
        options["thinking_budget"] = values["thinking_budget_tokens"]
    response_format = values.get("response_format")
    if isinstance(response_format, Mapping):
        options["response_format"] = dict(response_format)
    return options


def _metadata_headers(
    api_key: str,
    generation_options: Mapping[str, Any] | None,
) -> dict[str, str]:
    headers = _bearer_headers(api_key)
    values = _metadata_generation_values(generation_options)
    default_headers = values.get("default_headers")
    if isinstance(default_headers, Mapping):
        headers.update({str(key): str(value) for key, value in default_headers.items()})
    return headers


def _reasoning_effort(budget: Any) -> str | None:
    if not isinstance(budget, int):
        return None
    if budget <= 2048:
        return "low"
    if budget <= 4096:
        return "medium"
    return "high"


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


def _ollama_endpoint(model: ModelRuntimeSnapshot, path: str) -> str:
    base_url = _normalize_ollama_base_url(model.api_base or "http://localhost:11434")
    return f"{base_url}/{path.lstrip('/')}"


def _normalize_ollama_base_url(base_url: str) -> str:
    default_port = 11434
    scheme, _, hostport = base_url.partition("://")
    if not hostport:
        scheme, hostport = "http", base_url
    elif scheme == "http":
        default_port = 80
    elif scheme == "https":
        default_port = 443

    parsed = urlsplit(f"{scheme}://{hostport}")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or default_port
    try:
        if isinstance(ipaddress.ip_address(host), ipaddress.IPv6Address):
            host = f"[{host}]"
    except ValueError:
        pass

    path = parsed.path.strip("/")
    if path:
        return f"{scheme}://{host}:{port}/{path}"
    return f"{scheme}://{host}:{port}"


def _ollama_headers() -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if api_key := os.getenv("OLLAMA_API_KEY"):
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _build_timeout() -> httpx.Timeout:
    timeout = float(settings.LLM_TIMEOUT)
    return httpx.Timeout(timeout=timeout, connect=min(timeout, 60.0))


def _provider_name(model: ModelRuntimeSnapshot) -> str:
    return model.provider.lower().strip()


def _bedrock_provider(model_id: str) -> str:
    parts = model_id.split(".", 2)
    if len(parts) > 1 and parts[0].lower() in _BEDROCK_CROSS_REGION_PREFIXES:
        return parts[1].lower()
    return parts[0].lower()


def _bedrock_credentials(api_key: str) -> Credentials:
    credentials = api_key.split(":", 2)
    if len(credentials) >= 2 and credentials[0] and credentials[1]:
        session_token = credentials[2] if len(credentials) == 3 and credentials[2] else None
        return Credentials(credentials[0], credentials[1], session_token)

    access_key_id = api_key or os.getenv("AWS_ACCESS_KEY_ID")
    secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    if access_key_id and secret_access_key:
        return Credentials(
            access_key_id,
            secret_access_key,
            os.getenv("AWS_SESSION_TOKEN"),
        )
    raise KnowledgeRetrievalConfigError(
        "Bedrock native async retrieval requires api_key formatted as "
        "access_key_id:secret_access_key[:session_token] or static AWS environment credentials"
    )


def _bedrock_region(api_base: str | None) -> str:
    region = (api_base or "us-east-1").strip()
    if not region or "://" in region or "/" in region:
        raise KnowledgeRetrievalConfigError("Bedrock api_base must be an AWS region")
    return region


def _bedrock_max_retries() -> int:
    try:
        return max(0, int(os.getenv("BEDROCK_MAX_RETRIES", "2")))
    except ValueError:
        return 2


def _bedrock_retry_delay(attempt: int) -> float:
    return min(0.25 * (2**attempt), 2.0)


def _bedrock_runtime_endpoint(region: str) -> str:
    endpoint = _BEDROCK_ENDPOINT_RESOLVER.construct_endpoint(
        "bedrock-runtime",
        region,
    )
    hostname = endpoint.get("hostname") if endpoint else None
    if not isinstance(hostname, str) or not hostname:
        raise KnowledgeRetrievalConfigError(f"Bedrock does not support region={region}")
    return f"https://{hostname}"


def _bedrock_metadata_payload(
    provider: str,
    model_id: str,
    messages: Sequence[BaseMessage],
    generation_options: Mapping[str, Any] | None,
) -> dict[str, Any]:
    values = _metadata_generation_values(generation_options)
    model_kwargs = {
        key: values[key]
        for key in ("top_p", "top_k", "seed", "response_format")
        if key in values
    }
    if "stop" in values:
        model_kwargs["stop_sequences"] = values["stop"]
    if values.get("deep_thinking"):
        thinking: dict[str, Any] = {"type": "enabled"}
        if "thinking_budget_tokens" in values:
            thinking["budget_tokens"] = values["thinking_budget_tokens"]
        model_kwargs["thinking"] = thinking

    max_tokens = values.get("max_tokens")
    temperature = values.get("temperature")
    normalized_messages = list(messages)
    prompt: str | None = None
    system: str | list[dict[str, Any]] | None = None
    formatted_messages: list[dict[str, Any]] | None = None
    if provider == "anthropic":
        formatted = ChatPromptAdapter.format_messages(provider, normalized_messages)
        if not isinstance(formatted, tuple):
            raise KnowledgeRetrievalConfigError("Bedrock returned an invalid Anthropic message adapter")
        system, formatted_messages = formatted
    elif provider in {"openai", "qwen"}:
        formatted = ChatPromptAdapter.format_messages(provider, normalized_messages)
        if not isinstance(formatted, list):
            raise KnowledgeRetrievalConfigError("Bedrock returned an invalid message adapter")
        formatted_messages = formatted
    else:
        prompt = ChatPromptAdapter.convert_messages_to_prompt(
            provider,
            normalized_messages,
            model_id,
        )

    try:
        payload = LLMInputOutputAdapter.prepare_input(
            provider=provider,
            model_kwargs=model_kwargs,
            prompt=prompt,
            system=system,
            messages=formatted_messages,
            max_tokens=max_tokens if isinstance(max_tokens, int) else None,
            temperature=temperature if isinstance(temperature, (int, float)) else None,
        )
    except (NotImplementedError, TypeError, ValueError) as exc:
        raise KnowledgeRetrievalConfigError(
            f"Bedrock metadata filtering does not support model provider={provider}"
        ) from exc
    if not isinstance(payload, dict):
        raise KnowledgeRetrievalConfigError("Bedrock returned an invalid metadata request adapter")
    return payload


def _embedding_from_bedrock_response(response: Mapping[str, Any]) -> list[float]:
    try:
        direct_embedding = response.get("embedding")
        if direct_embedding is not None:
            return _float_vector(direct_embedding)
        embeddings = response.get("embeddings")
        if isinstance(embeddings, Mapping):
            return _float_vector(embeddings["float"][0])
        return _float_vector(embeddings[0]["embedding"] if isinstance(embeddings[0], Mapping) else embeddings[0])
    except (KeyError, IndexError, TypeError) as exc:
        raise KnowledgeRetrievalConfigError("Bedrock returned an invalid embedding response") from exc


def _metadata_content_from_bedrock_response(provider: str, response: Mapping[str, Any]) -> str:
    try:
        parsed = LLMInputOutputAdapter.prepare_output(
            provider,
            {"body": BytesIO(json.dumps(response).encode("utf-8"))},
        )
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
        raise KnowledgeRetrievalConfigError("Bedrock returned an invalid metadata response") from exc
    content = parsed.get("text")
    return content if isinstance(content, str) else ""


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
