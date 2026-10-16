"""Complete Elasticsearch snapshot recall and Coding Agent prompt rendering."""

from __future__ import annotations

import logging
import re
import time
import uuid
from pathlib import Path

from jinja2 import Template

from app.core.memory.storage.custom.preference_recall import (
    FullPreferenceCandidate,
    PreferenceRecallStorage,
)
from app.core.memory.storage.service import get_storage_service
from app.core.trace import get_trace_id
from app.db import get_async_db_context
from app.services.memory_config_service import MemoryConfigService

from .models import FullPreferenceRecallResult, RecalledPreference
from .recall_service import PreferenceRecallForbiddenError, masked_identifier

logger = logging.getLogger(__name__)
_TEMPLATE = Template(
    (Path(__file__).parent / "prompts/full_preference_prompt.jinja2").read_text(
        encoding="utf-8"
    )
)
_TAG = re.compile(r"<(?=/?historical_user_preferences(?:\s|>))", re.IGNORECASE)


def _clean(text: str) -> str:
    return " ".join(text.split())


def _rank(item: FullPreferenceCandidate) -> tuple[float, str, int]:
    return (-item.updated_at.timestamp(), item.document_id, item.item_index)


def assemble_full_preferences(
    candidates: list[FullPreferenceCandidate],
) -> FullPreferenceRecallResult:
    winners: dict[str, FullPreferenceCandidate] = {}
    for candidate in candidates:
        cleaned = _clean(candidate.preference_text)
        if cleaned and (cleaned not in winners or _rank(candidate) < _rank(winners[cleaned])):
            winners[cleaned] = candidate
    ordered = sorted(
        winners.items(),
        key=lambda pair: (
            0 if pair[1].is_global else 1,
            *_rank(pair[1]),
        ),
    )
    preferences = [
        RecalledPreference(preference_text=text, updated_at=item.updated_at)
        for text, item in ordered
    ]
    prompt = ""
    if preferences:
        lines = [
            _TAG.sub("&lt;", item.preference_text)
            for item in preferences
        ]
        prompt = _TEMPLATE.render(lines=lines)
    return FullPreferenceRecallResult(
        preferences=preferences, count=len(preferences), preference_prompt=prompt
    )


def _log_stage(
    *,
    stage: int,
    status: str,
    request_id: str,
    workspace_id: str,
    end_user_id: str,
    duration_ms: float,
    reason: str = "none",
    pages: int | None = None,
    candidate_count: int = 0,
    result_count: int = 0,
    pit_complete: bool = False,
    response_bytes: int = 0,
    empty: bool | None = None,
) -> None:
    """Log phase counts and timing without user text or prompt content."""
    logger.info(
        "[FullPreferenceRecall][%d/3] step%d %s request_id=%s workspace_id=%s "
        "end_user_id=%s pages=%s candidates=%d results=%d pit_complete=%s "
        "response_bytes=%d empty=%s reason=%s duration_ms=%.2f",
        stage, stage, status, request_id,
        masked_identifier(workspace_id), masked_identifier(end_user_id),
        pages, candidate_count, result_count, pit_complete,
        response_bytes, empty, reason, duration_ms,
    )


async def execute_full_preference_recall(
    *, config_id: uuid.UUID, workspace_id: uuid.UUID | str, end_user_id: str
) -> FullPreferenceRecallResult:
    """Resolve workspace ownership, then read ES without a model or write switch."""
    started = time.perf_counter()
    request_id = get_trace_id() or uuid.uuid4().hex
    workspace_id_text = str(workspace_id)
    stage = 1
    stage_started = started
    pages: int | None = None
    pit_complete = False
    candidates: list[FullPreferenceCandidate] = []
    try:
        async with get_async_db_context() as db:
            config = await MemoryConfigService(db).load_memory_config_async(config_id)
        if str(config.workspace_id) != workspace_id_text:
            raise PreferenceRecallForbiddenError("memory configuration is not owned by workspace")
        _log_stage(
            stage=1, status="completed", request_id=request_id,
            workspace_id=workspace_id_text, end_user_id=end_user_id,
            duration_ms=(time.perf_counter() - stage_started) * 1000,
        )

        stage = 2
        stage_started = time.perf_counter()
        storage = PreferenceRecallStorage(get_storage_service()._backend_factory)
        candidates, pages = await storage.query_all(end_user_id=end_user_id)
        pit_complete = True
        _log_stage(
            stage=2, status="completed", request_id=request_id,
            workspace_id=workspace_id_text, end_user_id=end_user_id,
            duration_ms=(time.perf_counter() - stage_started) * 1000,
            pages=pages, candidate_count=len(candidates), pit_complete=True,
        )

        stage = 3
        stage_started = time.perf_counter()
        result = assemble_full_preferences(candidates)
        _log_stage(
            stage=3, status="completed", request_id=request_id,
            workspace_id=workspace_id_text, end_user_id=end_user_id,
            duration_ms=(time.perf_counter() - stage_started) * 1000,
            pages=pages, candidate_count=len(candidates), result_count=result.count,
            pit_complete=True,
            response_bytes=len(result.model_dump_json().encode("utf-8")),
            empty=result.count == 0,
        )
        return result
    except Exception as exc:
        _log_stage(
            stage=stage, status="failed", request_id=request_id,
            workspace_id=workspace_id_text, end_user_id=end_user_id,
            duration_ms=(time.perf_counter() - stage_started) * 1000,
            reason=type(exc).__name__, pages=pages,
            candidate_count=len(candidates), pit_complete=pit_complete,
        )
        raise
