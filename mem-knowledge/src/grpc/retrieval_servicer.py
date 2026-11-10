"""KnowledgeRetrievalService adapter: typed carriage for the retrieval pipeline.

All retrieval semantics (target preparation, filters, rerank, graph mixing,
response shape and error vocabulary) stay in ``services.knowledge_retrieval``
and ``services.knowledge_retrieval_preparation``; this layer only decodes the
JSON envelope, resolves identity/source/trace from metadata, and converts
failures into gRPC status plus HTTP-restoring trailers.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from memorybear.knowledge.v1 import retrieval_pb2, retrieval_pb2_grpc
from memorybear_grpc.interceptors import current_principal
from memorybear_grpc.metadata import (
    bind_upstream_remaining,
    metadata_dict,
    reset_upstream_remaining,
)
from pydantic import ValidationError

from ..api.dependencies import Principal
from ..api.schemas.chunk import ChunkRetrieve, KnowledgeRetrievalSource, RetrievalPolicyRequest
from ..api.schemas.knowledge_retrieval import (
    KnowledgeRetrievalRequest,
    prepare_retrieval_request,
)
from ..errors import KnowledgeError
from ..i18n import resolve_locale
from ..request_logging import failure_diagnostics
from ..runtime import ProcessRuntime
from ..services.knowledge_retrieval import KnowledgeRetrievalService
from ..services.knowledge_retrieval_preparation import KnowledgeRetrievalPreparation
from ..trace import get_trace_id, normalize_trace_id, reset_trace_id, set_trace_id
from .envelope import TRACE_ID_TRAILER, abort_with_error, success_body

logger = logging.getLogger(__name__)

_RERANK_FIELDS = frozenset({"rerank_mode", "rerank_weights"})


def _decode_json_object(body_json: bytes, *, error_code: str) -> dict[str, Any]:
    try:
        body = json.loads(body_json)
    except (UnicodeDecodeError, ValueError) as exc:
        raise KnowledgeError.from_code(error_code) from exc
    if not isinstance(body, dict):
        raise KnowledgeError.from_code(error_code)
    return body


def _decode_retrieval_request(
    body_json: bytes, source: KnowledgeRetrievalSource
) -> KnowledgeRetrievalRequest:
    body = _decode_json_object(body_json, error_code="KB_RETRIEVAL_REQUEST_INVALID")
    try:
        return prepare_retrieval_request(ChunkRetrieve.model_validate(body), source)
    except ValidationError as exc:
        if any(
            any(part in _RERANK_FIELDS for part in error.get("loc", ()))
            for error in exc.errors()
        ):
            raise KnowledgeError.from_code("KB_RERANK_CONFIG_INVALID") from exc
        raise KnowledgeError.from_code("KB_RETRIEVAL_REQUEST_INVALID") from exc


def _decode_policy_request(body_json: bytes) -> RetrievalPolicyRequest:
    body = _decode_json_object(body_json, error_code="KB_HTTP_VALIDATION_ERROR")
    try:
        return RetrievalPolicyRequest.model_validate(body)
    except ValidationError as exc:
        raise KnowledgeError.from_code("KB_HTTP_VALIDATION_ERROR") from exc


def _principal() -> Principal:
    principal = current_principal()
    if not isinstance(principal, Principal):
        raise KnowledgeError.from_code("KB_HTTP_UNAUTHORIZED")
    return principal


def _source(context) -> KnowledgeRetrievalSource:
    """Parse the API-asserted business source from metadata like ``get_source``."""

    headers = metadata_dict(context.invocation_metadata())
    try:
        return KnowledgeRetrievalSource(
            headers.get("x-kb-source") or KnowledgeRetrievalSource.GENERAL
        )
    except ValueError as exc:
        raise KnowledgeError.from_code("KB_PRINCIPAL_INVALID") from exc


def _language(context) -> str:
    headers = metadata_dict(context.invocation_metadata())
    return resolve_locale(
        explicit=headers.get("x-language-type"),
        accepted=headers.get("accept-language"),
    )


def _bind_trace() -> tuple[str, Any]:
    trace_id = get_trace_id() or normalize_trace_id(None)
    return trace_id, set_trace_id(trace_id)


def _retrieval_data(result: Any) -> dict[str, Any] | list[dict[str, Any]]:
    """Serialize exactly like the HTTP route's ``SuccessEnvelope[Any]``."""

    if result.has_graph_data():
        return result.model_dump(mode="json")
    return [chunk.model_dump(mode="json") for chunk in result.chunks]


def _log_failure(
    error: KnowledgeError,
    *,
    operation: str,
    stage: str,
    trace_id: str,
    principal: Principal | None,
    source: KnowledgeRetrievalSource | None = None,
) -> None:
    diagnostics, nodes, bounded_chain = failure_diagnostics(error)
    details = {
        "operation": operation,
        "stage": stage,
        "trace_id": trace_id,
        "code": error.code,
        "status": error.status_code,
        "params": dict(error.params),
        "source": source.value if source is not None else None,
        "actor_id": str(principal.actor_id) if principal else None,
        "tenant_id": str(principal.tenant_id) if principal else None,
        "workspace_id": str(principal.workspace_id) if principal else None,
        **diagnostics,
    }
    logger.warning(
        "knowledge_grpc_failed %s",
        json.dumps(details, ensure_ascii=False, default=str),
        exc_info=(type(error), error, error.__traceback__)
        if not bounded_chain and len(nodes) > 1 else None,
    )


class KnowledgeRetrievalServicer(
    retrieval_pb2_grpc.KnowledgeRetrievalServiceServicer
):
    """Stateless adapter: the pipeline resolves everything per call."""

    def __init__(self, runtime: ProcessRuntime) -> None:
        self._runtime = runtime

    async def Retrieve(  # noqa: N802 - generated method name
        self, request: retrieval_pb2.RetrieveRequest, context
    ) -> retrieval_pb2.RetrieveResponse:
        upstream_token = bind_upstream_remaining(context.time_remaining())
        trace_id, trace_token = _bind_trace()
        language = _language(context)
        started = time.perf_counter()
        source: KnowledgeRetrievalSource | None = None
        principal: Principal | None = None
        stage = "request_validation"
        try:
            try:
                principal = _principal()
                source = _source(context)
                retrieval_request = _decode_retrieval_request(request.body_json, source)
                stage = "retrieval"
                result = await KnowledgeRetrievalService.retrieve_async(
                    self._runtime,
                    retrieval_request,
                    principal,
                )
                body = success_body(_retrieval_data(result), "retrieval successful")
            except KnowledgeError as exc:
                _log_failure(
                    exc, operation="Retrieve", stage=stage, trace_id=trace_id,
                    principal=principal, source=source,
                )
                await abort_with_error(
                    context, exc, language=language, trace_id=trace_id
                )
            except Exception:
                logger.exception(
                    "knowledge_grpc_retrieval_error source=%s elapsed_ms=%.2f trace_id=%s",
                    source.value if source is not None else "unknown",
                    (time.perf_counter() - started) * 1000,
                    trace_id,
                )
                await abort_with_error(
                    context,
                    KnowledgeError.from_code("KB_INTERNAL_ERROR"),
                    language=language,
                    trace_id=trace_id,
                )
            logger.info(
                "knowledge_grpc_retrieval_completed chunks=%s entities=%s "
                "relationships=%s source=%s elapsed_ms=%.2f trace_id=%s",
                len(result.chunks),
                len(result.entities),
                len(result.relationships),
                source.value if source is not None else "unknown",
                (time.perf_counter() - started) * 1000,
                trace_id,
            )
            context.set_trailing_metadata(((TRACE_ID_TRAILER, trace_id),))
            return retrieval_pb2.RetrieveResponse(body_json=body)
        finally:
            reset_trace_id(trace_token)
            reset_upstream_remaining(upstream_token)

    async def RetrievalPolicy(  # noqa: N802 - generated method name
        self, request: retrieval_pb2.RetrievalPolicyRequest, context
    ) -> retrieval_pb2.RetrievalPolicyResponse:
        upstream_token = bind_upstream_remaining(context.time_remaining())
        trace_id, trace_token = _bind_trace()
        language = _language(context)
        started = time.perf_counter()
        principal: Principal | None = None
        stage = "request_validation"
        try:
            try:
                principal = _principal()
                policy_request = _decode_policy_request(request.body_json)
                stage = "retrieval_policy"
                async with self._runtime.database.async_session() as db:
                    policy = await KnowledgeRetrievalPreparation.prepare_policy_with_db(
                        db,
                        policy_request,
                        principal,
                    )
                body = success_body(
                    policy.model_dump(mode="json", by_alias=True),
                    "retrieval policy resolved",
                )
            except KnowledgeError as exc:
                _log_failure(
                    exc, operation="RetrievalPolicy", stage=stage, trace_id=trace_id,
                    principal=principal,
                )
                await abort_with_error(
                    context, exc, language=language, trace_id=trace_id
                )
            except Exception:
                logger.exception(
                    "knowledge_grpc_retrieval_policy_error elapsed_ms=%.2f trace_id=%s",
                    (time.perf_counter() - started) * 1000,
                    trace_id,
                )
                await abort_with_error(
                    context,
                    KnowledgeError.from_code("KB_INTERNAL_ERROR"),
                    language=language,
                    trace_id=trace_id,
                )
            logger.info(
                "knowledge_grpc_retrieval_policy_completed elapsed_ms=%.2f trace_id=%s",
                (time.perf_counter() - started) * 1000,
                trace_id,
            )
            context.set_trailing_metadata(((TRACE_ID_TRAILER, trace_id),))
            return retrieval_pb2.RetrievalPolicyResponse(body_json=body)
        finally:
            reset_trace_id(trace_token)
            reset_upstream_remaining(upstream_token)


__all__ = ["KnowledgeRetrievalServicer"]