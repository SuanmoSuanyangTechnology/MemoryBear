"""Native DashScope adapter for qwen3-vl embedding requests."""

from __future__ import annotations

import json
import logging
import math
import re
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
logger = logging.getLogger(__name__)
_MAX_FAILURE_FIELD_LENGTH = 512
_SENSITIVE_FAILURE_TEXT = re.compile(
    r"(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+"
    r"|\bsk-[A-Za-z0-9_-]+"
    r"|(?:https?://|data:)[^\s\"'<>]+"
    r"|\b[a-z0-9_-]*(?:api[_-]?key|authorization|password|passwd|secret|token|cookie)"
    r"[a-z0-9_-]*[\"']?\s*(?:[:=]|\bis\b)\s*"
    r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;]+)"
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


def _safe_failure_field(
    response: Any,
    field: str,
    api_key: str,
    request: EmbeddingRequest,
) -> str | None:
    value = _value(response, field)
    if not isinstance(value, str):
        return None
    if api_key:
        value = value.replace(api_key, "[REDACTED]")
    for item in request.contents:
        if isinstance(item, TextEmbeddingContent):
            # Providers may echo text literally, JSON-escaped, or with collapsed whitespace.
            variants = {
                item.text,
                " ".join(item.text.split()),
                json.dumps(item.text, ensure_ascii=False)[1:-1],
                json.dumps(item.text, ensure_ascii=True)[1:-1],
            }
        else:
            variants = {item.data_uri}
        for variant in sorted(variants, key=len, reverse=True):
            if variant:
                value = value.replace(variant, "[REDACTED]")
    value = _SENSITIVE_FAILURE_TEXT.sub("[REDACTED]", value)
    return " ".join(value.split())[:_MAX_FAILURE_FIELD_LENGTH]


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

    def _log_failure(self, response: Any, request: EmbeddingRequest) -> None:
        try:
            api_key = self._config.api_key.get_secret_value()
            status_code = _value(response, "status_code")
            diagnostics = {
                "status_code": status_code
                if isinstance(status_code, int) and not isinstance(status_code, bool)
                else None,
                "provider_code": _safe_failure_field(
                    response, "code", api_key, request
                ),
                "provider_message": _safe_failure_field(
                    response, "message", api_key, request
                ),
                "provider_request_id": _safe_failure_field(
                    response, "request_id", api_key, request
                ),
            }
            logger.warning(
                "event=embedding_provider_failure provider=%s model=%s purpose=%s diagnostics=%s",
                self._config.provider.value,
                self._config.model_name,
                request.purpose.value,
                json.dumps(diagnostics, ensure_ascii=True),
            )
        except Exception:  # noqa: BLE001 - diagnostics must not replace the provider failure.
            return

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
        if _value(response, "status_code") != 200:
            self._log_failure(response, request)
        if is_dashscope_multimodal_input_limit(response):
            raise MultimodalInputLimitError("embedding")
        status_code = _value(response, "status_code")
        if status_code != 200:
            # 透出状态码：上游按 "status_code: 401" 文本标记区分鉴权失败与其他 4xx
            raise InvalidProviderResponseError(
                "embedding", f"non-success status (status_code: {status_code})"
            )
        output = _value(response, "output")
        embeddings = _value(output, "embeddings")
        if not isinstance(embeddings, Sequence) or isinstance(embeddings, (str, bytes)):
            raise InvalidProviderResponseError("embedding", "missing embeddings")
        if len(embeddings) != 1:
            raise InvalidProviderResponseError(
                "embedding", "expected one fusion result"
            )
        item = embeddings[0]
        if _value(item, "index") != 0 or _value(item, "type") != "fusion":
            raise InvalidProviderResponseError(
                "embedding", "invalid fusion result identity"
            )
        raw_vector = _value(item, "embedding")
        if not isinstance(raw_vector, Sequence) or isinstance(raw_vector, (str, bytes)):
            raise InvalidProviderResponseError("embedding", "missing vector")
        try:
            vector = tuple(float(value) for value in raw_vector)
        except (TypeError, ValueError) as exc:
            raise InvalidProviderResponseError(
                "embedding", "non-numeric vector"
            ) from exc
        if len(vector) != QWEN3_VL_EMBEDDING_DIMENSION:
            raise InvalidProviderResponseError(
                "embedding", "unexpected vector dimension"
            )
        if not all(math.isfinite(value) for value in vector):
            raise InvalidProviderResponseError("embedding", "non-finite vector")
        return EmbeddingResult(
            vector=vector,
            dimension=QWEN3_VL_EMBEDDING_DIMENSION,
            usage=_safe_usage(response),
        )


__all__ = ["DashScopeMultimodalEmbeddingAdapter"]
