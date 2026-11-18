"""Non-streaming read use case (``POST /internal/v1/memory/read/sync``).

Serves the API-key face. Mirrors the host contract:

* single host: ``{"answer": ..., "intermediate_outputs": [...]}``;
* multiple hosts: ``{"<end_user_id>": {answer, intermediate_outputs}, ...}``,
  read concurrently, with a failing host skipped (the batch still succeeds);
  the map is keyed by the id the caller sent, not by the merge target it routed
  to, so a caller can match answers to its own request entries;
* every host is validated before any read starts — an unknown/foreign host
  rejects the whole request, exactly like the host implementation;
* ``record_display`` and the DEEP/NORMAL short-term memo are non-fatal side
  effects (a failure never changes the returned answer).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from uuid import uuid4

from src.core.application.read.context import ReadContextFactory
from src.core.application.read.projection import memory_dumps
from src.core.application.read.read_pipeline import ReadPipeline
from src.core.application.text.retrieval_text import process_query
from src.core.domain.enums import SearchStrategy
from src.core.domain.errors import EndUserNotFoundError
from src.core.domain.models import (
    ReadContext,
    ReadOptions,
    ReadOutcome,
    ReadPrincipal,
    ReadResult,
    SyncReadCommand,
)
from src.core.domain.ports import ReadSideEffectsPort
from src.core.domain.text import clean_query_for_display

logger = logging.getLogger(__name__)

PipelineFactory = Callable[[ReadContext], ReadPipeline]


class ReadMemory:
    """Execute a non-streaming memory read for one or many end users."""

    def __init__(
        self,
        *,
        contexts: ReadContextFactory,
        pipeline_factory: PipelineFactory,
        side_effects: ReadSideEffectsPort,
        language_default: str = "zh",
    ) -> None:
        self._contexts = contexts
        self._pipeline_factory = pipeline_factory
        self._side_effects = side_effects
        self._language_default = language_default

    async def execute(
        self,
        command: SyncReadCommand,
        principal: ReadPrincipal,
        *,
        language: str | None = None,
        options: ReadOptions | None = None,
    ) -> ReadOutcome | dict[str, ReadOutcome]:
        resolved_language = language or self._language_default
        options = options or ReadOptions(record_display=True)
        end_user_ids = list(dict.fromkeys(command.end_user_ids))
        if not end_user_ids:
            raise EndUserNotFoundError("No users found.", reason="end_user_not_found")

        # Validate every host first; an invalid host rejects the whole request.
        # The requested id is kept next to its context: the read (and its side
        # effects) must use the merge-routed target, while the response stays
        # keyed by what the caller asked for — the host contract for the batch
        # face, where callers match answers to their own request entries.
        requests: list[tuple[str, ReadContext]] = []
        for end_user_id in end_user_ids:
            requests.append(
                (
                    end_user_id,
                    await self._contexts.build(
                        principal=principal,
                        end_user_id=end_user_id,
                        language=resolved_language,
                        draft=False,
                    ),
                )
            )

        if len(requests) == 1:
            outcome = await self._read_one(
                requests[0][1], command, options=options
            )
            return outcome

        results = await asyncio.gather(
            *(
                self._read_one(ctx, command, options=options)
                for _requested_id, ctx in requests
            ),
            return_exceptions=True,
        )
        response: dict[str, ReadOutcome] = {}
        for (requested_id, _context), result in zip(requests, results, strict=True):
            if isinstance(result, BaseException):
                # One host failing must not fail the batch; keep the others.
                logger.warning(
                    "[ReadMemory] skipped one end user: end_user=%s error=%s",
                    requested_id,
                    type(result).__name__,
                )
                continue
            response[requested_id] = result
        return response

    async def _read_one(
        self,
        ctx: ReadContext,
        command: SyncReadCommand,
        *,
        options: ReadOptions,
    ) -> ReadOutcome:
        pipeline = self._pipeline_factory(ctx)
        result = await pipeline.run(
            command.message,
            command.search_switch,
            history=[],
            options=options,
            retrieval_operation_id=uuid4().hex,
        )
        await self._apply_side_effects(ctx, command, result, options=options)
        return ReadOutcome(
            answer=result.content,
            intermediate_outputs=memory_dumps(result),
            result=result,
        )

    async def _apply_side_effects(
        self,
        ctx: ReadContext,
        command: SyncReadCommand,
        result: ReadResult,
        *,
        options: ReadOptions,
    ) -> None:
        if not ctx.draft and command.search_switch in {
            SearchStrategy.DEEP,
            SearchStrategy.NORMAL,
        }:
            try:
                await self._side_effects.save_short_term(
                    end_user_id=ctx.end_user_id,
                    query=process_query(command.message),
                    answer=result.content,
                    memories=result.memories,
                    search_switch=command.search_switch.value,
                )
            except Exception:
                logger.warning(
                    "[ReadMemory] short-term memo failed (read unaffected): end_user=%s",
                    ctx.end_user_id,
                    exc_info=True,
                )

        if options.record_display:
            display_query = clean_query_for_display(
                process_query(command.message)
            )
            try:
                await self._side_effects.enqueue_display(
                    end_user_id=ctx.end_user_id,
                    workspace_id=ctx.workspace_id,
                    language=ctx.language,
                    search_mode=command.search_switch.name.lower(),
                    query=display_query,
                    result=result,
                )
            except Exception:
                logger.warning(
                    "[ReadMemory] retrieval display enqueue failed (read unaffected): "
                    "end_user=%s",
                    ctx.end_user_id,
                    exc_info=True,
                )


__all__ = ["PipelineFactory", "ReadMemory"]