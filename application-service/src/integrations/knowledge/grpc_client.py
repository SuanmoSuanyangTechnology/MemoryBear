"""gRPC adapter for the knowledge retrieval face.

The service exposes ``KnowledgeRetrievalService`` over unary RPCs; payloads stay
JSON so the HTTP and gRPC paths share one schema (see :mod:`.wire`). Only the
carriage changes: identity/source/trace travel as metadata and failures arrive
as gRPC status plus HTTP-restoring trailers.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable
from typing import Any

import grpc
from memorybear.knowledge.v1 import retrieval_pb2, retrieval_pb2_grpc
from memorybear_grpc.client import ChannelPool
from memorybear_grpc.errors import http_status_for_grpc, parse_error_trailers
from memorybear_grpc.metadata import (
    deadline_for,
    metadata_dict,
    trace_metadata,
    upstream_remaining,
)

from ...schemas.knowledge_retrieval_schema import (
    KnowledgeRetrievalRequest,
    KnowledgeRetrievalResult,
)

from .contracts import KnowledgeCallContext
from .errors import (
    KnowledgeClientError,
    KnowledgeProtocolError,
    KnowledgeServiceError,
    KnowledgeTimeoutError,
    KnowledgeUnavailableError,
)
from .wire import (
    retrieval_policy_from_data,
    retrieval_result_from_data,
    retrieval_wire_payload,
)

logger = logging.getLogger(__name__)

_TRACE_ID_METADATA_KEY = "x-trace-id"


def _ascii_metadata(values: Iterable[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    """gRPC metadata must be ASCII; drop anything that cannot travel unchanged."""

    safe: list[tuple[str, str]] = []
    for key, value in values:
        try:
            key.encode("ascii")
            value.encode("ascii")
        except UnicodeEncodeError:
            logger.warning("dropping non-ascii knowledge grpc metadata key=%s", key)
            continue
        safe.append((key, value))
    return tuple(safe)


def _metadata(context: KnowledgeCallContext) -> tuple[tuple[str, str], ...]:
    values = list(trace_metadata(context.trace_id))
    principal = context.principal
    if principal is not None:
        values.extend(
            (
                ("x-kb-actor-id", str(principal.actor_id)),
                ("x-kb-tenant-id", str(principal.tenant_id)),
                ("x-kb-workspace-id", str(principal.workspace_id)),
            )
        )
    values.append(("x-kb-source", context.source.value))
    return _ascii_metadata(values)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


async def _trailing_trace_id(call: Any, context: KnowledgeCallContext) -> str:
    return (
        metadata_dict(await call.trailing_metadata() or ()).get(_TRACE_ID_METADATA_KEY)
        or context.trace_id
    )


def _error_trace_id(error: KnowledgeClientError, context: KnowledgeCallContext) -> str:
    return getattr(error, "trace_id", None) or context.trace_id


class KnowledgeGrpcRetriever:
    """``KnowledgeRetriever`` over the shared pooled gRPC channel.

    The pool and its channels stay caller-owned; the runtime closes them.
    """

    def __init__(
        self,
        pool: ChannelPool,
        *,
        target: str,
        timeout_s: float,
        margin_s: float,
    ) -> None:
        if not target.strip():
            raise ValueError("knowledge grpc retrieval needs a non-empty target")
        self._pool = pool
        self._target = target
        self._timeout_s = timeout_s
        self._margin_s = margin_s

    def _deadline(self) -> float:
        remaining = upstream_remaining()
        if remaining is not None and remaining <= self._margin_s:
            # The margin is reserved for the response trip/orchestration; a
            # downstream call started with no usable budget can never succeed.
            raise KnowledgeUnavailableError(
                "upstream deadline exhausted before knowledge retrieval"
            )
        return deadline_for(budget_s=self._timeout_s, margin_s=self._margin_s)

    async def _stub(self) -> retrieval_pb2_grpc.KnowledgeRetrievalServiceStub:
        channel = await self._pool.channel(self._target)
        return retrieval_pb2_grpc.KnowledgeRetrievalServiceStub(channel)

    async def retrieval_policy(
        self,
        *,
        kb_ids: list[str],
        context: KnowledgeCallContext,
        rerank_id: str | None = None,
    ) -> dict[str, frozenset[str]]:
        """Fetch supported query modalities from the knowledge service."""

        payload: dict[str, Any] = {"kb_ids": kb_ids}
        if rerank_id:
            payload["rerank_id"] = rerank_id
        started_at = time.perf_counter()
        stub = await self._stub()
        call = stub.RetrievalPolicy(
            retrieval_pb2.RetrievalPolicyRequest(body_json=_json_bytes(payload)),
            metadata=_metadata(context),
            timeout=self._deadline(),
            wait_for_ready=False,
        )
        try:
            response = await call
        except grpc.aio.AioRpcError as exc:
            error = self._rpc_error(exc, context)
            logger.warning(
                "knowledge_grpc_retrieval_policy_failed source=%s error=%s "
                "elapsed_ms=%.2f trace_id=%s",
                context.source.value,
                type(error).__name__,
                (time.perf_counter() - started_at) * 1000,
                _error_trace_id(error, context),
            )
            raise error from exc
        trace_id = await _trailing_trace_id(call, context)
        envelope = self._decode_envelope(
            response.body_json, trace_id=trace_id, operation="retrieval policy"
        )
        policy = retrieval_policy_from_data(envelope.get("data"))
        logger.info(
            "knowledge_grpc_retrieval_policy_completed source=%s modes=%s "
            "elapsed_ms=%.2f trace_id=%s",
            context.source.value,
            len(policy),
            (time.perf_counter() - started_at) * 1000,
            trace_id,
        )
        return policy

    async def retrieve(
        self,
        request: KnowledgeRetrievalRequest,
        context: KnowledgeCallContext,
    ) -> KnowledgeRetrievalResult:
        started_at = time.perf_counter()
        stub = await self._stub()
        call = stub.Retrieve(
            retrieval_pb2.RetrieveRequest(
                body_json=_json_bytes(retrieval_wire_payload(request, context))
            ),
            metadata=_metadata(context),
            timeout=self._deadline(),
            wait_for_ready=False,
        )
        try:
            response = await call
        except grpc.aio.AioRpcError as exc:
            error = self._rpc_error(exc, context)
            logger.warning(
                "knowledge_grpc_retrieval_failed source=%s error=%s "
                "elapsed_ms=%.2f trace_id=%s",
                context.source.value,
                type(error).__name__,
                (time.perf_counter() - started_at) * 1000,
                _error_trace_id(error, context),
            )
            raise error from exc
        trace_id = await _trailing_trace_id(call, context)
        envelope = self._decode_envelope(
            response.body_json, trace_id=trace_id, operation="retrieval"
        )
        result = retrieval_result_from_data(envelope.get("data"))
        logger.info(
            "knowledge_grpc_retrieval_completed source=%s chunks=%s entities=%s "
            "relationships=%s elapsed_ms=%.2f trace_id=%s",
            context.source.value,
            len(result.chunks),
            len(result.entities),
            len(result.relationships),
            (time.perf_counter() - started_at) * 1000,
            trace_id,
        )
        return result

    @staticmethod
    def _decode_envelope(
        body_json: bytes, *, trace_id: str, operation: str
    ) -> dict[str, Any]:
        try:
            envelope = json.loads(body_json)
        except (UnicodeDecodeError, ValueError) as exc:
            raise KnowledgeProtocolError(
                f"Knowledge {operation} response is not valid JSON"
            ) from exc
        if not isinstance(envelope, dict):
            raise KnowledgeProtocolError(
                f"Knowledge {operation} response must be an object"
            )
        code = envelope.get("code")
        if not isinstance(code, int):
            raise KnowledgeProtocolError(
                f"Knowledge {operation} response code must be an integer"
            )
        if code != 0:
            message = str(envelope.get("msg") or "Knowledge error")
            raise KnowledgeServiceError(
                200, code, message, trace_id, diagnostic=str(envelope.get("error") or "")
            )
        return envelope

    @staticmethod
    def _rpc_error(
        exc: grpc.aio.AioRpcError, context: KnowledgeCallContext
    ) -> KnowledgeClientError:
        trailers = exc.trailing_metadata() or ()
        trace_id = (
            metadata_dict(trailers).get(_TRACE_ID_METADATA_KEY) or context.trace_id
        )
        parsed = parse_error_trailers(trailers)
        if parsed is not None:
            status, raw = parsed
            try:
                payload = json.loads(raw)
            except (UnicodeDecodeError, ValueError):
                payload = None
            if isinstance(payload, dict):
                code = payload.get("code")
                message = str(payload.get("msg") or "Knowledge error")
                if isinstance(code, int):
                    return KnowledgeServiceError(
                        status, code, message, trace_id, diagnostic=str(payload.get("error") or "")
                    )
            return KnowledgeProtocolError("Knowledge error envelope is invalid")
        if exc.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
            return KnowledgeTimeoutError("Knowledge service request timed out")
        if exc.code() == grpc.StatusCode.UNAVAILABLE:
            return KnowledgeUnavailableError("Knowledge service is unavailable")
        status = http_status_for_grpc(exc.code())
        if 400 <= status < 500:
            # Auth interceptors abort without an HTTP envelope; keep 4xx
            # failures distinguishable instead of collapsing them into 503.
            return KnowledgeServiceError(
                status,
                status,
                exc.details() or "Knowledge request failed",
                trace_id,
            )
        return KnowledgeUnavailableError(
            f"Knowledge service rpc failed: {exc.code().name}"
        )


__all__ = ["KnowledgeGrpcRetriever"]