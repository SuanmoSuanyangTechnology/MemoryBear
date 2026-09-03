"""Normalize LLM provider exceptions into stable application errors."""

from dataclasses import dataclass, replace
from typing import Any, Protocol

import httpx
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
)

from app.core.error_codes import BizCode


@dataclass(frozen=True)
class ClassifiedLLMError:
    """Stable error contract consumed by the service and frontend."""

    error: str
    biz_code: BizCode
    i18n_key: str


@dataclass(frozen=True)
class ProviderErrorInfo:
    """Structured provider diagnostics extracted before normalization."""

    provider: str | None
    sdk_error_type: str
    http_status: int | None
    provider_code: str | None
    request_id: str | None
    retry_after: float | None
    message: str


class ProviderErrorAdapter(Protocol):
    """Contract for provider-specific error normalizers."""

    def classify(
            self,
            info: ProviderErrorInfo,
            exception_chain: tuple[Exception, ...],
    ) -> ClassifiedLLMError | None:
        """Return a classification when this adapter recognizes the error."""


_CLASSIFICATIONS = {
    "authentication_failed": ClassifiedLLMError(
        "authentication_failed", BizCode.API_KEY_INVALID, "errors.llm.authentication_failed"
    ),
    "permission_denied": ClassifiedLLMError(
        "permission_denied", BizCode.PERMISSION_DENIED, "errors.llm.permission_denied"
    ),
    "quota_exceeded": ClassifiedLLMError(
        "quota_exceeded", BizCode.QUOTA_EXCEEDED, "errors.llm.quota_exceeded"
    ),
    "rate_limited": ClassifiedLLMError(
        "rate_limited", BizCode.RATE_LIMIT_EXCEEDED, "errors.llm.rate_limited"
    ),
    "timeout": ClassifiedLLMError(
        "timeout", BizCode.SERVICE_UNAVAILABLE, "errors.llm.timeout"
    ),
    "connection_failed": ClassifiedLLMError(
        "connection_failed", BizCode.SERVICE_UNAVAILABLE, "errors.llm.connection_failed"
    ),
    "invalid_request": ClassifiedLLMError(
        "invalid_request", BizCode.INVALID_PARAMETER, "errors.llm.invalid_request"
    ),
    "model_not_found": ClassifiedLLMError(
        "model_not_found", BizCode.MODEL_NOT_FOUND, "errors.llm.model_not_found"
    ),
    "capability_mismatch": ClassifiedLLMError(
        "capability_mismatch", BizCode.MODEL_CONFIG_INVALID, "errors.llm.capability_mismatch"
    ),
    "server_error": ClassifiedLLMError(
        "server_error", BizCode.SERVICE_UNAVAILABLE, "errors.llm.server_error"
    ),
    "unknown": ClassifiedLLMError(
        "unknown", BizCode.LLM_ERROR, "errors.llm.unknown"
    ),
}

_PROVIDER_CODE_MAP = {
    "invalid_api_key": "authentication_failed",
    "authentication_error": "authentication_failed",
    "permission_denied": "permission_denied",
    "insufficient_permissions": "permission_denied",
    "insufficient_quota": "quota_exceeded",
    "quota_exceeded": "quota_exceeded",
    "billing_not_active": "quota_exceeded",
    "rate_limit_exceeded": "rate_limited",
    "model_not_found": "model_not_found",
    "unsupported_model": "model_not_found",
    "unsupported_parameter": "capability_mismatch",
    "unsupported_value": "capability_mismatch",
    "invalid_request": "invalid_request",
    "invalid_request_error": "invalid_request",
    "request_timeout": "timeout",
    "server_error": "server_error",
    "internal_server_error": "server_error",
}

_DASHSCOPE_CODE_MAP = {
    "invalidapikey": "authentication_failed",
    "accessdenied": "permission_denied",
    "modelaccessdenied": "permission_denied",
    "arrearage": "quota_exceeded",
    "throttling": "rate_limited",
    "ratelimit": "rate_limited",
    "invalidparameter": "invalid_request",
    "invalidinput": "invalid_request",
    "modelnotfound": "model_not_found",
    "invalidmodel": "model_not_found",
    "unsupportedmodel": "model_not_found",
    "requesttimeout": "timeout",
    "internalerror": "server_error",
    "serviceunavailable": "server_error",
}

_QUOTA_PHRASES = (
    "arrearage",
    "欠费",
    "余额不足",
    "额度不足",
    "insufficient balance",
    "insufficient quota",
    "insufficient credit",
    "quota exceeded",
    "quota exhausted",
    "billing limit",
    "credit balance",
    "out of funds",
)

_CAPABILITY_PHRASES = (
    "does not support",
    "not supported",
    "unsupported capability",
    "unsupported modality",
    "capability mismatch",
)

_KEYWORD_RULES = (
    (
        "authentication_failed",
        (
            "invalid_api_key",
            "incorrect_api_key",
            "invalid api key",
            "authentication failed",
            "authentication error",
            "unauthorized",
        ),
    ),
    (
        "permission_denied",
        (
            "api key expired",
            "api key disabled",
            "api key locked",
            "forbidden",
            "permission denied",
            "insufficient permission",
            "access denied",
        ),
    ),
    ("quota_exceeded", _QUOTA_PHRASES),
    ("rate_limited", ("rate limit", "too many requests", "too many request")),
    ("model_not_found", ("model not found", "unknown model", "model_not_found")),
    ("capability_mismatch", _CAPABILITY_PHRASES),
    ("invalid_request", ("invalid_parameter", "bad request", "content-length", "content_length")),
    ("timeout", ("request timeout", "timed out", "timeout")),
    ("connection_failed", ("connection failed", "connection error", "unreachable", "name resolution")),
    ("server_error", ("internal server error", "provider server error", "service unavailable")),
)

_PROVIDER_ADAPTERS: dict[str, ProviderErrorAdapter] = {}


def register_provider_error_adapter(provider: str, adapter: ProviderErrorAdapter) -> None:
    """Register or replace the adapter for a normalized provider name."""
    normalized = _normalize_provider(provider)
    if not normalized:
        raise ValueError("provider must not be empty")
    _PROVIDER_ADAPTERS[normalized] = adapter


def _normalize_provider(provider: Any | None) -> str | None:
    """Normalize a provider enum or string for registry lookup."""
    if provider is None:
        return None
    value = getattr(provider, "value", provider)
    normalized = str(value).strip().lower()
    return normalized or None


def _normalize_code(code: Any | None) -> str | None:
    """Normalize a scalar provider code while preserving meaningful separators."""
    if code is None or isinstance(code, (dict, list, tuple)):
        return None
    normalized = str(code).strip().lower().replace("-", "_").replace(" ", "_")
    return normalized or None


def _as_int(value: Any | None) -> int | None:
    """Convert integer-like status values without accepting booleans."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _exception_chain(error: Exception) -> tuple[Exception, ...]:
    """Collect a bounded, cycle-safe exception cause/context chain."""
    chain: list[Exception] = []
    seen: set[int] = set()
    current: Exception | None = error
    while current is not None and id(current) not in seen and len(chain) < 8:
        chain.append(current)
        seen.add(id(current))
        cause = current.__cause__ or current.__context__
        current = cause if isinstance(cause, Exception) else None
    return tuple(chain)


def _response_mapping(error: Exception) -> dict[str, Any] | None:
    """Extract a dictionary response body from common SDK exception shapes."""
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        return body

    response = getattr(error, "response", None)
    if isinstance(response, dict):
        return response
    response_json = getattr(response, "json", None)
    if callable(response_json):
        try:
            data = response_json()
        except Exception:
            return None
        return data if isinstance(data, dict) else None
    return None


def _mapping_error_code(data: dict[str, Any] | None) -> str | None:
    """Read a machine error code from common flat or nested response bodies."""
    if not data:
        return None
    nested = data.get("error") or data.get("Error")
    if isinstance(nested, dict):
        code = nested.get("code") or nested.get("Code")
        if normalized := _normalize_code(code):
            return normalized
    return _normalize_code(data.get("code") or data.get("Code"))


def _mapping_error_message(data: dict[str, Any] | None) -> str | None:
    """Read a human diagnostic message from common response body shapes."""
    if not data:
        return None
    nested = data.get("error") or data.get("Error")
    if isinstance(nested, str):
        return nested.strip() or None
    if isinstance(nested, dict):
        message = nested.get("message") or nested.get("Message")
        if message:
            return str(message).strip() or None
    message = data.get("message") or data.get("Message")
    return str(message).strip() if message else None


def _headers(error: Exception) -> Any | None:
    """Return response headers when the wrapped response exposes them."""
    response = getattr(error, "response", None)
    if isinstance(response, httpx.Response):
        return response.headers
    return getattr(response, "headers", None)


def _header_value(headers: Any | None, *names: str) -> str | None:
    """Read the first non-empty value from a case-insensitive header mapping."""
    if headers is None or not hasattr(headers, "get"):
        return None
    for name in names:
        value = headers.get(name)
        if value:
            return str(value)
    return None


def extract_provider_error_info(
        error: Exception,
        provider: str | None = None,
) -> tuple[ProviderErrorInfo, tuple[Exception, ...]]:
    """Extract structured diagnostics from an exception and its cause chain."""
    chain = _exception_chain(error)
    http_status: int | None = None
    provider_code: str | None = None
    request_id: str | None = None
    retry_after: float | None = None
    structured_messages: list[str] = []

    for item in chain:
        response = getattr(item, "response", None)
        status = _as_int(getattr(item, "status_code", None))
        if status is None:
            status = _as_int(getattr(response, "status_code", None))
        if status is None and isinstance(response, dict):
            metadata = response.get("ResponseMetadata") or {}
            if isinstance(metadata, dict):
                status = _as_int(metadata.get("HTTPStatusCode"))
        http_status = http_status or status

        response_data = _response_mapping(item)
        provider_code = provider_code or _normalize_code(getattr(item, "code", None))
        provider_code = provider_code or _mapping_error_code(response_data)
        if structured_message := _mapping_error_message(response_data):
            structured_messages.append(structured_message)

        request_id = request_id or getattr(item, "request_id", None)
        if isinstance(response_data, dict):
            request_id = request_id or response_data.get("request_id") or response_data.get("requestId")
        headers = _headers(item)
        request_id = request_id or _header_value(headers, "x-request-id", "request-id")
        retry_after_value = _header_value(headers, "retry-after")
        if retry_after is None and retry_after_value is not None:
            try:
                retry_after = float(retry_after_value)
            except ValueError:
                pass

        if isinstance(response, dict):
            metadata = response.get("ResponseMetadata") or {}
            if isinstance(metadata, dict):
                request_id = request_id or metadata.get("RequestId")

    messages = [str(item).strip() for item in chain if str(item).strip()]
    messages.extend(message for message in structured_messages if message not in messages)
    info = ProviderErrorInfo(
        provider=_normalize_provider(provider),
        sdk_error_type=type(chain[-1]).__name__ if len(chain) > 1 else type(error).__name__,
        http_status=http_status,
        provider_code=provider_code,
        request_id=str(request_id) if request_id else None,
        retry_after=retry_after,
        message=" | ".join(messages).lower(),
    )
    return info, chain


def _contains_any(message: str, phrases: tuple[str, ...]) -> bool:
    """Return whether a normalized diagnostic contains any strict fallback phrase."""
    return any(phrase in message for phrase in phrases)


def _classification_for_provider_code(code: str | None) -> ClassifiedLLMError | None:
    """Map a provider-neutral machine code to the internal error contract."""
    key = _PROVIDER_CODE_MAP.get(code or "")
    return _CLASSIFICATIONS[key] if key else None


def _parse_dashscope_error_fields(exception_chain: tuple[Exception, ...]) -> dict[str, str]:
    """Parse the stable multiline error format emitted by ChatTongyi for 400/401 responses."""
    fields: dict[str, str] = {}
    supported_fields = {"request_id", "status_code", "code", "message"}
    for error in exception_chain:
        for line in str(error).splitlines():
            key, separator, value = line.strip().partition(":")
            normalized_key = key.strip().lower()
            if separator and normalized_key in supported_fields and value.strip():
                fields.setdefault(normalized_key, value.strip())
    return fields


def _classification_for_dashscope_code(code: str | None) -> ClassifiedLLMError | None:
    """Map a native DashScope machine code to the internal stable classification."""
    key = _DASHSCOPE_CODE_MAP.get(code or "")
    return _CLASSIFICATIONS[key] if key else None


class _OpenAIErrorAdapter:
    def classify(
            self,
            info: ProviderErrorInfo,
            exception_chain: tuple[Exception, ...],
    ) -> ClassifiedLLMError | None:
        """Normalize exceptions produced by the OpenAI Python SDK family."""
        if classified := _classification_for_provider_code(info.provider_code):
            return classified
        for error in exception_chain:
            if isinstance(error, AuthenticationError):
                return _CLASSIFICATIONS["authentication_failed"]
            if isinstance(error, PermissionDeniedError):
                return _CLASSIFICATIONS["permission_denied"]
            if isinstance(error, RateLimitError):
                key = "quota_exceeded" if _contains_any(info.message, _QUOTA_PHRASES) else "rate_limited"
                return _CLASSIFICATIONS[key]
            if isinstance(error, APITimeoutError):
                return _CLASSIFICATIONS["timeout"]
            if isinstance(error, APIConnectionError):
                return _CLASSIFICATIONS["connection_failed"]
            if isinstance(error, BadRequestError):
                if _contains_any(info.message, _CAPABILITY_PHRASES):
                    return _CLASSIFICATIONS["capability_mismatch"]
                return _CLASSIFICATIONS["invalid_request"]
        return None


class _GenericHTTPErrorAdapter:
    def classify(
            self,
            info: ProviderErrorInfo,
            exception_chain: tuple[Exception, ...],
    ) -> ClassifiedLLMError | None:
        """Normalize structured HTTP status failures shared across providers."""
        del exception_chain
        if classified := _classification_for_provider_code(info.provider_code):
            return classified

        status = info.http_status
        if status == 400:
            if _contains_any(info.message, _CAPABILITY_PHRASES):
                return _CLASSIFICATIONS["capability_mismatch"]
            return _CLASSIFICATIONS["invalid_request"]
        if status == 401:
            return _CLASSIFICATIONS["authentication_failed"]
        if status == 402:
            return _CLASSIFICATIONS["quota_exceeded"]
        if status == 403:
            return _CLASSIFICATIONS["permission_denied"]
        if status == 404:
            model_phrases = ("model not found", "unknown model", "model_not_found")
            if _contains_any(info.message, model_phrases):
                return _CLASSIFICATIONS["model_not_found"]
            return _CLASSIFICATIONS["connection_failed"]
        if status in (408, 504):
            return _CLASSIFICATIONS["timeout"]
        if status == 409:
            return _CLASSIFICATIONS["server_error"]
        if status == 422:
            return _CLASSIFICATIONS["invalid_request"]
        if status == 429:
            key = "quota_exceeded" if _contains_any(info.message, _QUOTA_PHRASES) else "rate_limited"
            return _CLASSIFICATIONS[key]
        if status is not None and 500 <= status < 600:
            return _CLASSIFICATIONS["server_error"]
        return None


class _DashScopeErrorAdapter:
    def classify(
            self,
            info: ProviderErrorInfo,
            exception_chain: tuple[Exception, ...],
    ) -> ClassifiedLLMError | None:
        """Normalize native ChatTongyi errors without affecting DashScope Omni OpenAI errors."""
        fields = _parse_dashscope_error_fields(exception_chain)
        provider_code = info.provider_code or _normalize_code(fields.get("code"))
        if classified := _classification_for_dashscope_code(provider_code):
            return classified

        http_status = info.http_status or _as_int(fields.get("status_code"))
        request_id = info.request_id or fields.get("request_id")
        message = info.message
        if field_message := fields.get("message"):
            normalized_message = field_message.lower()
            if normalized_message not in message:
                message = f"{message} | {normalized_message}"

        enriched_info = replace(
            info,
            http_status=http_status,
            provider_code=provider_code,
            request_id=request_id,
            message=message,
        )
        return _GENERIC_HTTP_ADAPTER.classify(enriched_info, exception_chain)


_OPENAI_ADAPTER = _OpenAIErrorAdapter()
_GENERIC_HTTP_ADAPTER = _GenericHTTPErrorAdapter()
_DASHSCOPE_ADAPTER = _DashScopeErrorAdapter()

register_provider_error_adapter("dashscope", _DASHSCOPE_ADAPTER)


def classify_llm_error(
        error: Exception,
        provider: str | None = None,
) -> ClassifiedLLMError:
    """Normalize one provider exception using structured adapters and strict fallbacks."""
    info, chain = extract_provider_error_info(error, provider)

    if any(isinstance(item, OpenAIError) for item in chain):
        if classified := _OPENAI_ADAPTER.classify(info, chain):
            return classified

    provider_adapter = _PROVIDER_ADAPTERS.get(info.provider or "")
    if provider_adapter is not None:
        if classified := provider_adapter.classify(info, chain):
            return classified

    if classified := _GENERIC_HTTP_ADAPTER.classify(info, chain):
        return classified

    for key, phrases in _KEYWORD_RULES:
        if _contains_any(info.message, phrases):
            return _CLASSIFICATIONS[key]
    return _CLASSIFICATIONS["unknown"]
