"""Memory retrieval stage contract, custom-event dispatch, and per-tool collector."""

from __future__ import annotations

import logging
import math
from collections.abc import AsyncIterable, AsyncIterator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger(__name__)
MemoryStagePayload = dict[str, Any]
MemoryStageSink = list[MemoryStagePayload]
# ContextVar keeps capture state isolated across concurrent streaming requests.
_STAGE_CAPTURE_ENABLED: ContextVar[bool] = ContextVar("memory_stage_capture_enabled", default=False)
_STAGE_SINK: ContextVar[MemoryStageSink | None] = ContextVar("memory_stage_sink", default=None)

# Exact field sets form the public contract and prevent internal retrieval data from leaking.
_STAGE_FIELDS = {
    "profile_loaded": {"has_profile", "profile"},
    "query_split": {"count", "questions"},
    "hybrid_searched": {"hit_count", "memory_count", "shown_count", "items"},
    "relation_searched": {"hit_count", "relation_count", "shown_count", "items"},
    "results_merged": {"memory_count", "relation_count"},
    "results_ranked": {"count", "order"},
    "context_prepared": {"memory_count"},
    "result_ready": {"duration_ms", "total_count", "shown_count", "items"},
}
_PROFILE_FIELDS = {
    "aliases_name",
    "description",
    "core_facts",
    "goals",
    "interests",
    "relations",
    "beliefs_or_stances",
    "anchors",
    "events",
    "traits",
}
_MEMORY_ITEM_FIELDS = {"rank", "memory_type", "source", "score", "content"}
_FILE_FIELDS = {"file_name", "file_path", "file_type", "perceptual_type"}
_RELATION_ITEM_FIELDS = {"source", "relation", "target", "target_desc"}


class StageCollector(list[MemoryStagePayload]):
    """List collector that remains callable for older local test helpers."""

    def __call__(self, payload: MemoryStagePayload) -> None:
        self.append(payload)


def _is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _has_exact_fields(value: Any, fields: set[str]) -> bool:
    return isinstance(value, dict) and set(value) == fields


def _valid_profile(profile: Any) -> bool:
    if not _has_exact_fields(profile, _PROFILE_FIELDS):
        return False
    if not isinstance(profile["description"], str):
        return False
    return all(
        isinstance(profile[field], list)
        and len(profile[field]) <= 5
        and all(isinstance(item, str) for item in profile[field])
        and all(len(item) <= 200 for item in profile[field])
        for field in _PROFILE_FIELDS - {"description"}
    ) and len(profile["description"]) <= 200


def _valid_file(file_data: Any) -> bool:
    if not _has_exact_fields(file_data, _FILE_FIELDS):
        return False
    return (
        all(isinstance(file_data[field], str) for field in ("file_name", "file_path", "file_type"))
        and (
            file_data["perceptual_type"] is None
            or _is_non_negative_int(file_data["perceptual_type"])
        )
    )


def _valid_memory_item(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    fields = set(item)
    if fields not in (_MEMORY_ITEM_FIELDS, _MEMORY_ITEM_FIELDS | {"file"}):
        return False
    return (
        _is_positive_int(item["rank"])
        and isinstance(item["memory_type"], str)
        and isinstance(item["source"], str)
        and _is_number(item["score"])
        and isinstance(item["content"], str)
        and ("file" not in item or (item["memory_type"] == "file" and item["source"] == "Perceptual"))
        and ("file" not in item or _valid_file(item["file"]))
    )


def _valid_relation_item(item: Any) -> bool:
    return (
        _has_exact_fields(item, _RELATION_ITEM_FIELDS)
        and all(isinstance(item[field], str) for field in _RELATION_ITEM_FIELDS)
    )


def _valid_stage_data(stage: str, data: dict[str, Any]) -> bool:
    if stage == "profile_loaded":
        return isinstance(data["has_profile"], bool) and _valid_profile(data["profile"])
    if stage == "query_split":
        return (
            _is_non_negative_int(data["count"])
            and isinstance(data["questions"], list)
            and len(data["questions"]) <= 5
            and all(isinstance(question, str) for question in data["questions"])
            and all(len(question) <= 100 for question in data["questions"])
            and data["count"] >= len(data["questions"])
        )
    if stage in {"hybrid_searched", "relation_searched", "result_ready"}:
        count_fields = {
            "hybrid_searched": ("hit_count", "memory_count", "shown_count"),
            "relation_searched": ("hit_count", "relation_count", "shown_count"),
            "result_ready": ("total_count", "shown_count"),
        }[stage]
        item_validator = _valid_relation_item if stage == "relation_searched" else _valid_memory_item
        return (
            (stage != "result_ready" or _is_positive_int(data["duration_ms"]))
            and all(_is_non_negative_int(data[field]) for field in count_fields)
            and isinstance(data["items"], list)
            and all(item_validator(item) for item in data["items"])
            and data["shown_count"] == len(data["items"])
        )
    if stage == "results_merged":
        return _is_non_negative_int(data["memory_count"]) and _is_non_negative_int(data["relation_count"])
    if stage == "results_ranked":
        return _is_non_negative_int(data["count"]) and data["order"] == "score_desc"
    if stage == "context_prepared":
        return _is_non_negative_int(data["memory_count"])
    return False


def build_memory_stage_payload(
    *,
    stage: str,
    data: dict[str, Any] | None = None,
) -> MemoryStagePayload | None:
    """Validate a stage against the public schema, dropping unknown or malformed data."""
    allowed = _STAGE_FIELDS.get(stage)
    if allowed is None:
        logger.warning("Dropping unknown memory stage: %s", stage)
        return None
    if not isinstance(data, dict):
        logger.warning("Dropping memory stage %s with non-object data", stage)
        return None
    clean = data
    if set(clean) != allowed or not _valid_stage_data(stage, clean):
        logger.warning("Dropping memory stage %s with invalid payload", stage)
        return None
    return {
        "stage": stage,
        "status": "completed",
        "data": clean,
    }


def get_memory_stage_sink() -> MemoryStageSink | None:
    return _STAGE_SINK.get()


def is_memory_stage_capture_enabled() -> bool:
    return _STAGE_CAPTURE_ENABLED.get()


@contextmanager
def memory_stage_capture() -> Iterator[None]:
    """Enable retrieval-stage production for one target request path."""
    token = _STAGE_CAPTURE_ENABLED.set(True)
    try:
        yield
    finally:
        _STAGE_CAPTURE_ENABLED.reset(token)


async def capture_memory_stage_stream(stream: AsyncIterable[Any]) -> AsyncIterator[Any]:
    """Scope capture to upstream iteration so downstream consumers cannot inherit it."""
    iterator = aiter(stream)
    try:
        while True:
            try:
                with memory_stage_capture():
                    item = await anext(iterator)
            except StopAsyncIteration:
                return
            yield item
    finally:
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()


@contextmanager
def memory_stage_collector() -> Iterator[MemoryStageSink]:
    """Buffer weak-model stages until its completed tool step can be replayed."""
    stages: MemoryStageSink = StageCollector()
    token = _STAGE_SINK.set(stages)
    try:
        yield stages
    finally:
        _STAGE_SINK.reset(token)


async def emit_memory_stage(stage: str, data: dict[str, Any] | None = None) -> MemoryStagePayload | None:
    """Collect for weak ReAct calls, otherwise dispatch a LangChain custom event."""
    sink = get_memory_stage_sink()
    if not _STAGE_CAPTURE_ENABLED.get() and sink is None:
        return None
    try:
        payload = build_memory_stage_payload(stage=stage, data=data)
    except Exception:
        logger.warning("Unable to build memory stage %s", stage, exc_info=True)
        return None
    if payload is None:
        return None
    if sink is not None:
        sink.append(payload)
        return payload
    try:
        from langchain_core.callbacks.manager import adispatch_custom_event
        await adispatch_custom_event("memory_stage", payload)
    except Exception:
        # Stage telemetry must never change retrieval semantics.
        logger.debug("Memory stage dispatch unavailable for %s", stage, exc_info=True)
    return payload
