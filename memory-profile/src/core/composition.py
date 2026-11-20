"""Composition root of the memory-read core.

This is the only module that knows both the application layer and the adapter
implementations. It builds the use cases from the process runtime once per app
and is what the transport layer injects.

Adapters are imported lazily, one port at a time: a test (or a deployment that
has not enabled an adapter yet) can override a single port without importing the
others. The transport layer must not import ``src.core.adapters`` directly; it
asks this module for a :class:`MemoryReadModule` and calls its use cases.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.core.application.read.context import ReadContextFactory
from src.core.application.read.read_memory import ReadMemory
from src.core.application.read.read_pipeline import ReadPipeline
from src.core.application.read.stream_read import StreamRead
from src.core.domain.models import ReadContext

if TYPE_CHECKING:  # pragma: no cover - type-only import
    from src.runtime import ProfileRuntime

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MemoryReadModule:
    """The memory-read use cases wired to their adapters."""

    read_memory: ReadMemory
    stream_read: StreamRead


#: Bound on the shutdown drain of detached display-card writes, matching the
#: host ``MemoryRetrievalDisplayQueue.stop`` timeout.
DISPLAY_SHUTDOWN_TIMEOUT_SECONDS = 10.0


async def flush_read_side_effects() -> None:
    """Drain pending display-card writes before the process closes its pool.

    The read path never waits for a card (a dropped card is acceptable), so the
    only chance to land the ones already scheduled is shutdown. The host drains
    its display queue with the same bound; a write that outlives it is logged and
    dropped instead of blocking the process exit.
    """

    from src.core.adapters.read.side_effects import flush_pending_display_writes

    try:
        async with asyncio.timeout(DISPLAY_SHUTDOWN_TIMEOUT_SECONDS):
            await flush_pending_display_writes()
    except TimeoutError:
        logger.warning(
            "read side effects: display-card drain exceeded %.1fs; dropping the rest",
            DISPLAY_SHUTDOWN_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001 - shutdown must still release the runtime
        logger.warning("read side effects: display-card drain failed", exc_info=True)


def _build_end_users(runtime: ProfileRuntime) -> Any:
    from src.core.adapters.read.config import build_end_user_port

    return build_end_user_port(runtime)


def _build_configs(runtime: ProfileRuntime) -> Any:
    from src.core.adapters.read.config import build_config_port

    return build_config_port(runtime)


def _build_models(runtime: ProfileRuntime) -> Any:
    from src.core.adapters.models.model_gateway import build_model_gateway

    return build_model_gateway(runtime)


def _build_prompts() -> Any:
    from src.core.adapters.models.prompts import build_prompt_renderer

    return build_prompt_renderer()


def _build_sessions(runtime: ProfileRuntime) -> Any:
    from src.core.adapters.read.session import build_session_history

    return build_session_history(runtime)


def _build_answers(
    runtime: ProfileRuntime, *, prompts: Any, models: Any
) -> Any:
    from src.core.adapters.models.answer import build_answer_generator

    return build_answer_generator(runtime, prompts=prompts, models=models)


def _build_search_factory(
    runtime: ProfileRuntime, *, prompts: Any, models: Any
) -> Any:
    from src.core.adapters.retrieval import build_search_factory
    from src.core.adapters.storage import build_read_storage

    # The composition root is the only place that constructs adapters; the
    # retrieval factory receives the storage adapter instead of building it.
    storage = build_read_storage(runtime)
    return build_search_factory(
        storage=storage,
        postgres=runtime.postgres,
        prompts=prompts,
        models=models,
        enterprise=runtime.memory_enterprise,
    )


def _build_side_effects(runtime: ProfileRuntime) -> Any:
    from src.core.adapters.read.side_effects import build_side_effects

    return build_side_effects(runtime)


def _build_alerts(runtime: ProfileRuntime) -> Any:
    from src.core.adapters.read.alerts import build_alert_port

    # ``memory_enterprise`` is the enterprise switch: an empty base URL keeps
    # the open build's log-only adapter.
    return build_alert_port(getattr(runtime, "memory_enterprise", None))


def build_memory_read_module(
    runtime: ProfileRuntime,
    *,
    overrides: dict[str, Any] | None = None,
) -> MemoryReadModule:
    """Build the read module from the process runtime.

    ``overrides`` lets tests replace any port/adapter factory by name without
    touching the composition (``end_users``, ``configs``, ``models``,
    ``prompts``, ``sessions``, ``answers``, ``search_factory``,
    ``side_effects``, ``alerts``).
    """

    overrides = overrides or {}

    end_users = overrides.get("end_users") or _build_end_users(runtime)
    configs = overrides.get("configs") or _build_configs(runtime)
    models = overrides.get("models") or _build_models(runtime)
    prompts = overrides.get("prompts") or _build_prompts()
    sessions = overrides.get("sessions") or _build_sessions(runtime)
    answers = overrides.get("answers") or _build_answers(
        runtime, prompts=prompts, models=models
    )
    search_factory = overrides.get("search_factory") or _build_search_factory(
        runtime, prompts=prompts, models=models
    )
    side_effects = overrides.get("side_effects") or _build_side_effects(runtime)
    alerts = overrides.get("alerts") or _build_alerts(runtime)
    from src.core.adapters.read.degrade_cache import RedisDegradeCache

    degrade_cache = overrides.get("degrade_cache") or RedisDegradeCache(runtime.redis)

    contexts = ReadContextFactory(end_users, configs)

    def pipeline_for(ctx: ReadContext) -> ReadPipeline:
        return ReadPipeline(
            ctx=ctx,
            search_factory=search_factory,
            config_port=configs,
            model_gateway=models,
            prompts=prompts,
            alerts=alerts,
            degrade_cache=degrade_cache,
        )

    return MemoryReadModule(
        read_memory=ReadMemory(
            contexts=contexts,
            pipeline_factory=pipeline_for,
            side_effects=side_effects,
        ),
        stream_read=StreamRead(
            contexts=contexts,
            pipeline_factory=pipeline_for,
            history=sessions,
            answers=answers,
        ),
    )


__all__ = [
    "DISPLAY_SHUTDOWN_TIMEOUT_SECONDS",
    "MemoryReadModule",
    "build_memory_read_module",
    "flush_read_side_effects",
]