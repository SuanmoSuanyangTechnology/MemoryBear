from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol, cast

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging_config import get_logger
from app.core.memory.pipelines.base_pipeline import ModelClientMixin
from app.core.memory.utils.prompt.template_render import prompt_env
from app.core.utils.datetime_utils import as_utc_aware, utcnow_naive
from app.db import get_async_db_context
from app.repositories.end_user_repository import (
    EndUserRepository,
    MemoryInsightSourceRow,
)
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.repositories.neo4j.statement_repository import StatementRepository
from app.repositories.workspace_repository import WorkspaceRepository
from app.services.memory_config_service import MemoryConfigService

logger = get_logger(__name__)

MEMORY_INSIGHT_META_FIELDS = (
    "core_facts",
    "traits",
    "relations",
    "goals",
    "interests",
    "beliefs_or_stances",
    "anchors",
    "events",
)
MEMORY_INSIGHT_META_FIELD_LIMIT = 5
MEMORY_INSIGHT_STATEMENT_LIMIT = 12
MEMORY_INSIGHT_INPUT_TEXT_LIMIT = 200
MEMORY_INSIGHT_MIN_ENTRIES = 2
MEMORY_INSIGHT_LLM_TIMEOUT_SECONDS = 30
INSUFFICIENT_MEMORY_INSIGHT_TEXT = "该用户记忆不足，暂无法生成有效洞察。"
INSUFFICIENT_MEMORY_INSIGHT_TEXT_EN = (
    "There is insufficient user memory to generate a meaningful insight."
)

RefreshDecision = Literal["dispatch", "skip_no_change", "skip_fresh"]
WorkspaceValidationReason = Literal[
    "valid",
    "invalid_workspace_id",
    "workspace_not_found",
    "workspace_inactive",
    "unsupported_storage_type",
]


class StructuredLLMClient(Protocol):
    async def call_structured(
        self,
        input_data: object,
        schema: type[BaseModel],
    ) -> object: ...


class MemoryInsightFinding(BaseModel):
    finding: str = Field(min_length=1, max_length=160)
    recommended_action: str = Field(min_length=1, max_length=120)


class MemoryInsightOutput(BaseModel):
    overview: str = Field(min_length=1, max_length=240)
    key_findings: list[MemoryInsightFinding] = Field(default_factory=list, max_length=3)


@dataclass(frozen=True)
class MemoryInsightWorkspaceValidation:
    valid: bool
    reason: WorkspaceValidationReason
    storage_type: str | None = None


@dataclass(frozen=True)
class MemoryCacheRefreshDecisions:
    insight: RefreshDecision
    summary: RefreshDecision


def _normalize_text(value: str, max_length: int | None = None) -> str:
    normalized = " ".join(value.split())
    return normalized[:max_length] if max_length is not None else normalized


def build_memory_insight_metadata_input(meta_data: object) -> dict[str, list[str]]:
    """提取允许参与洞察生成的画像字段，并执行输入上限。"""
    if not isinstance(meta_data, dict):
        return {field: [] for field in MEMORY_INSIGHT_META_FIELDS}
    metadata = cast(dict[str, object], meta_data)

    result: dict[str, list[str]] = {}
    for field in MEMORY_INSIGHT_META_FIELDS:
        raw_values = metadata.get(field)
        if not isinstance(raw_values, list):
            result[field] = []
            continue
        values = cast(list[object], raw_values)

        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str):
                continue
            normalized = _normalize_text(value, MEMORY_INSIGHT_INPUT_TEXT_LIMIT)
            key = normalized.casefold()
            if not normalized or key in seen:
                continue
            seen.add(key)
            cleaned.append(normalized)
            if len(cleaned) >= MEMORY_INSIGHT_META_FIELD_LIMIT:
                break
        result[field] = cleaned
    return result


def count_memory_insight_entries(metadata_input: dict[str, list[str]]) -> int:
    return sum(len(values) for values in metadata_input.values())


def build_memory_insight_statement_input(
    statements: Sequence[object],
    metadata_input: dict[str, list[str]],
) -> list[str]:
    """清理 Statement，并排除与画像输入重复的文本。"""
    seen = {value.casefold() for values in metadata_input.values() for value in values}
    result: list[str] = []
    for value in statements:
        if not isinstance(value, str):
            continue
        normalized = _normalize_text(value, MEMORY_INSIGHT_INPUT_TEXT_LIMIT)
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
        if len(result) >= MEMORY_INSIGHT_STATEMENT_LIMIT:
            break
    return result


def render_memory_insight_grounded_prompt(
    metadata_input: dict[str, list[str]],
    fallback_statements: list[str],
    language: str = "zh",
) -> str:
    payload = {
        "profile_metadata": {
            field: values for field, values in metadata_input.items() if values
        },
        "fallback_statements": fallback_statements,
    }
    return prompt_env.get_template("memory_insight_grounded.jinja2").render(
        input_json=json.dumps(payload, ensure_ascii=False),
        language=language,
    )


def _normalize_memory_insight_output(
    output: MemoryInsightOutput,
) -> MemoryInsightOutput:
    overview = _normalize_text(output.overview)
    findings: list[MemoryInsightFinding] = []
    seen: set[str] = set()
    for item in output.key_findings:
        finding = _normalize_text(item.finding)
        action = _normalize_text(item.recommended_action)
        key = finding.casefold()
        if not finding or not action or key in seen:
            continue
        seen.add(key)
        findings.append(
            MemoryInsightFinding(finding=finding, recommended_action=action)
        )
        if len(findings) >= 3:
            break
    return MemoryInsightOutput(overview=overview, key_findings=findings)


async def generate_memory_insight_output(
    llm_client: StructuredLLMClient,
    metadata_input: dict[str, list[str]],
    fallback_statements: list[str],
    language: str = "zh",
) -> MemoryInsightOutput:
    prompt = render_memory_insight_grounded_prompt(
        metadata_input,
        fallback_statements,
        language,
    )
    raw_output = await asyncio.wait_for(
        llm_client.call_structured(
            [{"role": "user", "content": prompt}],
            MemoryInsightOutput,
        ),
        timeout=MEMORY_INSIGHT_LLM_TIMEOUT_SECONDS,
    )
    output = (
        raw_output
        if isinstance(raw_output, MemoryInsightOutput)
        else MemoryInsightOutput.model_validate(raw_output)
    )
    return _normalize_memory_insight_output(output)


def parse_stored_memory_insight_findings(
    stored_value: object,
) -> tuple[list[str], list[dict[str, str]]]:
    """把新旧 key_findings 存储格式投影为兼容字段。"""
    if stored_value in (None, ""):
        return [], []

    parsed: object
    if isinstance(stored_value, str):
        try:
            parsed = cast(object, json.loads(stored_value))
        except (json.JSONDecodeError, TypeError):
            findings = [
                item.strip() for item in stored_value.split("•") if item.strip()
            ]
            logger.warning(
                "Memory Insight key_findings 不是合法 JSON，使用历史文本兼容解析"
            )
            return findings, []
    else:
        parsed = stored_value

    if not isinstance(parsed, list):
        return [], []
    items = cast(list[object], parsed)
    if all(isinstance(item, str) for item in items):
        return [
            normalized
            for item in items
            if (normalized := _normalize_text(cast(str, item)))
        ], []
    if not all(isinstance(item, dict) for item in items):
        return [], []

    findings: list[str] = []
    actionable: list[dict[str, str]] = []
    for raw_item in items:
        item = cast(dict[str, object], raw_item)
        finding_value = item.get("finding")
        action_value = item.get("recommended_action")
        if not isinstance(finding_value, str) or not isinstance(action_value, str):
            continue
        finding = _normalize_text(finding_value)
        action = _normalize_text(action_value)
        if not finding or not action:
            continue
        findings.append(finding)
        actionable.append({"finding": finding, "recommended_action": action})
    return findings, actionable


def _classify_single_cache_refresh(
    cache_at: datetime | None,
    source_at: datetime | None,
    now: datetime,
    fresh_window: timedelta,
) -> RefreshDecision:
    if cache_at is None:
        return "dispatch"
    if source_at is None:
        return "skip_no_change"

    cache_time = as_utc_aware(cache_at)
    source_time = as_utc_aware(source_at)
    now_time = as_utc_aware(now)
    if cache_time is None or source_time is None or now_time is None:
        return "skip_no_change"
    if cache_time > source_time:
        return "skip_no_change"
    if now_time - cache_time < fresh_window:
        return "skip_fresh"
    return "dispatch"


def _latest_source_time(*values: datetime | None) -> datetime | None:
    latest_value: datetime | None = None
    latest_aware: datetime | None = None
    for value in values:
        aware_value = as_utc_aware(value)
        if aware_value is None:
            continue
        if latest_aware is None or aware_value > latest_aware:
            latest_value = value
            latest_aware = aware_value
    return latest_value


def classify_memory_cache_refresh(
    insight_at: datetime | None,
    summary_at: datetime | None,
    write_at: datetime | None,
    metadata_updated_at: datetime | None,
    *,
    now: datetime | None = None,
    fresh_hours: int | None = None,
) -> MemoryCacheRefreshDecisions:
    """分别计算 Insight 和 Summary 的刷新状态。"""
    insight_source_at = _latest_source_time(write_at, metadata_updated_at)
    current_time = now or utcnow_naive()
    fresh_window = timedelta(
        hours=settings.MEMORY_CACHE_FRESH_HOURS if fresh_hours is None else fresh_hours
    )

    return MemoryCacheRefreshDecisions(
        insight=_classify_single_cache_refresh(
            insight_at, insight_source_at, current_time, fresh_window
        ),
        summary=_classify_single_cache_refresh(
            summary_at, write_at, current_time, fresh_window
        ),
    )


async def validate_neo4j_memory_insight_workspace(
    workspace_id: uuid.UUID | str,
) -> MemoryInsightWorkspaceValidation:
    try:
        workspace_uuid = (
            workspace_id
            if isinstance(workspace_id, uuid.UUID)
            else uuid.UUID(workspace_id)
        )
    except (TypeError, ValueError, AttributeError):
        return MemoryInsightWorkspaceValidation(False, "invalid_workspace_id")

    async with get_async_db_context() as db:
        workspace = await WorkspaceRepository(db).get_workspace_by_id_async(workspace_uuid)
        if workspace is None:
            return MemoryInsightWorkspaceValidation(False, "workspace_not_found")
        is_active = cast(bool, cast(object, workspace.is_active))
        raw_storage_type = cast(str | None, cast(object, workspace.storage_type))

    if not is_active:
        return MemoryInsightWorkspaceValidation(
            False,
            "workspace_inactive",
            raw_storage_type,
        )

    storage_type = (raw_storage_type or "").lower()
    if storage_type != "neo4j":
        return MemoryInsightWorkspaceValidation(
            False,
            "unsupported_storage_type",
            storage_type or None,
        )
    return MemoryInsightWorkspaceValidation(True, "valid", storage_type)


async def _create_memory_insight_llm_client(
    end_user_id: uuid.UUID,
) -> StructuredLLMClient:
    async with get_async_db_context() as db:
        config_service = MemoryConfigService(db)
        config_id = await config_service.get_config_id_by_end_user_async(end_user_id)
        memory_config = await config_service.load_memory_config_async(config_id)
        llm_client = await ModelClientMixin.get_llm_client_async(
            db,
            memory_config.llm_model_id,
            memory_config.tenant_id,
        )
    return cast(
        StructuredLLMClient,
        cast(
            object,
            llm_client,
        ),
    )


def _memory_insight_result(
    *,
    success: bool,
    output: MemoryInsightOutput | None = None,
    status: str,
    error: str | None = None,
) -> dict[str, object]:
    actionable = [item.model_dump() for item in output.key_findings] if output else []
    return {
        "success": success,
        "status": status,
        "memory_insight": output.overview if output else None,
        "behavior_pattern": "" if output else None,
        "key_findings": [item["finding"] for item in actionable] if output else None,
        "actionable_findings": actionable,
        "growth_trajectory": "" if output else None,
        "error": error,
    }


async def refresh_memory_insight(
    end_user_id: str,
    workspace_id: uuid.UUID | str,
    language: str = "zh",
) -> dict[str, object]:
    """使用短 PG 会话生成并条件写回单个用户的 Memory Insight。"""
    started_at = time.monotonic()
    try:
        end_user_uuid = uuid.UUID(end_user_id)
        workspace_uuid = (
            workspace_id
            if isinstance(workspace_id, uuid.UUID)
            else uuid.UUID(workspace_id)
        )
    except (TypeError, ValueError, AttributeError):
        return _memory_insight_result(
            success=False,
            status="invalid_parameter",
            error="无效的用户或工作空间ID格式",
        )

    workspace_validation = await validate_neo4j_memory_insight_workspace(workspace_uuid)
    if not workspace_validation.valid:
        return _memory_insight_result(
            success=False,
            status=workspace_validation.reason,
            error=f"工作空间不可用于 Neo4j Memory Insight: {workspace_validation.reason}",
        )

    async with get_async_db_context() as db:
        source: MemoryInsightSourceRow | None = await EndUserRepository(
            db
        ).get_scoped_memory_insight_source_async(
            workspace_id=workspace_uuid,
            end_user_id=end_user_uuid,
        )
    if source is None:
        return _memory_insight_result(
            success=False,
            status="source_not_found",
            error="用户不存在、已停用或不属于当前工作空间",
        )

    metadata_input = build_memory_insight_metadata_input(source["meta_data"])
    fallback_statements: list[str] = []
    if count_memory_insight_entries(metadata_input) < MEMORY_INSIGHT_MIN_ENTRIES:
        async with Neo4jConnector() as connector:
            statement_repo = StatementRepository(connector)
            raw_statements = await statement_repo.find_recent_valid_user_statements(
                end_user_id=end_user_id,
                statement_types=["FACT", "OPINION"],
                limit=MEMORY_INSIGHT_STATEMENT_LIMIT,
            )
        fallback_statements = build_memory_insight_statement_input(
            raw_statements, metadata_input
        )

    total_entries = count_memory_insight_entries(metadata_input) + len(
        fallback_statements
    )
    if total_entries < MEMORY_INSIGHT_MIN_ENTRIES:
        output = MemoryInsightOutput(
            overview=(
                INSUFFICIENT_MEMORY_INSIGHT_TEXT_EN
                if language == "en"
                else INSUFFICIENT_MEMORY_INSIGHT_TEXT
            ),
            key_findings=[],
        )
    else:
        llm_client = await _create_memory_insight_llm_client(end_user_uuid)
        output = await generate_memory_insight_output(
            llm_client,
            metadata_input,
            fallback_statements,
            language,
        )

    actionable = [item.model_dump() for item in output.key_findings]
    refreshed_at = utcnow_naive()
    async with get_async_db_context() as db:
        updated = await EndUserRepository(
            db
        ).update_grounded_memory_insight_if_source_unchanged_async(
            workspace_id=workspace_uuid,
            end_user_id=end_user_uuid,
            expected_write_time=source["write_time"],
            expected_metadata_row_exists=source["metadata_row_exists"],
            expected_metadata_updated_at=source["metadata_updated_at"],
            memory_insight=output.overview,
            key_findings=json.dumps(actionable, ensure_ascii=False),
            refreshed_at=refreshed_at,
        )

    status = "refreshed" if updated else "superseded"
    logger.info(
        "Memory Insight 刷新完成 user=%s workspace=%s status=%s input_count=%s elapsed=%.3fs",
        end_user_id,
        workspace_id,
        status,
        total_entries,
        time.monotonic() - started_at,
    )
    if not updated:
        return _memory_insight_result(
            success=False,
            output=output,
            status=status,
            error="源数据已更新，本轮洞察未写入",
        )
    return _memory_insight_result(success=True, output=output, status=status)
