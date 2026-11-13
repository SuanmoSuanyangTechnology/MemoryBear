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


def _classify_model_error(
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


def _with_public_code(
    legacy: KnowledgeError,
    response_code: int,
    cause: BaseException,
) -> KnowledgeError:
    error = KnowledgeError.from_code(
        legacy.code,
        params=legacy.params,
        status_code=legacy.status_code,
        response_code=response_code,
    )
    error.__cause__ = cause
    return error


def map_model_error(
    exc: BaseException,
    *,
    model_role: ModelRole | None = None,
    visibility_proven: bool = False,
) -> KnowledgeError:
    legacy = _legacy_model_error(exc, visibility_proven=visibility_proven)
    if model_role is None or isinstance(exc, KnowledgeError):
        return legacy
    public = _classify_model_error(exc, model_role=model_role, visibility_proven=visibility_proven)
    return _with_public_code(legacy, public.response_code, exc)


def map_text_embedding_error(exc: BaseException) -> KnowledgeError | None:
    legacy = _legacy_embedding_error(exc)
    if legacy is None or isinstance(exc, KnowledgeError):
        return legacy
    public = _classify_model_error(exc, model_role=ModelRole.EMBEDDING)
    return _with_public_code(legacy, public.response_code, exc)


def map_multimodal_error(
    exc: BaseException,
    *,
    operation: Literal["embedding", "rerank"],
) -> KnowledgeError:
    legacy = _legacy_multimodal_error(exc, operation=operation)
    if isinstance(exc, KnowledgeError):
        return legacy
    role = ModelRole.IMAGE_EMBEDDING if operation == "embedding" else ModelRole.IMAGE_RERANK
    public = _classify_model_error(exc, model_role=role)
    return _with_public_code(legacy, public.response_code, exc)


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
            return _with_cause("KB_CONNECTOR_CREDENTIAL_INVALID", exc)
        if status == 429 or isinstance(status, int) and status >= 500:
            return _with_cause("KB_HTTP_UNAVAILABLE", exc)
    if isinstance(exc, (FeishuAuthError, YuqueAuthError)):
        return _with_cause("KB_CONNECTOR_CREDENTIAL_INVALID", exc)
    return _with_cause("KB_HTTP_BAD_GATEWAY", exc)


_FEISHU_INVALID_CREDENTIAL_CODES = {"10003", "10015", "20002"}


def map_connector_check_error(
    exc: Exception, *, feishu_folder_token: str | None = None,
) -> KnowledgeError:
    """Only reclassify evidence that refers to inputs supplied by this caller."""
    from .rag.integrations.feishu.exceptions import FeishuAuthError, FeishuFolderNotFoundError
    from .rag.integrations.yuque.exceptions import YuqueAPIError, YuqueAuthError

    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(exc, YuqueAuthError) and status == 401:
        code = "KB_CONNECTOR_CREDENTIAL_INVALID"
    elif isinstance(exc, YuqueAPIError) and status == 404:
        code = "KB_CONNECTOR_SOURCE_INVALID"
    elif (
        isinstance(exc, FeishuAuthError)
        and status in {200, 400, 401}
        and exc.error_code in _FEISHU_INVALID_CREDENTIAL_CODES
    ):
        code = "KB_CONNECTOR_CREDENTIAL_INVALID"
    elif isinstance(exc, FeishuFolderNotFoundError) and exc.folder_token == feishu_folder_token:
        code = "KB_CONNECTOR_SOURCE_INVALID"
    else:
        return KnowledgeError.from_code(map_connector_error(exc).code, status_code=500)
    return KnowledgeError.from_code(code, status_code=422)


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
    legacy = (
        "KB_RESOURCE_NOT_FOUND"
        if faq
        else {
            "WIKI_NOT_FOUND": "KB_WIKI_NOT_FOUND",
            "WIKI_KNOWLEDGE_UNAVAILABLE": "KB_WIKI_NOT_FOUND",
            "WIKI_DOCUMENT_UNAVAILABLE": "KB_WIKI_NOT_FOUND",
            "WIKI_SCOPE_MISMATCH": "KB_WIKI_NOT_FOUND",
            "WIKI_DISABLED": "KB_WIKI_DISABLED",
            "WIKI_SOURCE_NOT_READY": "KB_WIKI_SOURCE_NOT_READY",
            "WIKI_MODEL_REQUIRED": "KB_WIKI_MODEL_REQUIRED",
        }.get(exc.code, "KB_CONFLICT")
    )
    error = KnowledgeError.from_code(legacy, response_code=ERROR_DEFINITIONS[code].response_code)
    error.__cause__ = exc
    return error


# 内部原因键和重试策略已有任务/部分结果消费者，按旧契约保留。
def _is_remote_rate_limited(exc: ModelInvokeFailedError) -> bool:
    if exc.remote_code in ("RATE_LIMITED", 10004):
        return True
    return exc.http_status == HTTPStatus.TOO_MANY_REQUESTS


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


def _exception_matches(
    exc: BaseException,
    types: tuple[type[BaseException], ...],
) -> bool:
    return any(isinstance(item, types) for item in _exception_chain(exc))


def _legacy_model_error(
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


def _legacy_embedding_error(exc: BaseException) -> KnowledgeError | None:
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
            return _legacy_model_error(cause)
    return None


def _legacy_multimodal_error(
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


__all__ = [
    "map_model_error",
    "map_multimodal_error",
    "map_storage_error",
    "map_text_embedding_error",
]
