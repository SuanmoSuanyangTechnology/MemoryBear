"""Map typed dependency failures to finite knowledge-service errors."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from functools import lru_cache
from typing import Literal

import httpx
import requests
from mem_storage import (
    StorageConfigError,
)
from openai import APIConnectionError, APIStatusError, APITimeoutError
from redbear_model import (
    ChannelSwitchExhaustedError,
    CredentialDecryptError,
    InvalidProviderResponseError,
    ModelAccessDeniedError,
    ModelConfigDeprecatedError,
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
from .model_errors import ModelFailure, ModelRole, model_error


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


def map_model_error(
    exc: BaseException,
    *,
    model_role: ModelRole,
    visibility_proven: bool = False,
) -> KnowledgeError:
    """Classify typed failures, preserving the model role and original cause."""
    if isinstance(exc, asyncio.CancelledError):
        raise exc
    if isinstance(exc, KnowledgeError):
        return exc
    from sqlalchemy.exc import SQLAlchemyError

    failure = ModelFailure.PROCESSING_FAILED
    ark_timeouts, ark_connections, _ = _ark_embedding_errors()
    for cause in _exception_chain(exc):
        if isinstance(cause, SQLAlchemyError):
            return map_database_error(cause)
        if isinstance(
            cause,
            (
                ModelConfigNotFoundError,
                ModelAccessDeniedError,
                ModelConfigInactiveError,
                ModelConfigDeprecatedError,
                ModelCredentialNotFoundError,
                PublicCredentialUnavailableError,
                CredentialDecryptError,
            ),
        ):
            failure = ModelFailure.CONFIG_INVALID
            break
        if isinstance(cause, MultimodalInputLimitError):
            failure = ModelFailure.CAPABILITY_MISMATCH
            break
        if isinstance(cause, (InvalidProviderResponseError, ModelInvokeProtocolError)):
            break
        if isinstance(
            cause,
            (
                ChannelSwitchExhaustedError,
                NoAvailableChannelError,
                ModelInvokeTimeoutError,
                ModelInvokeUnavailableError,
                TimeoutError,
                ConnectionError,
                APITimeoutError,
                APIConnectionError,
                httpx.TimeoutException,
                httpx.NetworkError,
                requests.Timeout,
                requests.ConnectionError,
            )
            + ark_timeouts
            + ark_connections,
        ) or is_provider_rate_limit_error(cause):
            failure = ModelFailure.TEMPORARILY_UNAVAILABLE
            break
        if isinstance(cause, ModelInvokeFailedError):
            remote = _REMOTE_CODE_MODEL_ERRORS.get(cause.remote_code)
            if remote in {
                "KB_RETRIEVAL_MODEL_CHANNEL_UNAVAILABLE",
                "KB_RETRIEVAL_MODEL_CHANNEL_EXHAUSTED",
            }:
                failure = ModelFailure.TEMPORARILY_UNAVAILABLE
                break
            if remote is not None:
                failure = ModelFailure.CONFIG_INVALID
                break
        response = getattr(cause, "response", None)
        status = getattr(cause, "status_code", None) or getattr(response, "status_code", None)
        if status in {401, 403}:
            failure = ModelFailure.CONFIG_INVALID
            break
        if status in {400, 413, 415, 422}:
            failure = ModelFailure.CAPABILITY_MISMATCH
            break
        if status == 429 or isinstance(status, int) and status >= 500:
            failure = ModelFailure.TEMPORARILY_UNAVAILABLE
            break
    mapped = model_error(model_role, failure)
    mapped.__cause__ = exc
    return mapped


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
    """Keep unexpected program errors distinct from recognized embedding failures."""
    if isinstance(exc, KnowledgeError):
        return exc
    ark_timeouts, ark_connections, ark_statuses = _ark_embedding_errors()
    known = (
        (
            APIStatusError,
            httpx.HTTPError,
            requests.RequestException,
            TimeoutError,
            ConnectionError,
            APIConnectionError,
            ModelInvokeTimeoutError,
            ModelInvokeUnavailableError,
            ModelInvokeFailedError,
            ModelInvokeProtocolError,
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
        )
        + ark_timeouts
        + ark_connections
        + ark_statuses
    )
    if any(isinstance(cause, known) for cause in _exception_chain(exc)):
        return map_model_error(exc, model_role=ModelRole.EMBEDDING)
    return None


def map_multimodal_error(
    exc: BaseException,
    *,
    operation: Literal["embedding", "rerank"],
) -> KnowledgeError:
    role = ModelRole.IMAGE_EMBEDDING if operation == "embedding" else ModelRole.IMAGE_RERANK
    return map_model_error(exc, model_role=role)


def map_storage_error(exc: BaseException) -> KnowledgeError:
    """The file record has already been resolved before accessing storage."""
    if isinstance(exc, KnowledgeError):
        return exc
    if isinstance(exc, StorageConfigError):
        return _with_cause("KB_STORAGE_CONFIG_INVALID", exc)
    for cause in _exception_chain(exc):
        if isinstance(cause, FileNotFoundError):
            return _with_cause("KB_STORAGE_OBJECT_MISSING", exc)
        if isinstance(cause, (TimeoutError, httpx.TimeoutException, requests.Timeout)):
            return _with_cause("KB_STORAGE_OPERATION_TIMEOUT", exc)
        if isinstance(cause, (ConnectionError, httpx.NetworkError, requests.ConnectionError)):
            return _with_cause("KB_STORAGE_DEPENDENCY_UNAVAILABLE", exc)
        response = getattr(cause, "response", None)
        status = getattr(cause, "status", None) or getattr(response, "status_code", None)
        provider_code = None
        if isinstance(response, dict):
            provider_code = response.get("Error", {}).get("Code")
            status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if provider_code in {"NoSuchKey", "NoSuchObject"}:
            return _with_cause("KB_STORAGE_OBJECT_MISSING", exc)
        if (
            isinstance(cause, PermissionError)
            or status in {401, 403}
            or provider_code
            in {
                "AccessDenied",
                "InvalidAccessKeyId",
                "SignatureDoesNotMatch",
                "NoSuchBucket",
            }
        ):
            return _with_cause("KB_STORAGE_OPERATION_AUTH_OR_CONFIG_INVALID", exc)
        if status == 429 or isinstance(status, int) and status >= 500:
            return _with_cause("KB_STORAGE_DEPENDENCY_UNAVAILABLE", exc)
    return _with_cause("KB_STORAGE_OPERATION_UNEXPECTED", exc)


def map_database_error(exc: BaseException) -> KnowledgeError:
    from sqlalchemy.exc import DBAPIError, OperationalError

    unavailable = isinstance(exc, OperationalError) or (
        isinstance(exc, DBAPIError) and exc.connection_invalidated
    )
    return _with_cause(
        "KB_DATABASE_CONNECTION_UNAVAILABLE" if unavailable else "KB_DATABASE_OPERATION_FAILED",
        exc,
    )


def map_connector_error(exc: BaseException) -> KnowledgeError:
    from .rag.integrations.feishu.exceptions import FeishuAuthError
    from .rag.integrations.yuque.exceptions import YuqueAuthError

    for cause in _exception_chain(exc):
        if isinstance(cause, (TimeoutError, httpx.TimeoutException)):
            return _with_cause("KB_HTTP_TIMEOUT", exc)
        if isinstance(cause, (ConnectionError, httpx.NetworkError)):
            return _with_cause("KB_HTTP_UNAVAILABLE", exc)
        response = getattr(cause, "response", None)
        status = getattr(response, "status_code", None)
        if status == 404:
            return _with_cause("KB_CONNECTOR_SOURCE_INVALID", exc)
        if status in {401, 403}:
            return _with_cause("KB_FEISHU_AUTH_INVALID", exc)
        if status == 429 or isinstance(status, int) and status >= 500:
            return _with_cause("KB_HTTP_UNAVAILABLE", exc)
    if isinstance(exc, (FeishuAuthError, YuqueAuthError)):
        return _with_cause("KB_FEISHU_AUTH_INVALID", exc)
    return _with_cause("KB_HTTP_BAD_GATEWAY", exc)


_WIKI_CAUSES = {
    "WIKI_NOT_FOUND": "KB_WIKI_NOT_FOUND",
    "WIKI_KNOWLEDGE_UNAVAILABLE": "KB_KNOWLEDGE_NOT_FOUND",
    "WIKI_DOCUMENT_UNAVAILABLE": "KB_DOCUMENT_NOT_FOUND",
    "WIKI_SCOPE_MISMATCH": "KB_WIKI_NOT_FOUND",
    "WIKI_DISABLED": "KB_WIKI_DISABLED",
    "WIKI_SOURCE_NOT_READY": "KB_WIKI_SOURCE_NOT_READY",
    "WIKI_MODEL_REQUIRED": "KB_WIKI_MODEL_REQUIRED",
    "WIKI_SOURCE_CHANGED": "KB_WIKI_SOURCE_CHANGED",
    "WIKI_CONFIG_CHANGED": "KB_WIKI_CONFIG_CHANGED",
    "WIKI_MATERIAL_PLAN_MISMATCH": "KB_WIKI_MATERIAL_PLAN_MISMATCH",
    "WIKI_TAXONOMY_CHANGED": "KB_WIKI_TAXONOMY_CONFLICT",
    "WIKI_IDENTITY_AMBIGUOUS": "KB_WIKI_IDENTITY_AMBIGUOUS",
    "WIKI_MODEL_OUTPUT_INVALID": "KB_LLM_MODEL_PROCESSING_FAILED",
    "WIKI_MODEL_OUTPUT_TRUNCATED": "KB_LLM_MODEL_PROCESSING_FAILED",
    "WIKI_WORKSPACE_UNAVAILABLE": "KB_PRINCIPAL_REFERENCE_INVALID",
    "WIKI_CONTEXT_BUDGET_EXCEEDED": "KB_WIKI_BUDGET_EXCEEDED",
    "WIKI_SOURCE_BUDGET_EXCEEDED": "KB_WIKI_BUDGET_EXCEEDED",
    "WIKI_TOPIC_BUDGET_EXCEEDED": "KB_WIKI_BUDGET_EXCEEDED",
    "WIKI_COMPACTION_BUDGET_EXCEEDED": "KB_WIKI_BUDGET_EXCEEDED",
    "WIKI_EVIDENCE_ITEM_TOO_LARGE": "KB_WIKI_BUDGET_EXCEEDED",
}


def map_wiki_error(exc, *, faq: bool = False) -> KnowledgeError:
    from .errors import ERROR_DEFINITIONS

    code = exc.code if exc.code in ERROR_DEFINITIONS else _WIKI_CAUSES.get(exc.code)
    code = code or ("KB_FAQ_LOAD_FAILED" if faq else "KB_WIKI_UNMAPPED_ERROR")
    error = KnowledgeError.from_code(code)
    if error.grpc_status in {"INTERNAL", "UNAVAILABLE"}:
        error = KnowledgeError.from_code(code, status_code=400 if faq else 409)
    error.__cause__ = exc
    return error


__all__ = [
    "map_model_error",
    "map_multimodal_error",
    "map_storage_error",
    "map_text_embedding_error",
]
