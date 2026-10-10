"""Map typed dependency failures to finite knowledge-service errors."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from functools import lru_cache
from http import HTTPStatus
from typing import Literal

import httpx
import requests
from mem_storage import (
    StorageConfigError,
    StorageConnectionError,
    StorageDeleteError,
    StorageDownloadError,
    StorageError,
    StorageUploadError,
)
from openai import APIConnectionError, APIStatusError, APITimeoutError
from redbear_model import (
    ChannelSwitchExhaustedError,
    CredentialDecryptError,
    InvalidProviderResponseError,
    ModelAccessDeniedError,
    ModelConfigInactiveError,
    ModelConfigNotFoundError,
    ModelCredentialNotFoundError,
    MultimodalInputLimitError,
    NoAvailableChannelError,
    PublicCredentialUnavailableError,
)
from redbear_model.errors import is_provider_rate_limit_error

from .errors import KnowledgeError
from .integrations.model.errors import (
    ModelInvokeFailedError,
    ModelInvokeProtocolError,
    ModelInvokeTimeoutError,
    ModelInvokeUnavailableError,
)


def _with_cause(code: str, exc: BaseException) -> KnowledgeError:
    mapped = KnowledgeError.from_code(code)
    mapped.__cause__ = exc
    return mapped


# 服务侧 invoke 失败码（SSE 帧给名 / JSON 信封给数值，名字与数值双键）→ km KB_* 词表。
# 4011 在 invoke 路径仅由 ModelConfigInactiveError 产生（invoke_service._FAILURE_CODES），
# 因此归 INACTIVE 而非渠道类。
_REMOTE_CODE_MODEL_ERRORS: dict[str | int, str] = {
    "MODEL_NOT_FOUND": "KB_RETRIEVAL_MODEL_NOT_FOUND",
    4003: "KB_RETRIEVAL_MODEL_NOT_FOUND",
    "MODEL_DEPRECATED": "KB_RETRIEVAL_MODEL_INACTIVE",
    4010: "KB_RETRIEVAL_MODEL_INACTIVE",
    "CHANNEL_DISABLED": "KB_RETRIEVAL_MODEL_INACTIVE",
    4011: "KB_RETRIEVAL_MODEL_INACTIVE",
    "NO_AVAILABLE_CHANNEL": "KB_RETRIEVAL_MODEL_CHANNEL_UNAVAILABLE",
    4012: "KB_RETRIEVAL_MODEL_CHANNEL_UNAVAILABLE",
    "SPEEDBEAR_CHANNEL_MISSING": "KB_RETRIEVAL_MODEL_CHANNEL_UNAVAILABLE",
    4013: "KB_RETRIEVAL_MODEL_CHANNEL_UNAVAILABLE",
    "CREDENTIAL_DECRYPT_ERROR": "KB_RETRIEVAL_MODEL_CREDENTIAL_INVALID",
    4014: "KB_RETRIEVAL_MODEL_CREDENTIAL_INVALID",
    "API_KEY_INVALID": "KB_RETRIEVAL_MODEL_CREDENTIAL_INVALID",
    3009: "KB_RETRIEVAL_MODEL_CREDENTIAL_INVALID",
}


def _is_remote_rate_limited(exc: ModelInvokeFailedError) -> bool:
    if exc.remote_code in ("RATE_LIMITED", 10004):
        return True
    return exc.http_status == HTTPStatus.TOO_MANY_REQUESTS


def map_model_error(
    exc: BaseException,
    *,
    visibility_proven: bool = False,
) -> KnowledgeError:
    """Classify model failures without exposing provider or resource details."""

    if isinstance(exc, asyncio.CancelledError):
        raise exc
    if isinstance(exc, KnowledgeError):
        return exc
    if isinstance(exc, ModelConfigNotFoundError):
        return _with_cause("KB_RETRIEVAL_MODEL_NOT_FOUND", exc)
    if isinstance(exc, ModelAccessDeniedError):
        return _with_cause("KB_RETRIEVAL_MODEL_NOT_FOUND", exc)
    if isinstance(exc, ModelConfigInactiveError):
        code = (
            "KB_RETRIEVAL_MODEL_INACTIVE" if visibility_proven else "KB_RETRIEVAL_MODEL_NOT_FOUND"
        )
        return _with_cause(code, exc)
    if isinstance(
        exc,
        (ModelCredentialNotFoundError, PublicCredentialUnavailableError),
    ):
        return _with_cause("KB_RETRIEVAL_MODEL_CREDENTIAL_UNAVAILABLE", exc)
    if isinstance(exc, ChannelSwitchExhaustedError):
        return _with_cause("KB_RETRIEVAL_MODEL_CHANNEL_EXHAUSTED", exc)
    if isinstance(exc, NoAvailableChannelError):
        return _with_cause("KB_RETRIEVAL_MODEL_CHANNEL_UNAVAILABLE", exc)
    if isinstance(exc, CredentialDecryptError):
        return _with_cause("KB_RETRIEVAL_MODEL_CREDENTIAL_INVALID", exc)
    if isinstance(exc, MultimodalInputLimitError):
        return _with_cause("KB_MULTIMODAL_INPUT_LIMIT", exc)
    if isinstance(exc, InvalidProviderResponseError):
        return _with_cause("KB_MODEL_PROVIDER_RESPONSE_INVALID", exc)
    if isinstance(exc, ModelInvokeFailedError):
        code = _REMOTE_CODE_MODEL_ERRORS.get(exc.remote_code)
        return _with_cause(code or "KB_MODEL_UNAVAILABLE", exc)
    return _with_cause("KB_MODEL_UNAVAILABLE", exc)


def _exception_chain(exc: BaseException | None) -> Iterator[BaseException]:
    pending = [exc] if exc is not None else []
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        if isinstance(current.__cause__, BaseException):
            pending.append(current.__cause__)


def _storage_cause_matches(exc: StorageError, types: tuple[type[BaseException], ...]) -> bool:
    causes = (exc.cause, exc.__cause__)
    return any(isinstance(cause, types) for root in causes for cause in _exception_chain(root))


def _exception_matches(
    exc: BaseException,
    types: tuple[type[BaseException], ...],
) -> bool:
    return any(isinstance(item, types) for item in _exception_chain(exc))


@lru_cache(maxsize=1)
def _ark_embedding_errors() -> tuple[tuple[type[BaseException], ...], ...]:
    """Load optional Ark SDK types only when classifying a provider failure."""
    try:
        from volcenginesdkarkruntime._exceptions import (
            ArkAPIConnectionError,
            ArkAPIStatusError,
            ArkAPITimeoutError,
        )
    except ImportError:
        return (), (), ()
    return (ArkAPITimeoutError,), (ArkAPIConnectionError,), (ArkAPIStatusError,)


def _embedding_invoke_failure_code(exc: ModelInvokeFailedError) -> str:
    if _is_remote_rate_limited(exc):
        return "KB_EMBEDDING_RATE_LIMITED"
    code = _REMOTE_CODE_MODEL_ERRORS.get(exc.remote_code)
    if code is not None:
        return code
    status = exc.http_status
    if status is not None and HTTPStatus.INTERNAL_SERVER_ERROR <= status <= 599:
        return "KB_EMBEDDING_SERVICE_UNAVAILABLE"
    return "KB_EMBEDDING_REQUEST_FAILED"


def map_text_embedding_error(exc: BaseException) -> KnowledgeError | None:
    """Classify known provider failures; leave unknown program errors untouched."""
    if isinstance(exc, KnowledgeError):
        return exc
    ark_timeouts, ark_connections, ark_statuses = _ark_embedding_errors()
    for cause in _exception_chain(exc):
        if isinstance(
            cause,
            (APITimeoutError, TimeoutError, httpx.TimeoutException, requests.Timeout)
            + ark_timeouts,
        ):
            return _with_cause("KB_EMBEDDING_TIMEOUT", exc)
        if isinstance(
            cause,
            (
                APIConnectionError,
                ConnectionError,
                httpx.NetworkError,
                httpx.RemoteProtocolError,
                requests.ConnectionError,
            )
            + ark_connections,
        ):
            return _with_cause("KB_EMBEDDING_CONNECTION_FAILED", exc)
        if isinstance(
            cause, (APIStatusError, httpx.HTTPStatusError, requests.HTTPError) + ark_statuses
        ):
            response = getattr(cause, "response", None)
            status = getattr(cause, "status_code", None) or getattr(response, "status_code", None)
            if status == HTTPStatus.TOO_MANY_REQUESTS:
                return _with_cause("KB_EMBEDDING_RATE_LIMITED", exc)
            if status is not None and HTTPStatus.INTERNAL_SERVER_ERROR <= status <= 599:
                return _with_cause("KB_EMBEDDING_SERVICE_UNAVAILABLE", exc)
            return _with_cause("KB_EMBEDDING_REQUEST_FAILED", exc)
        if isinstance(cause, ModelInvokeTimeoutError):
            return _with_cause("KB_EMBEDDING_TIMEOUT", exc)
        if isinstance(cause, ModelInvokeUnavailableError):
            return _with_cause("KB_EMBEDDING_CONNECTION_FAILED", exc)
        if isinstance(cause, ModelInvokeFailedError):
            return _with_cause(_embedding_invoke_failure_code(cause), exc)
        if isinstance(
            cause,
            (
                ChannelSwitchExhaustedError,
                NoAvailableChannelError,
                CredentialDecryptError,
                ModelCredentialNotFoundError,
                PublicCredentialUnavailableError,
                InvalidProviderResponseError,
                ModelConfigNotFoundError,
                ModelAccessDeniedError,
                ModelConfigInactiveError,
                MultimodalInputLimitError,
            ),
        ):
            return map_model_error(cause)
    return None


def map_multimodal_error(
    exc: BaseException,
    *,
    operation: Literal["embedding", "rerank"],
) -> KnowledgeError:
    """Classify image model failures while retaining the legacy transport."""

    if isinstance(exc, asyncio.CancelledError):
        raise exc
    if isinstance(exc, KnowledgeError):
        return exc
    if isinstance(exc, MultimodalInputLimitError):
        return _with_cause("KB_MULTIMODAL_INPUT_LIMIT", exc)

    prefix = f"KB_MULTIMODAL_{operation.upper()}"
    if isinstance(exc, InvalidProviderResponseError):
        return _with_cause(f"{prefix}_RESPONSE_INVALID", exc)
    if isinstance(exc, ModelInvokeProtocolError):
        return _with_cause(f"{prefix}_RESPONSE_INVALID", exc)
    if isinstance(exc, ModelInvokeTimeoutError):
        return _with_cause(f"{prefix}_TIMEOUT", exc)
    if isinstance(exc, ModelInvokeUnavailableError):
        return _with_cause(f"{prefix}_CONNECTION_FAILED", exc)
    if isinstance(exc, ModelInvokeFailedError):
        if _is_remote_rate_limited(exc):
            return _with_cause(f"{prefix}_RATE_LIMITED", exc)
        code = _REMOTE_CODE_MODEL_ERRORS.get(exc.remote_code)
        return _with_cause(code or f"{prefix}_FAILED", exc)
    if is_provider_rate_limit_error(exc):
        return _with_cause(f"{prefix}_RATE_LIMITED", exc)
    if _exception_matches(
        exc,
        (TimeoutError, httpx.TimeoutException, requests.Timeout),
    ):
        return _with_cause(f"{prefix}_TIMEOUT", exc)
    if _exception_matches(
        exc,
        (ConnectionError, httpx.ConnectError, requests.ConnectionError),
    ):
        return _with_cause(f"{prefix}_CONNECTION_FAILED", exc)
    return _with_cause(f"{prefix}_FAILED", exc)


def map_storage_error(exc: StorageError | KnowledgeError) -> KnowledgeError:
    """Classify storage business failures while preserving safe retry metadata."""

    if isinstance(exc, KnowledgeError):
        return exc
    if isinstance(exc, StorageConfigError):
        return _with_cause("KB_STORAGE_CONFIG_INVALID", exc)
    if isinstance(exc, StorageConnectionError):
        if _storage_cause_matches(
            exc,
            (
                TimeoutError,
                ConnectionError,
                httpx.TimeoutException,
                httpx.ConnectError,
                requests.Timeout,
                requests.ConnectionError,
            ),
        ):
            return _with_cause("KB_STORAGE_UNAVAILABLE", exc)
        return _with_cause("KB_STORAGE_OPERATION_FAILED", exc)

    operations: tuple[tuple[type[StorageError], str], ...] = (
        (StorageUploadError, "UPLOAD"),
        (StorageDownloadError, "DOWNLOAD"),
        (StorageDeleteError, "DELETE"),
    )
    for error_type, operation in operations:
        if not isinstance(exc, error_type):
            continue
        if _storage_cause_matches(
            exc,
            (TimeoutError, httpx.TimeoutException, requests.Timeout),
        ):
            return _with_cause(f"KB_STORAGE_{operation}_TIMEOUT", exc)
        if _storage_cause_matches(
            exc,
            (ConnectionError, httpx.ConnectError, requests.ConnectionError),
        ):
            return _with_cause("KB_STORAGE_UNAVAILABLE", exc)
        return _with_cause(f"KB_STORAGE_{operation}_FAILED", exc)
    return _with_cause("KB_STORAGE_OPERATION_FAILED", exc)


__all__ = [
    "map_model_error",
    "map_multimodal_error",
    "map_storage_error",
    "map_text_embedding_error",
]
