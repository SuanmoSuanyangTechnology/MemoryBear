"""Application service for Coding Agent preference recall."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from jinja2 import Template
from pydantic import ValidationError

from src.common.errors import BizCode, ProfileError
from src.common.observability.context import get_trace_id
from src.config import ProfileSettings
from src.core.adapters.read.config import build_config_port
from src.core.domain.models import ReadConfig
from src.infrastructure.model_service import ProfileChatModel
from src.middleware.auth import MemoryPrincipal
from src.repositories.end_user import EndUserRepository
from src.repositories.preference_recall import (
    FullPreferenceCandidate,
    PreferenceRecallCandidate,
    PreferenceRecallStorage,
)
from src.runtime import ProfileRuntime
from src.schemas.preference_recall import (
    FullPreferenceRecallResult,
    PreferenceRecallClassification,
    PreferenceRecallContextMessage,
    PreferenceRecallRequest,
    PreferenceRecallResult,
    PreferenceRecallTarget,
    RecalledPreference,
)

_ASSET_DIR = Path(__file__).with_name("assets")
_P3_TEMPLATE = Template(
    (_ASSET_DIR / "p3_route_preference_recall.jinja2").read_text(encoding="utf-8")
)
_FULL_TEMPLATE = Template(
    (_ASSET_DIR / "full_preference_prompt.jinja2").read_text(encoding="utf-8")
)
_MAX_ONTOLOGY_ATTEMPTS = 3


@dataclass(frozen=True)
class CodingOntology:
    subjects: tuple[str, ...]
    situation_keys: tuple[str, ...]
    subjects_json: str
    situation_keys_json: str


@cache
def _coding_ontology() -> CodingOntology:
    payload = json.loads(
        (_ASSET_DIR / "coding_preference_ontology.json").read_text(encoding="utf-8")
    )
    if payload.get("ontology_id") != "preference":
        raise ValueError("Invalid preference ontology_id")
    if payload.get("deterministic_fields", {}).get("domain") != "coding":
        raise ValueError("Invalid preference ontology domain")
    domain = payload["domains"]["coding"]
    subjects = tuple(item["key"] for item in domain["subjects"])
    situations = tuple(item["key"] for item in domain["situation_keys"])
    if not subjects or not situations:
        raise ValueError("Preference ontology has incomplete closed sets")
    return CodingOntology(
        subjects=subjects,
        situation_keys=situations,
        subjects_json=json.dumps(domain["subjects"], ensure_ascii=False),
        situation_keys_json=json.dumps(domain["situation_keys"], ensure_ascii=False),
    )


class PreferencePromptRunner:
    def __init__(self, llm_client: ProfileChatModel) -> None:
        self.llm_client = llm_client

    async def route_recall(
        self,
        *,
        current_user_message: str,
        context_messages: list[dict[str, str]],
    ) -> list[PreferenceRecallTarget]:
        """Classify a task into validated, closed-set preference buckets."""
        ontology = _coding_ontology()
        prompt = _P3_TEMPLATE.render(
            current_user_message_json=json.dumps(current_user_message, ensure_ascii=False),
            context_messages_json=json.dumps(context_messages, ensure_ascii=False),
            subjects_json=ontology.subjects_json,
            situation_keys_json=ontology.situation_keys_json,
        )

        best_valid: list[PreferenceRecallTarget] = []
        last_validation_error: Exception | None = None
        for _ in range(_MAX_ONTOLOGY_ATTEMPTS):
            # Provider/transport failures are already retried by the model client.
            # Only a successfully returned but invalid P3 payload is retried here.
            result = await self.llm_client.call_structured(
                prompt,
                PreferenceRecallClassification,
                strict=True,
            )
            try:
                parsed = (
                    result
                    if isinstance(result, PreferenceRecallClassification)
                    else PreferenceRecallClassification.model_validate(result)
                )
                seen: set[tuple[str, str]] = set()
                valid: list[PreferenceRecallTarget] = []
                has_invalid = False
                for target in parsed.targets:
                    key = (target.subject, target.situation_key)
                    if key in seen:
                        has_invalid = True
                        continue
                    seen.add(key)
                    if (
                        target.subject not in ontology.subjects
                        or target.situation_key not in ontology.situation_keys
                    ):
                        has_invalid = True
                        continue
                    valid.append(target)

                if not has_invalid:
                    return valid
                if len(valid) > len(best_valid):
                    best_valid = valid
                last_validation_error = ValueError(
                    "P3 output contains duplicate or out-of-ontology targets"
                )
            except (ValidationError, ValueError, TypeError) as exc:
                last_validation_error = exc

        if best_valid:
            return best_valid
        raise ValueError("P3 output is invalid") from last_validation_error


logger = logging.getLogger(__name__)


def masked_identifier(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


class PreferenceRecallValidationError(Exception):
    pass


@dataclass(frozen=True)
class PreferenceRecallLimits:
    max_context_messages: int
    max_context_chars: int
    max_preferences: int
    timeout_ms: int

    @classmethod
    def from_settings(cls, settings: ProfileSettings) -> PreferenceRecallLimits:
        return cls(
            max_context_messages=max(0, settings.preference_recall_max_context_messages),
            max_context_chars=max(settings.preference_recall_max_context_messages, 3) * 12000,
            max_preferences=max(1, settings.preference_recall_max_preferences),
            timeout_ms=max(100, settings.preference_recall_timeout_ms),
        )


@dataclass
class _RecallTelemetry:
    target_count: int = 0
    stage: int = 1
    stage_started: float = 0.0


class _TargetedRecall:
    """Route, query, merge, deduplicate and limit recall results."""

    def __init__(
        self,
        *,
        prompt_runner: PreferencePromptRunner,
        storage: PreferenceRecallStorage,
        limits: PreferenceRecallLimits,
    ):
        self.prompt_runner = prompt_runner
        self.storage = storage
        self.limits = limits

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
            stage,
            stage,
            status,
            request_id,
            masked_identifier(workspace_id),
            masked_identifier(end_user_id),
            context_count,
            context_chars,
            context_truncated,
            target_count,
            target_details,
            candidate_count,
            result_count,
            preference_truncated,
            reason,
            duration_ms,
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
            stage=2,
            status="completed",
            request_id=request_id,
            workspace_id=workspace_id,
            end_user_id=end_user_id,
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
                stage=3,
                status="skipped",
                request_id=request_id,
                workspace_id=workspace_id,
                end_user_id=end_user_id,
                duration_ms=0,
                reason="no_targets",
            )
            return []

        candidates = await self.storage.query_buckets(
            end_user_id=end_user_id,
            targets=targets,
        )
        selected, truncated = self._select_preferences(candidates)
        self._log_stage(
            stage=3,
            status="completed",
            request_id=request_id,
            workspace_id=workspace_id,
            end_user_id=end_user_id,
            duration_ms=(time.perf_counter() - telemetry.stage_started) * 1000,
            target_count=telemetry.target_count,
            candidate_count=len(candidates),
            result_count=len(selected),
            preference_truncated=truncated,
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

        trimmed_context, was_truncated = self._trim_context(request.context_messages)
        self._log_stage(
            stage=1,
            status="completed",
            request_id=request_id,
            workspace_id=workspace_id_text,
            end_user_id=end_user_id,
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
        except TimeoutError as exc:
            reason = "timeout"
            cause = exc
        except Exception as exc:
            reason = type(exc).__name__
            cause = exc
        else:
            return preferences

        self._log_stage(
            stage=telemetry.stage,
            status="failed",
            request_id=request_id,
            workspace_id=workspace_id_text,
            end_user_id=end_user_id,
            duration_ms=(time.perf_counter() - telemetry.stage_started) * 1000,
            target_count=telemetry.target_count,
            reason=reason,
        )
        if reason == "timeout":
            raise TimeoutError("preference recall timed out") from cause
        raise RuntimeError("preference recall dependency failed") from cause


def validate_preference_recall_request(
    request_payload: Any,
    settings: ProfileSettings,
) -> PreferenceRecallRequest:
    """Validate the shared MCP/HTTP business payload exactly once."""
    try:
        request = PreferenceRecallRequest.model_validate(request_payload)
    except ValidationError as exc:
        raise PreferenceRecallValidationError("invalid preference recall request") from exc
    if len(request.current_user_message) > max(1, settings.memory_message_max_content_chars):
        raise PreferenceRecallValidationError("current_user_message is too long")
    return request


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
        lines = [_TAG.sub("&lt;", item.preference_text) for item in preferences]
        prompt = _FULL_TEMPLATE.render(lines=lines)
    return FullPreferenceRecallResult(
        preferences=preferences, count=len(preferences), preference_prompt=prompt
    )


class PreferenceRecallService:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.runtime = runtime
        self.end_users = EndUserRepository(runtime.postgres)
        self.configs = build_config_port(runtime)
        self.storage = PreferenceRecallStorage(runtime.elasticsearch)

    async def _resolve(self, principal: MemoryPrincipal, other_id: str) -> tuple[str, ReadConfig]:
        end_user_id = await self.end_users.resolve_id_by_other_id(principal.workspace_id, other_id)
        if end_user_id is None:
            raise ProfileError(BizCode.PERMISSION_DENIED, "无权访问该偏好资源")

        config_id = await self.configs.active_config_id(principal.workspace_id)
        if config_id is None:
            raise RuntimeError("workspace has no active memory configuration")
        config = await self.configs.load_config(config_id)
        if config is None:
            raise RuntimeError("workspace memory configuration not found")
        if config.workspace_id != principal.workspace_id or config.tenant_id != principal.tenant_id:
            raise ProfileError(BizCode.PERMISSION_DENIED, "无权访问该偏好资源")
        return str(end_user_id), config

    async def recall(
        self, principal: MemoryPrincipal, other_id: str, request: PreferenceRecallRequest
    ) -> PreferenceRecallResult:
        end_user_id, config = await self._resolve(principal, other_id)
        if config.llm_model_id is None:
            raise RuntimeError("workspace LLM configuration not found")
        model = await self.runtime.model_service.chat_model(config.llm_model_id, config.tenant_id)
        service = _TargetedRecall(
            prompt_runner=PreferencePromptRunner(model),
            storage=self.storage,
            limits=PreferenceRecallLimits.from_settings(self.runtime.settings),
        )
        preferences = await service.recall(
            workspace_id=principal.workspace_id, end_user_id=end_user_id, request=request
        )
        return PreferenceRecallResult(preferences=preferences, count=len(preferences))

    async def recall_full(
        self, principal: MemoryPrincipal, other_id: str
    ) -> FullPreferenceRecallResult:
        end_user_id, _ = await self._resolve(principal, other_id)
        candidates, pages = await self.storage.query_all(end_user_id=end_user_id)
        result = assemble_full_preferences(candidates)
        logger.info(
            "full_preference_recall trace_id=%s workspace_id=%s end_user_id=%s "
            "pages=%d candidates=%d results=%d",
            get_trace_id(),
            masked_identifier(str(principal.workspace_id)),
            masked_identifier(end_user_id),
            pages,
            len(candidates),
            result.count,
        )
        return result
