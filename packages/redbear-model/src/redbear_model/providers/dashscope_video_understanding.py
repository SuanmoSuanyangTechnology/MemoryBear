"""Text-only DashScope video understanding over the existing chat transport."""

from __future__ import annotations

import asyncio
import re
import time
from urllib.parse import urlsplit, urlunsplit

import httpx
from langchain_core.messages import AIMessageChunk, HumanMessage
from openai import APIConnectionError, APITimeoutError

from redbear_model.contracts import (
    ModelCapability,
    ModelProvider,
    ModelType,
    ResolvedModelConfig,
)
from redbear_model.errors import (
    EmptyModelOutputError,
    IncompleteModelOutputError,
    InvalidProviderResponseError,
    MediaCallTimeoutError,
    MediaOutputLimitError,
    MediaProviderError,
    RedBearModelError,
    UnsupportedMultimodalModelError,
)
from redbear_model.media_contracts import (
    MediaCallOptions,
    MediaUsage,
    VideoUnderstandingRequest,
    VideoUnderstandingResult,
)
from redbear_model.providers.openai import (
    CompatibleChatOpenAI,
    build_openai_compatible_params,
)
from redbear_model.runtime.client_pool import ModelClientPool

_MODEL = "qwen3.5-omni-plus-2026-03-15"
_ALLOWED_PARAMS = {
    "temperature",
    "max_tokens",
    "seed",
    "top_p",
    "top_k",
    "repetition_penalty",
    "default_headers",
    "streaming",
}
_DETAIL_KEYS = {
    "audio",
    "video",
    "image",
    "text",
    "cache_read",
    "cache_creation",
    "reasoning",
    "audio_tokens",
    "video_tokens",
    "image_tokens",
    "text_tokens",
    "cached_tokens",
    "reasoning_tokens",
}


def _safe_id(value):
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_.:-]{1,256}", value)
        else None
    )


def _isolated_config(config: ResolvedModelConfig) -> ResolvedModelConfig:
    if (
        config.provider is not ModelProvider.DASHSCOPE
        or config.model_type not in {ModelType.LLM, ModelType.CHAT}
        or config.model_name != _MODEL
        or ModelCapability.VIDEO not in config.capabilities
    ):
        raise UnsupportedMultimodalModelError("video understanding")
    if (
        config.deep_thinking
        or config.thinking_budget_tokens is not None
        or config.json_output
        or set(config.provider_params) - _ALLOWED_PARAMS
        or config.provider_params.get("streaming", True) is not True
    ):
        raise ValueError(
            "Video understanding requires text-only streaming without tools, search, or reasoning controls"
        )
    base = config.base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    try:
        parsed = urlsplit(base)
        valid = (
            parsed.scheme in {"https", "http"}
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and parsed.path.rstrip("/") in {"/api/v1", "/compatible-mode/v1"}
            and not any(c.isspace() or ord(c) < 32 for c in base)
        )
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Video understanding requires a DashScope API root URL")
    return config.model_copy(
        deep=True,
        update={
            "base_url": urlunsplit(
                (parsed.scheme, parsed.netloc, "/compatible-mode/v1", "", "")
            ),
            "capabilities": (ModelCapability.VIDEO,),
            "runtime": config.runtime.model_copy(update={"max_retries": 0}),
        },
    )


class _VideoChat(CompatibleChatOpenAI):
    """Preserve the provider identifier discarded by the base chunk mapper."""

    def _convert_chunk_to_generation_chunk(
        self, chunk, default_chunk_class, base_generation_info
    ):
        generation = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        request_id = _safe_id(chunk.get("id"))
        if generation is not None and request_id is not None:
            generation.message.response_metadata["provider_request_id"] = request_id
        return generation


class _VideoConnectionError(MediaProviderError, ConnectionError):
    """Retain gateway classification without retaining a raw transport error."""


class _IncompleteVideoConnectionError(IncompleteModelOutputError, ConnectionError):
    """A redacted transport failure after streamed output began."""


class _Collector:
    def __init__(self, operation, options):
        self.operation = operation
        self.options = options
        self.started = time.perf_counter()
        self.parts = []
        self.chars = 0
        self.bytes = 0
        self.finish = None
        self.request_id = None
        self.usage = MediaUsage()

    def check_time(self):
        if time.perf_counter() - self.started >= self.options.call_timeout_ms / 1000:
            raise MediaCallTimeoutError(self.operation, usage=self.usage)

    def consume(self, chunk):
        self.check_time()
        if not isinstance(chunk, AIMessageChunk):
            raise InvalidProviderResponseError(
                self.operation, "Expected a text message chunk"
            )
        chunk_id = _safe_id(
            chunk.response_metadata.get("provider_request_id")
        ) or _safe_id(chunk.id)
        # LangChain assigns local run IDs to usage-only chunks.
        if chunk_id is not None and not chunk_id.startswith("lc_run--"):
            self.request_id = chunk_id
        self.bytes += len(chunk.model_dump_json().encode("utf-8"))
        if self.bytes > self.options.max_result_bytes:
            raise MediaOutputLimitError(self.operation, usage=self.usage)
        content = chunk.content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif (
                    isinstance(block, dict)
                    and block.get("type") == "text"
                    and isinstance(block.get("text"), str)
                ):
                    parts.append(block["text"])
                else:
                    raise InvalidProviderResponseError(
                        self.operation, "Unexpected non-text output"
                    )
            content = "".join(parts)
        if not isinstance(content, str):
            raise InvalidProviderResponseError(self.operation, "Expected text content")
        self.chars += len(content)
        if self.chars > self.options.max_text_chars:
            raise MediaOutputLimitError(self.operation, usage=self.usage)
        self.parts.append(content)
        reason = chunk.response_metadata.get("finish_reason")
        if reason:
            if self.finish is not None and self.finish != reason:
                raise IncompleteModelOutputError(self.operation, usage=self.usage)
            self.finish = reason
        usage = chunk.usage_metadata
        if usage:
            tokens = {
                key: usage.get(key)
                for key in ("input_tokens", "output_tokens", "total_tokens")
            }
            details = {}
            for side in ("input", "output"):
                for key, value in (usage.get(f"{side}_token_details") or {}).items():
                    if key in _DETAIL_KEYS and type(value) is int and value >= 0:
                        details[f"{side}.{key}"] = value
            try:
                self.usage = MediaUsage(**tokens, token_details=details)
            except ValueError:
                raise InvalidProviderResponseError(
                    self.operation, "Invalid token usage"
                ) from None

    def result(self):
        self.check_time()
        if self.finish != "stop":
            raise IncompleteModelOutputError(self.operation, usage=self.usage)
        text = "".join(self.parts)
        if not text.strip():
            raise EmptyModelOutputError(self.operation, usage=self.usage)
        return VideoUnderstandingResult(
            text=text,
            finish_reason=self.finish,
            provider_request_id=self.request_id,
            usage=self.usage,
            elapsed_ms=int((time.perf_counter() - self.started) * 1000),
        )

    def error(self, exc):
        if isinstance(exc, MediaProviderError):
            if exc.provider_request_id is None:
                exc.provider_request_id = self.request_id
            if exc.usage is None:
                exc.usage = self.usage
            return exc
        if isinstance(exc, RedBearModelError):
            return exc
        if isinstance(exc, (TimeoutError, httpx.TimeoutException, APITimeoutError)):
            cls = MediaCallTimeoutError
        elif isinstance(
            exc, (APIConnectionError, httpx.TransportError, ConnectionError)
        ):
            cls = (
                _IncompleteVideoConnectionError
                if self.parts or self.finish
                else _VideoConnectionError
            )
        else:
            cls = (
                IncompleteModelOutputError
                if self.parts or self.finish
                else MediaProviderError
            )
        status = getattr(exc, "status_code", None)
        return cls(
            self.operation,
            status_code=status if type(status) is int else None,
            provider_request_id=_safe_id(getattr(exc, "request_id", None))
            or self.request_id,
            provider_code=_safe_id(getattr(exc, "code", None)),
            usage=self.usage,
        )


class _DeadlineStream(httpx.SyncByteStream):
    """Check the total budget even when the SDK consumes no complete SSE event."""

    def __init__(self, response: httpx.Response, state: _Collector):
        self.response = response
        self.state = state

    def __iter__(self):
        self.state.check_time()
        for chunk in self.response.stream:
            self.state.check_time()
            yield chunk
            self.state.check_time()

    def close(self):
        self.response.close()


class _DeadlineTransport(httpx.BaseTransport):
    """Delegate to a borrowed pool while owning only the current response."""

    def __init__(self, client: httpx.Client, state: _Collector):
        self.client = client
        self.state = state
        self.response = None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.state.check_time()
        self.response = self.client.send(
            request, stream=True, auth=None, follow_redirects=False
        )
        self.state.check_time()
        return httpx.Response(
            self.response.status_code,
            headers=self.response.headers,
            stream=_DeadlineStream(self.response, self.state),
            extensions=self.response.extensions,
        )

    def close(self):
        if self.response is not None:
            self.response.close()


class DashScopeVideoUnderstandingAdapter:
    def __init__(
        self,
        config: ResolvedModelConfig,
        *,
        client_pool: ModelClientPool,
        options: MediaCallOptions | None = None,
    ):
        self.config = _isolated_config(config)
        self.pool = client_pool
        self.options = options or MediaCallOptions()

    def _call(self, request, collector, *, sync_client=None):
        if not isinstance(request, VideoUnderstandingRequest):
            raise TypeError("A VideoUnderstandingRequest is required")
        clients = self.pool.get_http_clients()
        params = build_openai_compatible_params(self.config, clients)
        if sync_client is not None:
            params["http_client"] = sync_client
        remaining = self.options.call_timeout_ms / 1000 - (
            time.perf_counter() - collector.started
        )
        collector.check_time()
        limits = clients.timeout.as_dict() if clients.timeout is not None else {}
        params.update(
            timeout=httpx.Timeout(
                **{
                    key: min(value, remaining) if value is not None else remaining
                    for key, value in {
                        "connect": remaining,
                        "read": remaining,
                        "write": remaining,
                        "pool": remaining,
                        **limits,
                    }.items()
                }
            ),
            streaming=True,
            stream_usage=True,
            max_retries=0,
        )
        chat = _VideoChat(**params)
        kwargs = {"modalities": ["text"], "stream_options": {"include_usage": True}}
        if request.max_output_tokens is not None:
            kwargs["max_tokens"] = request.max_output_tokens
        messages = [
            HumanMessage(
                content=[
                    {"type": "text", "text": request.prompt},
                    {"type": "video_url", "video_url": {"url": request.video_url}},
                ]
            )
        ]
        return chat, messages, kwargs

    def invoke(self, request: VideoUnderstandingRequest) -> VideoUnderstandingResult:
        state = _Collector("video.invoke", self.options)
        failure = None
        try:
            transport = _DeadlineTransport(self.pool.get_http_clients().sync, state)
            with httpx.Client(transport=transport, trust_env=False) as scoped_client:
                chat, messages, kwargs = self._call(
                    request, state, sync_client=scoped_client
                )
                stream = chat.stream(messages, **kwargs)
                try:
                    for chunk in stream:
                        state.consume(chunk)
                finally:
                    stream.close()
                return state.result()
        except Exception as exc:  # noqa: BLE001 - redact SDK/transport failures
            failure = state.error(exc)
        raise failure from None

    async def ainvoke(
        self, request: VideoUnderstandingRequest
    ) -> VideoUnderstandingResult:
        state = _Collector("video.ainvoke", self.options)
        failure = None
        try:
            async with asyncio.timeout(self.options.call_timeout_ms / 1000):
                chat, messages, kwargs = self._call(request, state)
                stream = chat.astream(messages, **kwargs)
                try:
                    async for chunk in stream:
                        state.consume(chunk)
                finally:
                    await stream.aclose()
                return state.result()
        except Exception as exc:  # noqa: BLE001 - redact SDK/transport failures
            failure = state.error(exc)
        raise failure from None
