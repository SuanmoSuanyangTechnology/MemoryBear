"""Application service for on-demand Coding Agent preference recall."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Sequence

from pydantic import ValidationError

from app.core.config import settings
from app.core.memory.pipelines.base_pipeline import ModelClientMixin
from app.core.memory.storage.custom.preference_recall import (
    PreferenceRecallCandidate,
    PreferenceRecallStorage,
)
from app.core.memory.storage.service import get_storage_service
from app.core.trace import get_trace_id
from app.db import get_async_db_context
from app.services.memory_config_service import MemoryConfigService

from .models import (
    PreferenceRecallContextMessage,
    PreferenceRecallRequest,
    PreferenceRecallTarget,
    RecalledPreference,
)
from .ontology_registry import load_preference_ontology
from .prompt_runner import PreferencePromptRunner

logger = logging.getLogger(__name__)


def masked_identifier(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


class PreferenceRecallValidationError(Exception):
    pass


class PreferenceRecallForbiddenError(Exception):
    pass


@dataclass(frozen=True)
class PreferenceRecallLimits:
    max_context_messages: int
    max_context_chars: int
    max_preferences: int
    per_bucket_limit: int
    timeout_ms: int

    @classmethod
    def from_settings(cls) -> "PreferenceRecallLimits":
        return cls(
            max_context_messages=settings.PREFERENCE_RECALL_MAX_CONTEXT_MESSAGES,
            max_context_chars=settings.PREFERENCE_RECALL_MAX_CONTEXT_CHARS,
            max_preferences=settings.PREFERENCE_RECALL_MAX_PREFERENCES,
            per_bucket_limit=settings.PREFERENCE_RECALL_PER_BUCKET_LIMIT,
            timeout_ms=settings.PREFERENCE_RECALL_TIMEOUT_MS,
        )


@dataclass
class _RecallTelemetry:
    target_count: int = 0
    stage: int = 1
    stage_started: float = 0.0


class PreferenceRecallService:
    """Route, query, merge, deduplicate and limit recall results."""

    def __init__(
        self,
        *,
        configured_workspace_id: uuid.UUID | str,
        prompt_runner: PreferencePromptRunner,
        storage: PreferenceRecallStorage,
        limits: PreferenceRecallLimits | None = None,
    ):
        self.configured_workspace_id = str(configured_workspace_id)
        self.prompt_runner = prompt_runner
        self.storage = storage
        self.limits = limits or PreferenceRecallLimits.from_settings()

    # 公共能力：记录标识、计数和耗时；step2 附闭集目标，不记录请求或模型正文。
    @staticmethod
    def _log_stage(
        *,
        stage: int,
        status: str,
        request_id: str,
        workspace_id: str,
        end_user_id: str,
        duration_ms: float,
        reason: str = "none",
        context_count: int = 0,
        context_chars: int = 0,
        context_truncated: bool = False,
        target_count: int = 0,
        targets: Sequence[PreferenceRecallTarget] | None = None,
        candidate_count: int = 0,
        result_count: int = 0,
        preference_truncated: bool = False,
    ) -> None:
        target_details = ""
        if targets is not None:
            target_details = " target_details=" + json.dumps(
                [
                    {"subject": target.subject, "situation_key": target.situation_key}
                    for target in targets
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        logger.info(
            "[PreferenceRecall][%d/3] step%d %s request_id=%s workspace_id=%s "
            "end_user_id=%s context_count=%d context_chars=%d context_truncated=%s "
            "targets=%d%s candidates=%d results=%d preference_truncated=%s "
            "reason=%s duration_ms=%.2f",
            stage, stage, status, request_id,
            masked_identifier(workspace_id), masked_identifier(end_user_id),
            context_count, context_chars, context_truncated, target_count, target_details,
            candidate_count, result_count, preference_truncated, reason, duration_ms,
        )

    # step1：限制近期上下文，避免超出提示词预算。
    def _trim_context(
        self,
        context_messages: Sequence[PreferenceRecallContextMessage],
    ) -> tuple[list[dict[str, str]], bool]:
        max_messages = self.limits.max_context_messages
        selected = list(context_messages[-max_messages:]) if max_messages else []
        truncated = len(selected) != len(context_messages)
        remaining = self.limits.max_context_chars
        newest_first: list[dict[str, str]] = []
        for message in reversed(selected):
            if remaining <= 0:
                truncated = True
                break
            content = message.content
            if len(content) > remaining:
                content = content[-remaining:]
                truncated = True
            newest_first.append({"role": message.role, "content": content})
            remaining -= len(content)
        newest_first.reverse()
        return newest_first, truncated

    # step2：识别目标偏好桶，并记录模型路由结果。
    async def _route_targets(
        self,
        *,
        request: PreferenceRecallRequest,
        context_messages: list[dict[str, str]],
        telemetry: _RecallTelemetry,
        request_id: str,
        workspace_id: str,
        end_user_id: str,
    ) -> list[PreferenceRecallTarget]:
        telemetry.stage = 2
        telemetry.stage_started = time.perf_counter()
        targets = await self.prompt_runner.route_recall(
            current_user_message=request.current_user_message,
            context_messages=context_messages,
        )
        telemetry.target_count = len(targets)
        self._log_stage(
            stage=2, status="completed", request_id=request_id,
            workspace_id=workspace_id, end_user_id=end_user_id,
            duration_ms=(time.perf_counter() - telemetry.stage_started) * 1000,
            target_count=telemetry.target_count,
            targets=targets,
        )
        return targets

    # step3：按特定场景优先规则去重，并限制返回数量。
    def _select_preferences(
        self,
        candidates: Sequence[PreferenceRecallCandidate],
    ) -> tuple[list[RecalledPreference], bool]:
        winners: dict[str, PreferenceRecallCandidate] = {}
        for candidate in candidates:
            key = " ".join(candidate.preference_text.strip().split())
            if not key:
                continue
            previous = winners.get(key)
            if previous is None or self._dedup_rank(candidate) < self._dedup_rank(previous):
                winners[key] = candidate

        ordered = sorted(winners.items(), key=lambda item: self._sort_rank(item[1], item[0]))
        preferences = [
            RecalledPreference(
                preference_text=normalized_text,
                updated_at=candidate.updated_at,
            )
            for normalized_text, candidate in ordered[: self.limits.max_preferences]
        ]
        return preferences, len(ordered) > self.limits.max_preferences

    # step3：同一偏好优先保留特定场景的记录。
    @staticmethod
    def _dedup_rank(candidate: PreferenceRecallCandidate) -> tuple[Any, ...]:
        return (
            1 if candidate.is_global else 0,
            -candidate.updated_at.timestamp(),
            candidate.target_index,
            candidate.item_index,
        )

    # step3：保持目标顺序，并稳定排列同一目标内的候选项。
    @staticmethod
    def _sort_rank(
        candidate: PreferenceRecallCandidate,
        normalized_text: str,
    ) -> tuple[Any, ...]:
        return (
            candidate.target_index,
            1 if candidate.is_global else 0,
            -candidate.updated_at.timestamp(),
            candidate.item_index,
            normalized_text,
        )

    # step3：查询精确偏好桶并生成最终结果。
    async def _query_and_select(
        self,
        *,
        end_user_id: str,
        targets: Sequence[PreferenceRecallTarget],
        telemetry: _RecallTelemetry,
        request_id: str,
        workspace_id: str,
    ) -> list[RecalledPreference]:
        telemetry.stage = 3
        telemetry.stage_started = time.perf_counter()
        if not targets:
            self._log_stage(
                stage=3, status="skipped", request_id=request_id,
                workspace_id=workspace_id, end_user_id=end_user_id,
                duration_ms=0, reason="no_targets",
            )
            return []

        candidates = await self.storage.query_buckets(
            end_user_id=end_user_id,
            targets=targets,
            per_bucket_limit=self.limits.per_bucket_limit,
        )
        selected, truncated = self._select_preferences(candidates)
        self._log_stage(
            stage=3, status="completed", request_id=request_id,
            workspace_id=workspace_id, end_user_id=end_user_id,
            duration_ms=(time.perf_counter() - telemetry.stage_started) * 1000,
            target_count=telemetry.target_count, candidate_count=len(candidates),
            result_count=len(selected), preference_truncated=truncated,
        )
        return selected

    # 总编排：依次完成上下文准备、目标路由和精确检索。
    async def recall(
        self,
        *,
        workspace_id: uuid.UUID | str,
        end_user_id: str,
        request: PreferenceRecallRequest,
    ) -> list[RecalledPreference]:
        started = time.perf_counter()
        telemetry = _RecallTelemetry(stage_started=started)
        request_id = get_trace_id() or uuid.uuid4().hex
        workspace_id_text = str(workspace_id)

        if workspace_id_text != self.configured_workspace_id:
            self._log_stage(
                stage=1, status="failed", request_id=request_id,
                workspace_id=workspace_id_text, end_user_id=end_user_id,
                duration_ms=(time.perf_counter() - started) * 1000,
                reason="workspace_mismatch",
            )
            raise PreferenceRecallForbiddenError(
                "memory configuration is not owned by the authenticated workspace"
            )

        trimmed_context, was_truncated = self._trim_context(request.context_messages)
        self._log_stage(
            stage=1, status="completed", request_id=request_id,
            workspace_id=workspace_id_text, end_user_id=end_user_id,
            duration_ms=(time.perf_counter() - started) * 1000,
            context_count=len(trimmed_context),
            context_chars=sum(len(item["content"]) for item in trimmed_context),
            context_truncated=was_truncated,
        )

        # 在同一超时预算内完成 step2 路由和 step3 精确检索。
        async def run_stages() -> list[RecalledPreference]:
            targets = await self._route_targets(
                request=request,
                context_messages=trimmed_context,
                telemetry=telemetry,
                request_id=request_id,
                workspace_id=workspace_id_text,
                end_user_id=end_user_id,
            )
            return await self._query_and_select(
                end_user_id=end_user_id,
                targets=targets,
                telemetry=telemetry,
                request_id=request_id,
                workspace_id=workspace_id_text,
            )

        try:
            preferences = await asyncio.wait_for(
                run_stages(), timeout=self.limits.timeout_ms / 1000
            )
        except asyncio.TimeoutError as exc:
            reason = "timeout"
            cause = exc
        except Exception as exc:
            reason = type(exc).__name__
            cause = exc
        else:
            return preferences

        self._log_stage(
            stage=telemetry.stage, status="failed", request_id=request_id,
            workspace_id=workspace_id_text, end_user_id=end_user_id,
            duration_ms=(time.perf_counter() - telemetry.stage_started) * 1000,
            target_count=telemetry.target_count, reason=reason,
        )
        if isinstance(cause, (PreferenceRecallValidationError, PreferenceRecallForbiddenError)):
            raise cause
        if reason == "timeout":
            raise TimeoutError("preference recall timed out") from cause
        raise RuntimeError("preference recall dependency failed") from cause


def validate_preference_recall_request(
    request_payload: Any,
) -> PreferenceRecallRequest:
    """Validate the shared MCP/HTTP business payload exactly once."""
    try:
        request = PreferenceRecallRequest.model_validate(request_payload)
    except ValidationError as exc:
        raise PreferenceRecallValidationError(
            "invalid preference recall request"
        ) from exc
    if len(request.current_user_message) > settings.MEMORY_MESSAGE_MAX_CONTENT_CHARS:
        raise PreferenceRecallValidationError("current_user_message is too long")
    return request


async def execute_preference_recall(
    *,
    config_id: uuid.UUID,
    workspace_id: uuid.UUID | str,
    end_user_id: str,
    request_payload: Any,
) -> list[RecalledPreference]:
    """Validate and run recall with the application-managed model transport."""
    started = time.perf_counter()
    request_id = get_trace_id() or uuid.uuid4().hex
    try:
        # Validate before touching model/storage dependencies so malformed requests
        # deterministically remain client errors even during an outage.
        request = (
            request_payload
            if isinstance(request_payload, PreferenceRecallRequest)
            else validate_preference_recall_request(request_payload)
        )
    except PreferenceRecallValidationError:
        PreferenceRecallService._log_stage(
            stage=1, status="failed", request_id=request_id,
            workspace_id=str(workspace_id), end_user_id=end_user_id,
            duration_ms=(time.perf_counter() - started) * 1000,
            reason="invalid_request",
        )
        raise

    service_started = False
    try:
        async with get_async_db_context() as db:
            memory_config = await MemoryConfigService(db).load_memory_config_async(config_id)
            llm_client = await ModelClientMixin.get_llm_client_async(
                db, memory_config.llm_model_id, memory_config.tenant_id
            )
        # The reader is request-scoped; the API lifespan still owns its ES client.
        storage = PreferenceRecallStorage(get_storage_service()._backend_factory)
        service = PreferenceRecallService(
            configured_workspace_id=memory_config.workspace_id,
            prompt_runner=PreferencePromptRunner(
                llm_client, load_preference_ontology("coding")
            ),
            storage=storage,
        )
        service_started = True
        return await service.recall(
            workspace_id=workspace_id,
            end_user_id=end_user_id,
            request=request,
        )
    except Exception as exc:
        if not service_started:
            PreferenceRecallService._log_stage(
                stage=1, status="failed", request_id=request_id,
                workspace_id=str(workspace_id), end_user_id=end_user_id,
                duration_ms=(time.perf_counter() - started) * 1000,
                reason=type(exc).__name__,
            )
        raise
