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


def _with_cause(code: str, exc: BaseException) -> KnowledgeError:
    mapped = KnowledgeError.from_code(code)
    mapped.__cause__ = exc
    return mapped


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
