"""Remote implementation of knowledge route and retrieval interfaces."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import Request
from starlette.datastructures import UploadFile
from starlette.responses import StreamingResponse

from app.schemas.knowledge_retrieval_schema import (
    KnowledgeRetrievalRequest,
    KnowledgeRetrievalResult,
)

from .call_profile import CallProfile
from .contracts import KnowledgeCallContext
from .errors import (
    KnowledgeProtocolError,
    KnowledgeServiceError,
    KnowledgeTimeoutError,
    KnowledgeUnavailableError,
)
from .transport import KnowledgeHttpTransport

logger = logging.getLogger(__name__)


def _retrieval_wire_payload(
    request: KnowledgeRetrievalRequest,
    context: KnowledgeCallContext,
) -> dict[str, Any]:
    normalized = request.model_copy(update={"source": context.source})
    payload = normalized.model_dump(mode="json")
    for field_name in ("rerank_mode", "rerank_weights"):
        if getattr(normalized, field_name) is None:
            payload.pop(field_name, None)
    payload["knowledge_bases"] = [
        config.model_dump(
            mode="json",
            include=config.model_fields_set | {"kb_id"},
            exclude_none=True,
        )
        for config in normalized.knowledge_bases
    ]
    return payload


class KnowledgeServiceClient:
    """HTTP adapter used by both route forwarding and semantic retrieval."""

    def __init__(self, transport: KnowledgeHttpTransport):
        self._transport = transport

    @classmethod
    def from_settings(cls, settings: Any) -> KnowledgeServiceClient:
        timeout = httpx.Timeout(
            connect=settings.MEM_KNOWLEDGE_CONNECT_TIMEOUT_SECONDS,
            pool=settings.MEM_KNOWLEDGE_POOL_TIMEOUT_SECONDS,
            read=settings.MEM_KNOWLEDGE_READ_TIMEOUT_SECONDS,
            write=settings.MEM_KNOWLEDGE_WRITE_TIMEOUT_SECONDS,
        )
        stream_timeout = httpx.Timeout(
            connect=settings.MEM_KNOWLEDGE_CONNECT_TIMEOUT_SECONDS,
            pool=settings.MEM_KNOWLEDGE_POOL_TIMEOUT_SECONDS,
            read=settings.MEM_KNOWLEDGE_STREAM_READ_TIMEOUT_SECONDS,
            write=settings.MEM_KNOWLEDGE_WRITE_TIMEOUT_SECONDS,
        )
        limits = httpx.Limits(
            max_connections=settings.MEM_KNOWLEDGE_MAX_CONNECTIONS,
            max_keepalive_connections=settings.MEM_KNOWLEDGE_MAX_KEEPALIVE_CONNECTIONS,
        )
        return cls(
            KnowledgeHttpTransport(
                base_url=settings.MEM_KNOWLEDGE_BASE_URL,
                timeout=timeout,
                limits=limits,
                stream_timeout=stream_timeout,
                health_timeout=settings.MEM_KNOWLEDGE_HEALTH_TIMEOUT_SECONDS,
            )
        )

    @classmethod
    def for_test(
        cls,
        base_url: str,
        transport: httpx.AsyncBaseTransport,
    ) -> KnowledgeServiceClient:
        timeout = httpx.Timeout(5.0)
        return cls(
            KnowledgeHttpTransport(
                base_url=base_url,
                timeout=timeout,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                stream_timeout=timeout,
                health_timeout=1.0,
                transport=transport,
            )
        )

    async def forward(
        self,
        request: Request,
        context: KnowledgeCallContext,
        *,
        profile: CallProfile = CallProfile.JSON,
    ) -> StreamingResponse:
        started_at = time.perf_counter()
        path = self._transport.internal_path(request.url.path)
        url = self._transport.internal_url(path, request.scope.get("query_string", b""))
        headers = self._transport.request_headers(request.headers, context, profile)
        send_kwargs: dict[str, Any] = {}
        if profile is CallProfile.MULTIPART_UPLOAD:
            form = await request.form()
            data: dict[str, str] = {}
            files: list[tuple[str, tuple[str, Any, str | None]]] = []
            for key, value in form.multi_items():
                if isinstance(value, UploadFile):
                    await value.seek(0)
                    files.append(
                        (key, (value.filename or "", value.file, value.content_type))
                    )
                else:
                    data[key] = str(value)
            send_kwargs = {"data": data, "files": files}
        else:
            body = await request.body()
            if body:
                send_kwargs["content"] = body
        upstream = await self._transport.send(
            method=request.method,
            url=url,
            headers=headers,
            profile=profile,
            **send_kwargs,
        )
        headers_at = time.perf_counter()

        async def body_iterator() -> AsyncIterator[bytes]:
            bytes_forwarded = 0
            first_byte_at: float | None = None
            completion = "closed_early"
            error_type = "none"
            try:
                async for chunk in upstream.aiter_raw():
                    if first_byte_at is None:
                        first_byte_at = time.perf_counter()
                    bytes_forwarded += len(chunk)
                    yield chunk
                completion = "complete"
            except asyncio.CancelledError:
                completion = "cancelled"
                error_type = "CancelledError"
                raise
            except Exception as exc:
                completion = "error"
                error_type = type(exc).__name__
                raise
            finally:
                await upstream.aclose()
                finished_at = time.perf_counter()
                logger.info(
                    "knowledge_proxy_stream_finished method=%s path=%s status=%s "
                    "profile=%s source=%s completion=%s bytes=%s header_ms=%.2f "
                    "ttfb_ms=%.2f elapsed_ms=%.2f error=%s trace_id=%s",
                    request.method,
                    path,
                    upstream.status_code,
                    profile.value,
                    context.source.value,
                    completion,
                    bytes_forwarded,
                    (headers_at - started_at) * 1000,
                    (
                        (first_byte_at - started_at) * 1000
                        if first_byte_at is not None
                        else -1.0
                    ),
                    (finished_at - started_at) * 1000,
                    error_type,
                    context.trace_id,
                )

        return StreamingResponse(
            body_iterator(),
            status_code=upstream.status_code,
            headers=self._transport.response_headers(upstream.headers),
        )

    async def retrieve(
        self,
        request: KnowledgeRetrievalRequest,
        context: KnowledgeCallContext,
    ) -> KnowledgeRetrievalResult:
        started_at = time.perf_counter()
        payload = json.dumps(
            _retrieval_wire_payload(request, context),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        url = self._transport.internal_url("/internal/v1/chunks/retrieval")
        headers = self._transport.request_headers(
            {"Content-Type": "application/json"},
            context,
            CallProfile.JSON,
        )
        upstream = await self._transport.send(
            method="POST",
            url=url,
            headers=headers,
            profile=CallProfile.JSON,
            content=payload,
        )
        headers_at = time.perf_counter()
        try:
            try:
                raw = await upstream.aread()
            except httpx.TimeoutException as exc:
                raise KnowledgeTimeoutError(
                    "Knowledge service request timed out"
                ) from exc
            except httpx.RequestError as exc:
                raise KnowledgeUnavailableError(
                    "Knowledge service is unavailable"
                ) from exc
            trace_id = upstream.headers.get("X-Trace-Id", context.trace_id)
            try:
                envelope = json.loads(raw)
            except (TypeError, ValueError) as exc:
                raise KnowledgeProtocolError(
                    "Knowledge service returned invalid JSON"
                ) from exc
            if not isinstance(envelope, dict):
                raise KnowledgeProtocolError("Knowledge service envelope must be an object")
            code = envelope.get("code")
            if not isinstance(code, int):
                raise KnowledgeProtocolError("Knowledge service envelope code must be an integer")
            if not 200 <= upstream.status_code < 300 or code != 0:
                message = str(envelope.get("msg") or envelope.get("error") or "Knowledge error")
                raise KnowledgeServiceError(upstream.status_code, code, message, trace_id)
            data = envelope.get("data")
            try:
                if isinstance(data, list):
                    result = KnowledgeRetrievalResult(chunks=data)
                elif isinstance(data, dict):
                    result = KnowledgeRetrievalResult.model_validate(data)
                else:
                    raise KnowledgeProtocolError(
                        "Knowledge retrieval data must be a list or object"
                    )
            except ValueError as exc:
                raise KnowledgeProtocolError(
                    "Knowledge retrieval data is incompatible"
                ) from exc
            logger.info(
                "knowledge_retrieval_completed status=%s code=%s source=%s "
                "bytes=%s chunks=%s entities=%s relationships=%s header_ms=%.2f "
                "elapsed_ms=%.2f trace_id=%s",
                upstream.status_code,
                code,
                context.source.value,
                len(raw),
                len(result.chunks),
                len(result.entities),
                len(result.relationships),
                (headers_at - started_at) * 1000,
                (time.perf_counter() - started_at) * 1000,
                trace_id,
            )
            return result
        except Exception as exc:
            logger.warning(
                "knowledge_retrieval_failed status=%s source=%s error=%s "
                "elapsed_ms=%.2f trace_id=%s",
                upstream.status_code,
                context.source.value,
                type(exc).__name__,
                (time.perf_counter() - started_at) * 1000,
                upstream.headers.get("X-Trace-Id", context.trace_id),
            )
            raise
        finally:
            await upstream.aclose()

    async def ready(self) -> bool:
        return await self._transport.ready()

    async def aclose(self) -> None:
        await self._transport.aclose()
