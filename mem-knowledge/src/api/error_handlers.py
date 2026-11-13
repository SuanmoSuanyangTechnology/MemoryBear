"""Knowledge error serialization and explicit legacy HTTP compatibility."""

from __future__ import annotations

from collections.abc import Mapping

from error_core.codes import BizCode
from error_core.diagnostics import format_diagnostics
from error_core.knowledge import knowledge_message, validation_diagnostic
from error_core.knowledge_upload import is_versioned_upload, upload_error_response
from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from mem_storage import StorageError
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException

from ..error_mapping import map_storage_error
from ..errors import KnowledgeError
from ..i18n import normalize_locale, resolve_locale, translate_legacy
from ..request_logging import log_request_failure
from ..services.upload_deduplication import DuplicateUploadError
from ..trace import TRACE_ID_HEADER, get_trace_id, normalize_trace_id
from .schemas.common import fail

_HTTP_ERRORS = {
    400: "KB_REQUEST_MALFORMED",
    401: "KB_HTTP_UNAUTHORIZED",
    403: "KB_HTTP_FORBIDDEN",
    404: "KB_ROUTE_NOT_FOUND",
    405: "KB_HTTP_METHOD_NOT_ALLOWED",
    409: "KB_CONFLICT",
    413: "KB_HTTP_TOO_LARGE",
    422: "KB_HTTP_VALIDATION_ERROR",
    429: "KB_HTTP_RATE_LIMITED",
    500: "KB_INTERNAL_ERROR",
    502: "KB_HTTP_BAD_GATEWAY",
    503: "KB_HTTP_UNAVAILABLE",
    504: "KB_HTTP_TIMEOUT",
}


# The actual endpoint identity is independent of router prefixes and query strings.
# ChunkRetrieve is the only public request schema with rerank fields today.
_VALIDATION_CONTRACTS = {
    ("src.api.routes.chunk", "retrieve_chunks"): "KB_RETRIEVAL_REQUEST_INVALID",
}
_RERANK_FIELDS = frozenset({"rerank_mode", "rerank_weights"})


# These legacy HTTP operations address the missing resource directly. Keep the
# shared defaults for references supplied in a payload, background jobs and gRPC.
_RESOURCE_NOT_FOUND = {
    "get_knowledge": "KB_KNOWLEDGE_NOT_FOUND",
    "update_knowledge": "KB_KNOWLEDGE_NOT_FOUND",
    "delete_knowledge": "KB_KNOWLEDGE_NOT_FOUND",
    "get_document": "KB_DOCUMENT_NOT_FOUND",
    "update_document": "KB_DOCUMENT_NOT_FOUND",
    "delete_document": "KB_DOCUMENT_NOT_FOUND",
    "get_file": "KB_FILE_NOT_FOUND",
    "update_file": "KB_FILE_NOT_FOUND",
    "delete_file": "KB_FILE_NOT_FOUND",
}
_KNOWLEDGE_CONFLICTS = {
    "create_knowledge": {"KB_KNOWLEDGE_NAME_EXISTS", "KB_EXTERNAL_ID_EXISTS"},
    "update_knowledge": {"KB_KNOWLEDGE_NAME_EXISTS"},
}


# These causes have one server-side meaning even when raised from shared services.
_INTERNAL_FAILURES = frozenset({
    "KB_KNOWLEDGE_CREATOR_NOT_FOUND",
    "KB_KNOWLEDGE_SHARE_REFERENCE_INCOMPLETE",
    "KB_TASK_NAME_CONFIG_INVALID",
    "KB_TASK_QUEUE_CONFIG_INVALID",
    "KB_TASK_DISPATCH_CONFIG_INVALID",
    "KB_VISION_MODEL_OUTPUT_EMPTY",
})


def _business_http_status(request: Request, error: KnowledgeError) -> int:
    if not 400 <= error.status_code < 500:
        return error.status_code
    endpoint = request.scope.get("endpoint")
    operation = getattr(endpoint, "__name__", "")
    if error.http_status_override is not None:
        if (
            operation == "get_knowledge_graph_entity_types"
            and getattr(endpoint, "__module__", "") == "src.api.routes.knowledge"
            and error.http_status_override == 503
        ):
            # The gateway retries 503 on GET; this endpoint invokes a billable model.
            return error.status_code
        return error.http_status_override
    if error.code in _INTERNAL_FAILURES:
        return 500
    if error.status_code == 400:
        module = getattr(endpoint, "__module__", "")
        if module in {"src.api.routes.knowledge", "src.api.routes.document", "src.api.routes.file"}:
            if error.code == _RESOURCE_NOT_FOUND.get(operation):
                return 404
            if error.code in _KNOWLEDGE_CONFLICTS.get(operation, ()):
                return 409
    return error.status_code


def request_locale(request: Request) -> str:
    selected = getattr(request.state, "knowledge_locale", None)
    if normalize_locale(selected):
        return selected
    selected = resolve_locale(
        explicit=request.headers.get("X-Language-Type"),
        accepted=request.headers.get("Accept-Language"),
    )
    request.state.knowledge_locale = selected
    return selected


def _response(
    request: Request,
    *,
    status_code: int,
    response_code: int,
    message: str,
    headers: Mapping[str, str] | None = None,
    diagnostic: str = "",
) -> JSONResponse:
    response = JSONResponse(
        status_code=status_code,
        headers=dict(headers or {}),
        content=fail(code=response_code, msg=message, error=diagnostic),
    )
    response.headers[TRACE_ID_HEADER] = getattr(request.state, "trace_id", get_trace_id())
    response.headers["Content-Language"] = request_locale(request)
    vary = response.headers.get("Vary", "")
    if "*" not in vary:
        for header in ("X-Language-Type", "Accept-Language"):
            if header.lower() not in {v.strip().lower() for v in vary.split(",")}:
                vary = f"{vary}, {header}" if vary else header
        response.headers["Vary"] = vary
    return response


def render_legacy_business_error(
    request: Request, error: KnowledgeError, *, exception: BaseException | None = None,
) -> JSONResponse:
    """Keep the existing successful refusal / saved-but-unconfirmed contract."""
    language = normalize_locale(request.headers.get("X-Language-Type")) or "zh"
    message = translate_legacy(error.code, language)
    log_request_failure(
        request, status_code=error.status_code, response_code=error.response_code,
        error_code=error.code, message=message,
        exception=exception if exception is not None else error,
    )
    return JSONResponse(
        status_code=error.status_code,
        content=fail(error.response_code, message, error=message),
        headers={TRACE_ID_HEADER: getattr(request.state, "trace_id", get_trace_id()),
                 "Content-Language": language, "Vary": "X-Language-Type"},
    )


def render_error(
    request: Request,
    error: KnowledgeError,
    *,
    exception: BaseException | None = None,
    validation_errors: list[dict] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Log every business failure, including direct HTTP-200 failure responses."""
    if error.code in {
        "KB_FEISHU_AUTH_INVALID", "KB_YUQUE_AUTH_INVALID", "KB_WIKI_BACKFILL_DISPATCH_FAILED",
    }:
        return render_legacy_business_error(request, error, exception=exception)
    status_code = _business_http_status(request, error)
    response_code = error.upstream_response_code or error.response_code
    log_request_failure(
        request,
        status_code=status_code,
        response_code=response_code,
        error_code=error.code,
        message=knowledge_message(response_code, "en"),
        params=dict(error.params),
        retryable=error.retryable,
        exception=exception if exception is not None else error,
        validation_errors=validation_errors,
    )
    return _response(
        request,
        status_code=status_code,
        response_code=response_code,
        message=knowledge_message(response_code, request_locale(request)),
        headers=headers,
        diagnostic=format_diagnostics(
            trace_id=getattr(request.state, "trace_id", get_trace_id()),
            detail=(
                validation_diagnostic(validation_errors)
                if validation_errors
                else error.diagnostic or f"cause={error.code}"
            ),
        ),
    )


_UPLOAD_ERROR_STATUSES = {
    "KB_PRINCIPAL_INVALID": 401,
    "KB_KNOWLEDGE_NOT_FOUND": 404,
    "KB_PARENT_FOLDER_NOT_FOUND": 404,
    "KB_UPLOAD_FILE_EMPTY": 422,
    "KB_QA_IMPORT_FILE_EMPTY": 422,
    "KB_QA_IMPORT_FILE_TYPE_UNSUPPORTED": 415,
    "KB_UPLOAD_FILE_SIZE_LIMIT": 413,
    "KB_TASK_DISPATCH_FAILED": 503,
    "KB_DATABASE_UNAVAILABLE": 503,
    "KB_HTTP_UNAVAILABLE": 503,
    "KB_STORAGE_UNAVAILABLE": 503,
    "KB_STORAGE_DEPENDENCY_UNAVAILABLE": 503,
    "KB_STORAGE_OPERATION_TIMEOUT": 503,
    "KB_STORAGE_UPLOAD_TIMEOUT": 503,
    "KB_STORAGE_CONFIG_INVALID": 500,
    "KB_STORAGE_OPERATION_FAILED": 500,
    "KB_STORAGE_UPLOAD_FAILED": 500,
    "KB_DOCUMENT_PARSER_CONFIG_INVALID": 500,
}


def render_upload_error(
    request: Request, exc: Exception, *, status: int, cause: str,
    validation_errors: list[dict] | None = None,
    data: dict[str, str] | None = None,
    code: int | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    trace_id = getattr(request.state, "trace_id", None)
    if not trace_id:
        trace_id = normalize_trace_id(request.headers.get(TRACE_ID_HEADER))
        request.state.trace_id = trace_id
    response = upload_error_response(
        request, status, cause=cause, trace_id=trace_id, validation_errors=validation_errors,
        data=data, code=code, headers=dict(headers or {}),
    )
    log_request_failure(
        request, status_code=status, response_code=code, error_code=cause,
        message=cause, exception=exc, validation_errors=validation_errors,
    )
    return response


def register_error_handlers(application: FastAPI) -> None:
    @application.exception_handler(DuplicateUploadError)
    async def duplicate_upload_handler(request: Request, exc: DuplicateUploadError):
        return render_upload_error(
            request, exc, status=409, cause="KB_FILE_DUPLICATE",
            code=BizCode.KB_FILE_DUPLICATE, data={"existing_file_name": exc.existing_file_name},
        )

    @application.exception_handler(StorageError)
    async def storage_error_handler(request: Request, exc: StorageError):
        mapped = map_storage_error(exc)
        if is_versioned_upload(request, internal=True):
            return render_upload_error(
                request, exc, status=_UPLOAD_ERROR_STATUSES.get(mapped.code, 500),
                cause=mapped.code,
            )
        return render_error(request, mapped)

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        if is_versioned_upload(request, internal=True):
            return render_upload_error(
                request, exc, status=422, cause="VALIDATION_FAILED", validation_errors=exc.errors(),
            )
        endpoint = request.scope.get("endpoint")
        identity = (getattr(endpoint, "__module__", ""), getattr(endpoint, "__name__", ""))
        code = _VALIDATION_CONTRACTS.get(identity)
        if code is None:
            log_request_failure(
                request,
                status_code=422,
                response_code=None,
                error_code="KB_HTTP_VALIDATION_ERROR",
                message="Request validation failed",
                validation_errors=exc.errors(),
            )
            try:
                return await request_validation_exception_handler(request, exc)
            except ValueError:
                # Invalid JSON values such as Infinity cannot be echoed by JSONResponse.
                detail = [
                    {key: error[key] for key in ("loc", "msg", "type")}
                    for error in exc.errors()
                ]
                return JSONResponse(status_code=422, content={"detail": detail})
        if any(
            any(part in _RERANK_FIELDS for part in error.get("loc", ())) for error in exc.errors()
        ):
            code = "KB_RERANK_CONFIG_INVALID"
        return render_error(request, KnowledgeError.from_code(code), validation_errors=exc.errors())

    @application.exception_handler(KnowledgeError)
    async def knowledge_error_handler(request: Request, exc: KnowledgeError):
        if is_versioned_upload(request, internal=True):
            status = _UPLOAD_ERROR_STATUSES.get(exc.code, exc.status_code)
            return render_upload_error(request, exc, status=status, cause=exc.code)
        return render_error(request, exc)

    @application.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        if is_versioned_upload(request, internal=True):
            return render_upload_error(
                request, exc, status=exc.status_code,
                cause=_HTTP_ERRORS.get(exc.status_code, "KB_UPLOAD_HTTP_ERROR"),
                headers=exc.headers,
            )
        key = _HTTP_ERRORS.get(exc.status_code, "KB_INTERNAL_ERROR")
        status = exc.status_code
        error = KnowledgeError.from_code(key, status_code=status)
        return render_error(request, error, exception=exc, headers=exc.headers)

    @application.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        if is_versioned_upload(request, internal=True):
            if isinstance(exc, OperationalError):
                return render_upload_error(
                    request, exc, status=503, cause="KB_DATABASE_UNAVAILABLE",
                )
            return render_upload_error(request, exc, status=500, cause="KB_INTERNAL_ERROR")
        return render_error(request, KnowledgeError.from_code("KB_INTERNAL_ERROR"), exception=exc)
