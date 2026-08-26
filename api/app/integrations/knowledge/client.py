"""Remote implementation of knowledge route and retrieval interfaces."""

from __future__ import annotations

import json
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

        async def body_iterator() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()

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
        normalized = request.model_copy(update={"source": context.source})
        payload = json.dumps(
            normalized.model_dump(mode="json", exclude_none=True),
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
            if isinstance(data, list):
                return KnowledgeRetrievalResult(chunks=data)
            if isinstance(data, dict):
                try:
                    return KnowledgeRetrievalResult.model_validate(data)
                except ValueError as exc:
                    raise KnowledgeProtocolError(
                        "Knowledge retrieval data is incompatible"
                    ) from exc
            raise KnowledgeProtocolError("Knowledge retrieval data must be a list or object")
        finally:
            await upstream.aclose()

    async def ready(self) -> bool:
        return await self._transport.ready()

    async def aclose(self) -> None:
        await self._transport.aclose()
