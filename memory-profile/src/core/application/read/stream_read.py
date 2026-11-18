"""Streaming read use case (``POST /internal/v1/memory/read/stream``).

Serves the management face (JWT). The host implementation interleaves five
concerns in one generator: session history, retrieval with live stage events,
trace projection, answer generation and SSE framing. The use case keeps the
first four and yields :class:`StreamEvent` objects; the transport layer owns
SSE framing (event name / data / heartbeat comment), so this module has no
FastAPI or starlette dependency.

Event order is wire-frozen:

    start → (memory_stage | heartbeat)* → retrieval_trace → message → end

and the error path yields ``error`` (plus the remaining community stages for the
community strategy).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from typing import Any
from uuid import uuid4

from src.core.application.read.community_search import COMMUNITY_DEFAULTS
from src.core.application.read.context import ReadContextFactory
from src.core.application.read.projection import (
    build_fast_search_stage,
    build_intermediate_outputs,
    build_validation_trace,
    community_stage_collector,
    project_community_trace,
)
from src.core.application.read.read_pipeline import ReadPipeline
from src.core.application.read.stages import (
    VALIDATION_STAGE_ALLOWLIST,
    memory_stage_collector,
)
from src.core.domain.enums import SearchStrategy, StorageType
from src.core.domain.errors import (
    AnswerGenerationError,
    EndUserNotFoundError,
    ModelUnavailableError,
    ReadError,
    ReadErrorCode,
)
from src.core.domain.models import (
    ReadContext,
    ReadOptions,
    ReadPrincipal,
    StreamEvent,
    StreamReadCommand,
)
from src.core.domain.ports import AnswerGenerator, SessionHistoryPort

logger = logging.getLogger(__name__)

SSE_HEARTBEAT_SECONDS = 15.0
SSE_STAGE_POLL_SECONDS = 0.1
EMPTY_MESSAGES = {
    "no_community_hit": "未检索到相关社区，本轮未生成回答。",
    "no_scene_hit_in_communities": "已检索到社区，但没有符合条件的场景证据。",
    "no_valid_evidence": "候选未形成可用证据，本轮未生成回答。",
}
SEARCH_MODE_NAMES = {
    SearchStrategy.DEEP: "deep",
    SearchStrategy.NORMAL: "normal",
    SearchStrategy.QUICK: "quick",
    SearchStrategy.EXPRESS: "express",
    SearchStrategy.COMMUNITY: "community",
}
_COMMUNITY_STAGES = (
    "community_searched",
    "community_scenes_searched",
    "community_evidence_packed",
)

PipelineFactory = Callable[[ReadContext], ReadPipeline]



def _community_failure_stage(stage: str, completed: set[str]) -> str:
    """The stage the host marks ``failed``: the running one, or the next pending."""

    if stage in _COMMUNITY_STAGES and stage in completed:
        return next(
            (key for key in _COMMUNITY_STAGES if key not in completed),
            "final_answer",
        )
    return stage


class StreamRead:
    """Produce the domain events of one streaming memory read."""

    def __init__(
        self,
        *,
        contexts: ReadContextFactory,
        pipeline_factory: PipelineFactory,
        history: SessionHistoryPort,
        answers: AnswerGenerator,
        default_limit: int = 10,
    ) -> None:
        self._contexts = contexts
        self._pipeline_factory = pipeline_factory
        self._history = history
        self._answers = answers
        self._default_limit = default_limit

    async def events(
        self,
        command: StreamReadCommand,
        principal: ReadPrincipal,
        *,
        language: str = "zh",
        request_id: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        request_id = request_id or str(uuid4())
        strategy = command.search_switch
        try:
            ctx = await self._contexts.build(
                principal=principal,
                end_user_id=command.end_user_id,
                language=language,
                draft=True,
            )
            if strategy == SearchStrategy.COMMUNITY and ctx.storage_type != StorageType.NEO4J:
                raise ReadError(
                    "社区检索仅支持图存储空间",
                    code=ReadErrorCode.INVALID_PARAMETER,
                    reason="unsupported_storage",
                    status_code=200,
                )
        except ModelUnavailableError as error:
            yield StreamEvent(
                "error",
                {
                    "request_id": request_id,
                    "code": ReadErrorCode.MEMORY_READ_FAILED.value,
                    "message": f"{error.model_type}模型不可用",
                },
            )
            return
        except EndUserNotFoundError:
            # Host parity: the validation page reports an unknown/foreign host as
            # a permission failure inside the stream, not as USER_NOT_FOUND.
            yield StreamEvent(
                "error",
                {
                    "request_id": request_id,
                    "code": ReadErrorCode.PERMISSION_DENIED.value,
                    "message": "记忆库不存在或无权访问",
                },
            )
            return
        except ReadError as error:
            if error.reason in {"invalid_input", "unsupported_storage"}:
                message = (
                    "社区检索仅支持图存储空间"
                    if error.reason == "unsupported_storage"
                    else "记忆库不存在或无权访问"
                )
                yield StreamEvent(
                    "error",
                    {
                        "request_id": request_id,
                        "code": error.code.value,
                        "message": message,
                    },
                )
                return
            logger.exception(
                "Unable to initialize memory read: request_id=%s end_user=%s",
                request_id,
                command.end_user_id,
            )
            yield StreamEvent(
                "error",
                {
                    "request_id": request_id,
                    "code": ReadErrorCode.MEMORY_READ_FAILED.value,
                    "message": "回复对话消息失败",
                },
            )
            return
        except Exception:
            logger.exception(
                "Unable to initialize memory read: request_id=%s end_user=%s",
                request_id,
                command.end_user_id,
            )
            yield StreamEvent(
                "error",
                {
                    "request_id": request_id,
                    "code": ReadErrorCode.MEMORY_READ_FAILED.value,
                    "message": "回复对话消息失败",
                },
            )
            return

        if strategy == SearchStrategy.COMMUNITY:
            async for event in self._community_events(
                ctx, command, request_id=request_id
            ):
                yield event
            return
        async for event in self._standard_events(
            ctx, command, request_id=request_id
        ):
            yield event

    # ── standard (non-community) path ───────────────────────────

    async def _standard_events(
        self,
        ctx: ReadContext,
        command: StreamReadCommand,
        *,
        request_id: str,
    ) -> AsyncIterator[StreamEvent]:
        running_task: asyncio.Task[Any] | None = None
        session_id = command.session_id
        try:
            yield StreamEvent(
                "start",
                {
                    "request_id": request_id,
                    "session_id": session_id,
                    "search_switch": command.search_switch.value,
                    "mode": SEARCH_MODE_NAMES.get(command.search_switch, "unknown"),
                    "backend": ctx.storage_type.value,
                    "limit": self._default_limit,
                },
            )

            running_task = asyncio.create_task(self._history.history(session_id))
            async for heartbeat in _stream_heartbeats(running_task):
                yield heartbeat
            history = await running_task
            running_task = None

            with memory_stage_collector() as retrieval_stages:
                pipeline = self._pipeline_factory(ctx)
                running_task = asyncio.create_task(
                    pipeline.run(
                        command.message,
                        command.search_switch,
                        history=history,
                        options=ReadOptions(
                            limit=self._default_limit,
                            record_display=False,
                            include_metadata=True,
                        ),
                    )
                )
                next_stage_index = 0
                event_loop = asyncio.get_running_loop()
                last_event_at = event_loop.time()
                while True:
                    while next_stage_index < len(retrieval_stages):
                        stage = retrieval_stages[next_stage_index]
                        next_stage_index += 1
                        if stage.get("stage") not in VALIDATION_STAGE_ALLOWLIST:
                            continue
                        last_event_at = event_loop.time()
                        yield StreamEvent(
                            "memory_stage", {"request_id": request_id, **stage}
                        )
                    if running_task.done():
                        break
                    await asyncio.wait({running_task}, timeout=SSE_STAGE_POLL_SECONDS)
                    if event_loop.time() - last_event_at >= SSE_HEARTBEAT_SECONDS:
                        last_event_at = event_loop.time()
                        yield StreamEvent("heartbeat")
                result = await running_task
                running_task = None

            fast_stage = build_fast_search_stage(
                command.search_switch, ctx.end_user_id, result
            )
            if fast_stage is not None:
                retrieval_stages.append(fast_stage)
                yield StreamEvent(
                    "memory_stage", {"request_id": request_id, **fast_stage}
                )

            retrieval_trace = build_validation_trace(
                request_id=request_id,
                query=command.message,
                search_switch=command.search_switch.value,
                end_user_id=ctx.end_user_id,
                result=result,
                collected_stages=retrieval_stages,
                backend=(
                    result.execution_trace.backend
                    if result.execution_trace
                    else ctx.storage_type.value
                ),
                limit=(
                    result.execution_trace.limit
                    if result.execution_trace
                    else self._default_limit
                ),
            )
            yield StreamEvent(
                "retrieval_trace",
                {"request_id": request_id, "trace": retrieval_trace},
            )

            intermediate_outputs = build_intermediate_outputs(
                result, command.search_switch
            )
            running_task = asyncio.create_task(
                self._answers.summarize(
                    ctx=ctx, query=command.message, result=result
                )
            )
            async for heartbeat in _stream_heartbeats(running_task):
                yield heartbeat
            answer = await running_task
            running_task = None

            running_task = asyncio.create_task(
                self._history.append(
                    session_id,
                    [
                        {"role": "user", "content": command.message},
                        {"role": "assistant", "content": answer},
                    ],
                )
            )
            async for heartbeat in _stream_heartbeats(running_task):
                yield heartbeat
            await running_task
            running_task = None

            yield StreamEvent(
                "message", {"request_id": request_id, "content": answer}
            )
            yield StreamEvent(
                "end",
                {
                    "request_id": request_id,
                    "session_id": session_id,
                    "intermediate_outputs": intermediate_outputs,
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(
                "Read operation error: request_id=%s end_user=%s error=%s",
                request_id,
                command.end_user_id,
                str(error),
                exc_info=True,
            )
            yield StreamEvent(
                "error",
                {
                    "request_id": request_id,
                    "code": ReadErrorCode.MEMORY_READ_FAILED.value,
                    "message": "回复对话消息失败",
                },
            )
        finally:
            if running_task is not None and not running_task.done():
                running_task.cancel()
                with suppress(asyncio.CancelledError):
                    await running_task

    # ── community path ──────────────────────────────────────────

    async def _community_events(
        self,
        ctx: ReadContext,
        command: StreamReadCommand,
        *,
        request_id: str,
    ) -> AsyncIterator[StreamEvent]:
        session_id = command.session_id
        running_task: asyncio.Task[Any] | None = None
        events: list[dict[str, Any]] = []
        next_event = 0
        failure_reason = "dependency_failed"

        def event(name: str, data: dict[str, Any]) -> StreamEvent:
            return StreamEvent(name, {**data, "request_id": request_id})

        try:
            yield event(
                "start",
                {
                    "session_id": session_id,
                    "search_switch": "6",
                    "mode": "community",
                    "backend": "neo4j",
                    "limit": COMMUNITY_DEFAULTS.evidence_top_k,
                },
            )
            with community_stage_collector() as events:
                stage = _COMMUNITY_STAGES[0]
                pipeline = self._pipeline_factory(ctx)
                running_task = asyncio.create_task(
                    pipeline.run(
                        command.message,
                        SearchStrategy.COMMUNITY,
                        history=[],
                        options=ReadOptions(
                            limit=COMMUNITY_DEFAULTS.evidence_top_k,
                            record_display=False,
                        ),
                    )
                )
                last_event_at = time.monotonic()
                while True:
                    while next_event < len(events):
                        payload = events[next_event]
                        next_event += 1
                        stage = payload.get("stage", stage)
                        last_event_at = time.monotonic()
                        yield StreamEvent("memory_stage", payload)
                    if running_task.done():
                        break
                    await asyncio.wait({running_task}, timeout=SSE_STAGE_POLL_SECONDS)
                    if time.monotonic() - last_event_at >= SSE_HEARTBEAT_SECONDS:
                        last_event_at = time.monotonic()
                        yield StreamEvent("heartbeat")
                result = await running_task
                running_task = None
            trace = project_community_trace(result.community_trace)
            items = trace["items"]
            yield event(
                "retrieval_trace",
                {
                    "trace": {
                        "mode": trace["kind"],
                        "status": trace["status"],
                        "empty_reason": trace["empty_reason"],
                        "items": [
                            {key: item[key] for key in ("id", "community_id", "evidence_no")}
                            for item in items
                        ],
                    }
                },
            )
            if items:
                stage = "final_answer"
                failure_reason = "answer_failed"
                running_task = asyncio.create_task(
                    self._answers.community_answer(
                        ctx=ctx, query=command.message, evidence=items
                    )
                )
                async for heartbeat in _stream_heartbeats(running_task):
                    yield heartbeat
                answer = await running_task
                running_task = None
                if not answer.strip():
                    raise AnswerGenerationError(
                        "Community answer is empty", reason="answer_failed"
                    )
                failure_reason = "dependency_failed"
                running_task = asyncio.create_task(
                    self._history.append(
                        session_id,
                        [
                            {"role": "user", "content": command.message},
                            {"role": "assistant", "content": answer},
                        ],
                    )
                )
                async for heartbeat in _stream_heartbeats(running_task):
                    yield heartbeat
                await running_task
                running_task = None
            else:
                empty_reason = trace["empty_reason"]
                answer = EMPTY_MESSAGES.get(
                    empty_reason, "未检索到相关社区，本轮未生成回答。"
                )
            yield event("message", {"content": answer})
            yield event(
                "end",
                {"session_id": session_id, "intermediate_outputs": []},
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            reason = (
                error.reason
                if isinstance(error, AnswerGenerationError)
                else "scan_timeout"
                if isinstance(error, TimeoutError)
                else failure_reason
            )
            logger.exception(
                "Community validation failed: request_id=%s reason=%s",
                request_id,
                reason,
            )
            # A dependency can fail before producing a stage; keep completed snapshots.
            for payload in events[next_event:]:
                yield StreamEvent("memory_stage", payload)
            completed = {
                payload["stage"] for payload in events if payload["status"] == "completed"
            }
            # The host marks the stage that was actually running as failed.
            stage = _community_failure_stage(stage, completed)
            for key in _COMMUNITY_STAGES:
                if key not in completed:
                    yield event(
                        "memory_stage",
                        {
                            "stage": key,
                            "status": "failed" if key == stage else "skipped",
                            "data": {},
                        },
                    )
            yield event(
                "error",
                {
                    "code": ReadErrorCode.MEMORY_READ_FAILED.value,
                    "message": (
                        "回答生成失败，请重新验证"
                        if stage == "final_answer"
                        else "社区检索失败，请重新验证"
                    ),
                },
            )
        finally:
            if running_task is not None:
                running_task.cancel()
                await asyncio.gather(running_task, return_exceptions=True)


async def _stream_heartbeats(
    task: asyncio.Task[Any],
) -> AsyncIterator[StreamEvent]:
    """Emit a heartbeat comment while the wrapped task is still running."""

    while not task.done():
        done, _ = await asyncio.wait({task}, timeout=SSE_HEARTBEAT_SECONDS)
        if not done:
            yield StreamEvent("heartbeat")


__all__ = [
    "EMPTY_MESSAGES",
    "SSE_HEARTBEAT_SECONDS",
    "SSE_STAGE_POLL_SECONDS",
    "StreamRead",
]