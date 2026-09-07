"""Native DashScope adapter for qwen3-vl embedding requests."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from redbear_model.contracts import (
    QWEN3_VL_EMBEDDING_DIMENSION,
    EmbeddingRequest,
    EmbeddingResult,
    ImageEmbeddingContent,
    ResolvedModelConfig,
    TextEmbeddingContent,
)
from redbear_model.errors import (
    InvalidProviderResponseError,
    MultimodalInputLimitError,
    ProviderDependencyMissingError,
    UnsupportedMultimodalModelError,
)
from redbear_model.providers.dashscope import (
    is_dashscope_multimodal_input_limit,
    is_qwen3_vl_embedding,
    resolve_dashscope_native_base_address,
)

_SAFE_USAGE_KEYS = frozenset(
    {"input_tokens", "image_tokens", "text_tokens", "total_tokens", "output_tokens"}
)


def _value(container: Any, key: str, default: Any = None) -> Any:
    if isinstance(container, Mapping):
        return container.get(key, default)
    return getattr(container, key, default)


def _load_call() -> Callable[..., Any]:
    try:
        from dashscope import MultiModalEmbedding
    except ModuleNotFoundError as exc:
        raise ProviderDependencyMissingError("dashscope", "dashscope") from exc
    return MultiModalEmbedding.call


def _safe_usage(response: Any) -> dict[str, int]:
    raw = _value(response, "usage", {})
    if not isinstance(raw, Mapping):
        return {}
    return {
        key: int(value)
        for key, value in raw.items()
        if key in _SAFE_USAGE_KEYS
        and isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    }


class DashScopeMultimodalEmbeddingAdapter:
    def __init__(
        self,
        config: ResolvedModelConfig,
        *,
        call: Callable[..., Any] | None = None,
    ) -> None:
        if not is_qwen3_vl_embedding(config):
            raise UnsupportedMultimodalModelError("qwen3-vl embedding")
        self._config = config
        self._call = call or _load_call()

    def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        contents = []
        for item in request.contents:
            if isinstance(item, TextEmbeddingContent):
                contents.append({"text": item.text})
            elif isinstance(item, ImageEmbeddingContent):
                contents.append({"image": item.data_uri})

        response = self._call(
            model=self._config.model_name,
            input=contents,
            api_key=self._config.api_key.get_secret_value(),
            base_address=resolve_dashscope_native_base_address(self._config.base_url),
            dimension=request.dimension,
            enable_fusion=request.fusion,
            request_timeout=self._config.runtime.timeout_s,
        )
        if is_dashscope_multimodal_input_limit(response):
            raise MultimodalInputLimitError("embedding")
        if _value(response, "status_code") != 200:
            raise InvalidProviderResponseError("embedding", "non-success status")
        output = _value(response, "output")
        embeddings = _value(output, "embeddings")
        if not isinstance(embeddings, Sequence) or isinstance(embeddings, (str, bytes)):
            raise InvalidProviderResponseError("embedding", "missing embeddings")
        if len(embeddings) != 1:
            raise InvalidProviderResponseError("embedding", "expected one fusion result")
        item = embeddings[0]
        if _value(item, "index") != 0 or _value(item, "type") != "fusion":
            raise InvalidProviderResponseError("embedding", "invalid fusion result identity")
        raw_vector = _value(item, "embedding")
        if not isinstance(raw_vector, Sequence) or isinstance(raw_vector, (str, bytes)):
            raise InvalidProviderResponseError("embedding", "missing vector")
        try:
            vector = tuple(float(value) for value in raw_vector)
        except (TypeError, ValueError) as exc:
            raise InvalidProviderResponseError("embedding", "non-numeric vector") from exc
        if len(vector) != QWEN3_VL_EMBEDDING_DIMENSION:
            raise InvalidProviderResponseError("embedding", "unexpected vector dimension")
        if not all(math.isfinite(value) for value in vector):
            raise InvalidProviderResponseError("embedding", "non-finite vector")
        return EmbeddingResult(
            vector=vector,
            dimension=QWEN3_VL_EMBEDDING_DIMENSION,
            usage=_safe_usage(response),
        )


__all__ = ["DashScopeMultimodalEmbeddingAdapter"]
