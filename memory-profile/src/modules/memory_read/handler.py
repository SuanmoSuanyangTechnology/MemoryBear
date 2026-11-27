"""Transport handler of the memory-read module.

Responsibilities, and nothing more:

* translate the validated request body into a core command;
* resolve the caller's :class:`ReadPrincipal` from the authenticated context;
* run the use case and encode its result into the legacy envelope (JSON) or SSE;
* map core :class:`ReadError` values into the shared business-code catalog.

Retrieval logic, storage access and model calls all live in ``src.core``.

The use-case adapters are transport-neutral: ``execute_sync``,
``execute_internal`` and ``stream_frames`` produce the exact envelopes/events
the HTTP face carries, so a future transport can frame the same results without
duplicating read semantics.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from error_core.codes import coerce_code
from error_core.messages import user_message
from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from src.common.responses import ApiResponse, failure, success
from src.core.application.read.projection import public_memory_dumps
from src.core.composition import MemoryReadModule, build_memory_read_module
from src.core.domain.enums import MemorySource, SearchStrategy, StorageType
from src.core.domain.errors import ReadError, ReadErrorCode
from src.core.domain.models import (
    ReadContextOverrides,
    ReadOptions,
    ReadOutcome,
    ReadPrincipal,
    StreamReadCommand,
    SyncReadCommand,
)
from src.i18n import get_request_language
from src.middleware.auth import MemoryPrincipal
from src.modules.memory_read.schemas import (
    AppServiceReadBody,
    PredictionStatementReadBody,
    ReadInternalBody,
    ReadStreamBody,
    ReadSyncBody,
)
from src.runtime import ProfileRuntime

logger = logging.getLogger(__name__)

_MODULE_STATE_KEY = "memory_read_module"

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

#: Event name that carries a keepalive instead of a payload.
HEARTBEAT_EVENT = "heartbeat"


def _principal(context: MemoryPrincipal) -> ReadPrincipal:
    return ReadPrincipal(
        workspace_id=context.workspace_id,
        tenant_id=context.tenant_id,
        actor_id=str(context.actor_id),
        source=str(context.source),
    )


def _code_int(code_name: str) -> int:
    """Resolve a core code name to the shared numeric catalog value."""

    numeric = coerce_code(code_name)
    return int(numeric) if numeric is not None else int(coerce_code("MEMORY_READ_FAILED") or 9502)


def _error_message(error: ReadError, language: str) -> str:
    numeric = _code_int(error.code.value)
    return user_message(numeric, language) or error.message


def _sse(event_name: str, data: dict[str, Any]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sync_payload(
    outcome: ReadOutcome | dict[str, ReadOutcome], *, public: bool = False
) -> dict[str, Any]:
    """Encode the legacy V1 payload: one host or a map keyed by requested id.

    ``public`` selects the API-key ``/read/sync`` face, whose terminal-user
    profile item drops the historical profile fields; ``/read/internal`` keeps
    the raw dumps, exactly like the host.
    """

    def encode(result: ReadOutcome) -> dict[str, Any]:
        outputs = (
            public_memory_dumps(result.result, result.end_user_id)
            if public
            else result.intermediate_outputs
        )
        return {"answer": result.answer, "intermediate_outputs": outputs}

    if isinstance(outcome, dict):
        return {
            end_user_id: encode(result)
            for end_user_id, result in outcome.items()
        }
    return encode(outcome)


def _read_failure(error: ReadError, language: str) -> ApiResponse[dict[str, Any]]:
    return failure(
        _code_int(error.code.value),
        _error_message(error, language),
        error.detail,
    )


class MemoryReadHandler:
    """Adapt the memory-read use cases to HTTP / SSE.

    HTTP methods (``read_sync``/``read_internal``/``read_stream``) own request
    context (app-scoped module, negotiated language) and framing only; the
    ``execute_*``/``stream_frames`` adapters are called by every transport.
    """

    def __init__(self, runtime: ProfileRuntime) -> None:
        self.runtime = runtime

    def module(self, request: Request) -> MemoryReadModule:
        """Build (once per app) and return the wired read module."""

        state = request.app.state
        cached = getattr(state, _MODULE_STATE_KEY, None)
        if isinstance(cached, MemoryReadModule):
            return cached
        module = build_memory_read_module(self.runtime)
        setattr(state, _MODULE_STATE_KEY, module)
        return module

    # ── transport-neutral use-case adapters ───────────────────────

    async def execute_sync(
        self,
        module: MemoryReadModule,
        context: MemoryPrincipal,
        body: ReadSyncBody,
        *,
        language: str,
    ) -> ApiResponse[dict[str, Any]]:
        """Run the ``/read/sync`` use case and encode the legacy V1 payload."""

        principal = _principal(context)
        try:
            command = SyncReadCommand(
                message=body.message,
                search_switch=_parse_strategy(body.search_switch),
                end_user_ids=body.resolved_end_user_ids(),
                enable_rerank=body.enable_rerank,
            )
        except ValueError as error:
            return failure(
                _code_int(ReadErrorCode.INVALID_PARAMETER.value),
                _error_message(
                    ReadError(
                        "Invalid search strategy",
                        code=ReadErrorCode.INVALID_PARAMETER,
                        reason="invalid_search_switch",
                    ),
                    language,
                ),
                str(error),
            )

        try:
            outcome = await module.read_memory.execute(
                command,
                principal,
                language=language,
                # The /v1 face records display cards and honours enable_rerank.
                options=ReadOptions(
                    record_display=True, enable_rerank=body.enable_rerank
                ),
            )
        except ReadError as error:
            logger.warning(
                "memory read failed: code=%s reason=%s workspace=%s",
                error.code.value,
                error.reason,
                context.workspace_id,
            )
            return _read_failure(error, language)

        return success(data=_sync_payload(outcome, public=True))

    async def execute_internal(
        self,
        module: MemoryReadModule,
        context: MemoryPrincipal,
        body: ReadInternalBody,
        *,
        language: str,
    ) -> ApiResponse[dict[str, Any]]:
        """Run the internal read use case (one host plus retrieval options).

        Unlike ``/read/sync`` this face does not record a display card (the host
        implementation never passed ``record_display``); all other options are
        forwarded verbatim.
        """

        principal = _principal(context)
        try:
            command = SyncReadCommand(
                message=body.message,
                search_switch=_parse_strategy(body.search_switch),
                end_user_ids=(body.end_user_id,),
                enable_rerank=body.enable_rerank,
            )
        except ValueError as error:
            return failure(
                _code_int(ReadErrorCode.INVALID_PARAMETER.value),
                _error_message(
                    ReadError(
                        "Invalid search strategy",
                        code=ReadErrorCode.INVALID_PARAMETER,
                        reason="invalid_search_switch",
                    ),
                    language,
                ),
                str(error),
            )

        options = ReadOptions(
            limit=body.limit,
            includes=tuple(body.includes) if body.includes else None,
            skip_summary=body.skip_summary,
            enable_rerank=body.enable_rerank,
            include_metadata=body.include_metadata,
        )

        try:
            outcome = await module.read_memory.execute(
                command,
                principal,
                language=language,
                options=options,
            )
        except ReadError as error:
            logger.warning(
                "memory internal read failed: code=%s reason=%s workspace=%s",
                error.code.value,
                error.reason,
                context.workspace_id,
            )
            return _read_failure(error, language)

        return success(data=_sync_payload(outcome, public=False))

    async def read_prediction_statements(
        self,
        request: Request,
        context: MemoryPrincipal,
        body: PredictionStatementReadBody,
    ) -> ApiResponse[dict[str, Any]]:
        try:
            outcome = await self.module(request).read_memory.execute(
                SyncReadCommand(
                    message=body.message,
                    search_switch=SearchStrategy.NORMAL,
                    end_user_ids=(body.end_user_id,),
                    enable_rerank=body.enable_rerank,
                ),
                _principal(context),
                language="zh",
                overrides=ReadContextOverrides(
                    config_id=body.config_id,
                    storage_type=StorageType.NEO4J.value,
                    draft=True,
                ),
                options=ReadOptions(
                    limit=body.limit,
                    includes=(MemorySource.STATEMENT.value,),
                    skip_summary=True,
                    enable_rerank=body.enable_rerank,
                ),
            )
        except ReadError as error:
            return _read_failure(error, get_request_language(request))

        assert isinstance(outcome, ReadOutcome)
        result = outcome.result
        trace = result.execution_trace
        return success(data={
            "statements": [
                {
                    "id": item.id,
                    "score": item.score,
                    "retrieval_trace": (
                        item.retrieval_trace.model_dump() if item.retrieval_trace else None
                    ),
                }
                for item in result.memories
                if item.source == MemorySource.STATEMENT
            ][:body.limit],
            "execution_trace": trace.model_dump() if trace is not None else None,
        })

    async def read_app_service(
        self,
        request: Request,
        context: MemoryPrincipal,
        body: AppServiceReadBody,
    ) -> ApiResponse[dict[str, Any]]:
        """Run the application-service callback read.

        Replaces the host endpoint the application service calls back into. The
        host returned the memory items themselves (``content`` / ``memories``,
        not the V1 ``answer`` / ``intermediate_outputs`` shape) and honoured the
        caller's configuration, draft flag, display-card switch, history and
        storage backend, so every one of those travels through instead of being
        fixed by the face.
        """

        language = body.language or get_request_language(request)
        principal = _principal(context)

        try:
            command = SyncReadCommand(
                message=body.query,
                search_switch=_parse_strategy(body.search_switch),
                end_user_ids=(body.end_user_id,),
                enable_rerank=body.enable_rerank,
            )
        except ValueError as error:
            return failure(
                _code_int(ReadErrorCode.INVALID_PARAMETER.value),
                _error_message(
                    ReadError(
                        "Invalid search strategy",
                        code=ReadErrorCode.INVALID_PARAMETER,
                        reason="invalid_search_switch",
                    ),
                    language,
                ),
                str(error),
            )

        options = ReadOptions(
            limit=body.limit,
            includes=tuple(body.includes) if body.includes else None,
            skip_summary=body.skip_summary,
            enable_rerank=body.enable_rerank,
            record_display=body.record_display,
        )
        overrides = ReadContextOverrides(
            config_id=body.config_id,
            storage_type=body.storage_type,
            draft=body.draft,
            conversation_id=body.conversation_id,
            app_id=body.app_id,
            user_rag_memory_id=body.user_rag_memory_id or None,
        )

        try:
            outcome = await self.module(request).read_memory.execute(
                command,
                principal,
                language=language,
                options=options,
                history=body.history,
                overrides=overrides,
            )
        except ReadError as error:
            logger.warning(
                "application-service memory read failed: code=%s reason=%s workspace=%s",
                error.code.value,
                error.reason,
                principal.workspace_id,
            )
            return _read_failure(error, language)

        if isinstance(outcome, dict):
            # One requested host can only ever produce a single outcome; a map
            # here would mean the use case changed shape under this face.
            outcome = next(iter(outcome.values()))
        return success(
            data={
                "content": outcome.answer,
                "memories": outcome.intermediate_outputs,
            }
        )

    async def stream_frames(
        self,
        module: MemoryReadModule,
        context: MemoryPrincipal,
        body: ReadStreamBody,
        *,
        language: str,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Yield ``(event_name, data)`` for the streaming read face.

        The heartbeat marker carries no payload; SSE writes it as a comment so
        the connection stays alive without a spurious data event.
        """

        principal = _principal(context)
        try:
            command = StreamReadCommand(
                message=body.message,
                search_switch=_parse_strategy(body.search_switch),
                end_user_id=body.end_user_id,
                # Host parity: the validation page keys its Redis history (and
                # echoes session_id) with the dashless ``uuid.hex`` spelling.
                session_id=body.session_id.hex,
            )
        except ValueError as error:
            logger.warning("memory read stream rejected: %s", error)
            yield (
                "error",
                {
                    "request_id": "",
                    "code": _code_int(ReadErrorCode.INVALID_PARAMETER.value),
                    "message": "不支持的检索方式",
                },
            )
            return

        async for event in module.stream_read.events(
            command, principal, language=language
        ):
            if event.name == HEARTBEAT_EVENT:
                yield (HEARTBEAT_EVENT, {})
                continue
            data = dict(event.data)
            code = data.get("code")
            if isinstance(code, str):
                data["code"] = _code_int(code)
            yield (event.name, data)

    # ── HTTP framing ──────────────────────────────────────────────

    async def read_sync(
        self,
        request: Request,
        context: MemoryPrincipal,
        body: ReadSyncBody,
    ) -> ApiResponse[dict[str, Any]]:
        return await self.execute_sync(
            self.module(request),
            context,
            body,
            language=get_request_language(request),
        )

    async def read_internal(
        self,
        request: Request,
        context: MemoryPrincipal,
        body: ReadInternalBody,
    ) -> ApiResponse[dict[str, Any]]:
        return await self.execute_internal(
            self.module(request),
            context,
            body,
            language=get_request_language(request),
        )

    async def read_stream(
        self,
        request: Request,
        context: MemoryPrincipal,
        body: ReadStreamBody,
    ) -> StreamingResponse:
        module = self.module(request)
        language = get_request_language(request)

        async def stream() -> AsyncIterator[str]:
            async for name, data in self.stream_frames(
                module, context, body, language=language
            ):
                if name == HEARTBEAT_EVENT:
                    yield ": heartbeat\n\n"
                    continue
                yield _sse(name, jsonable_encoder(data))

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers=dict(_SSE_HEADERS),
        )


def _parse_strategy(value: Any) -> Any:
    return SearchStrategy(str(value))


__all__ = ["HEARTBEAT_EVENT", "MemoryReadHandler"]
