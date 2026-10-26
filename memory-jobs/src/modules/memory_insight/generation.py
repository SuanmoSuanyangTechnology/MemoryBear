"""Memory-insight generation, ported to blocking clients.

A faithful port of ``memory-profile/src/modules/analytics/generation/memory_insight.py``:
the profile metadata is bounded and deduplicated, recent statements fill the gap
when metadata is thin, one structured LLM call produces the insight, and the write
is guarded by the same optimistic concurrency conditions the monolith used.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel, Field

from src.common.normalization import compact_text
from src.infrastructure.model_service import SyncChatModel
from src.modules.memory_insight.metadata import (
    flatten_domain_specific_info,
    split_metadata_entry,
)
from src.modules.shared.repositories.generation_graph import GenerationGraphRepository
from src.modules.shared.repositories.memory_generation import MemoryGenerationRepository
from src.prompts import render_memory_insight_prompt

logger = logging.getLogger(__name__)

MEMORY_INSIGHT_META_FIELDS = (
    "identity_background",
    "relationships",
    "domain_specific_info",
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
MEMORY_INSIGHT_META_TOTAL_CHAR_LIMIT = 6000
MEMORY_INSIGHT_STATEMENT_LIMIT = 12
MEMORY_INSIGHT_INPUT_TEXT_LIMIT = 200
MEMORY_INSIGHT_MIN_ENTRIES = 2
INSUFFICIENT_MEMORY_INSIGHT_TEXT = "该用户记忆不足，暂无法生成有效洞察。"
INSUFFICIENT_MEMORY_INSIGHT_TEXT_EN = (
    "There is insufficient user memory to generate a meaningful insight."
)


class MemoryInsightFinding(BaseModel):
    finding: str = Field(min_length=1, max_length=160)
    recommended_action: str = Field(min_length=1, max_length=120)


class MemoryInsightOutput(BaseModel):
    overview: str = Field(min_length=1, max_length=240)
    key_findings: list[MemoryInsightFinding] = Field(default_factory=list, max_length=3)


def _metadata_groups(metadata: dict[str, object]) -> list[tuple[str, list[str]]]:
    groups: dict[str, list[str]] = {}
    for field in MEMORY_INSIGHT_META_FIELDS:
        raw_values = metadata.get(field)
        if field in ("identity_background", "relationships"):
            if not isinstance(raw_values, list):
                continue
            for value in cast(list[object], raw_values):
                triplet = split_metadata_entry(value)
                if not triplet:
                    continue
                l2, l3, content = triplet
                text = f"{l3} | {content}" if l3 else content
                groups.setdefault(f"{field}.{l2}", []).append(text)
        elif field == "domain_specific_info":
            for scene_key, field_key, content in flatten_domain_specific_info(raw_values):
                groups.setdefault(f"{field}.{scene_key}.{field_key}", []).append(content)
        elif isinstance(raw_values, list):
            groups[field] = [v for v in cast(list[object], raw_values) if isinstance(v, str)]
    return list(groups.items())


def build_memory_insight_metadata_input(meta_data: object) -> dict[str, list[str]]:
    """Bounded metadata projection (api limits, same order and truncation)."""

    if not isinstance(meta_data, dict):
        return {}
    metadata = cast(dict[str, object], meta_data)

    result: dict[str, list[str]] = {}
    total_chars = 0
    for group_key, values in _metadata_groups(metadata):
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = compact_text(value, MEMORY_INSIGHT_INPUT_TEXT_LIMIT)
            key = normalized.casefold()
            if not normalized or key in seen:
                continue
            if total_chars + len(normalized) > MEMORY_INSIGHT_META_TOTAL_CHAR_LIMIT:
                if cleaned:
                    result[group_key] = cleaned
                return result
            seen.add(key)
            cleaned.append(normalized)
            total_chars += len(normalized)
            if len(cleaned) >= MEMORY_INSIGHT_META_FIELD_LIMIT:
                break
        if cleaned:
            result[group_key] = cleaned
    return result


def count_memory_insight_entries(metadata_input: dict[str, list[str]]) -> int:
    return sum(len(values) for values in metadata_input.values())


def build_memory_insight_statement_input(
    statements: Sequence[object],
    metadata_input: dict[str, list[str]],
) -> list[str]:
    """Clean statements and drop text already present in the profile input."""

    seen = {value.casefold() for values in metadata_input.values() for value in values}
    result: list[str] = []
    for value in statements:
        if not isinstance(value, str):
            continue
        normalized = compact_text(value, MEMORY_INSIGHT_INPUT_TEXT_LIMIT)
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
        if len(result) >= MEMORY_INSIGHT_STATEMENT_LIMIT:
            break
    return result


def _normalize_output(output: MemoryInsightOutput) -> MemoryInsightOutput:
    overview = compact_text(output.overview)
    findings: list[MemoryInsightFinding] = []
    seen: set[str] = set()
    for item in output.key_findings:
        finding = compact_text(item.finding)
        action = compact_text(item.recommended_action)
        key = finding.casefold()
        if not finding or not action or key in seen:
            continue
        seen.add(key)
        findings.append(MemoryInsightFinding(finding=finding, recommended_action=action))
        if len(findings) >= 3:
            break
    return MemoryInsightOutput(overview=overview, key_findings=findings)


def generate_memory_insight_output(
    llm_client: SyncChatModel,
    metadata_input: dict[str, list[str]],
    fallback_statements: list[str],
    language: str = "zh",
) -> MemoryInsightOutput:
    prompt = render_memory_insight_prompt(metadata_input, fallback_statements, language)
    raw_output = llm_client.call_structured(
        [{"role": "user", "content": prompt}],
        MemoryInsightOutput,
    )
    output = (
        raw_output
        if isinstance(raw_output, MemoryInsightOutput)
        else MemoryInsightOutput.model_validate(raw_output)
    )
    return _normalize_output(output)


def _result(
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


def validate_workspace_row(
    workspace: Mapping[str, Any] | None,
) -> tuple[bool, str, str | None]:
    """Return ``(valid, reason, storage_type)`` for an already-loaded workspace."""

    if workspace is None:
        return False, "workspace_not_found", None
    raw_storage_type = workspace["storage_type"]
    if not workspace["is_active"]:
        return False, "workspace_inactive", raw_storage_type
    storage_type = "neo4j" if raw_storage_type is None else str(raw_storage_type).lower()
    if storage_type != "neo4j":
        return False, "unsupported_storage_type", storage_type or None
    return True, "valid", storage_type


def refresh_memory_insight(
    repository: MemoryGenerationRepository,
    graph: GenerationGraphRepository,
    llm_provider: Callable[[], SyncChatModel],
    end_user_id: str,
    workspace_id: UUID,
    language: str = "zh",
    *,
    workspace_row: Mapping[str, Any] | None,
) -> dict[str, object]:
    """Generate and conditionally persist one user's insight cache."""

    started_at = time.monotonic()
    try:
        end_user_uuid = UUID(end_user_id)
    except (TypeError, ValueError, AttributeError):
        return _result(
            success=False,
            status="invalid_parameter",
            error="无效的用户或工作空间ID格式",
        )

    valid, reason, _ = validate_workspace_row(workspace_row)
    if not valid:
        return _result(
            success=False,
            status=reason,
            error=f"工作空间不可用于 Neo4j Memory Insight: {reason}",
        )

    source = repository.insight_source(end_user_uuid, workspace_id)
    if source is None:
        return _result(
            success=False,
            status="source_not_found",
            error="用户不存在、已停用或不属于当前工作空间",
        )

    metadata_input = build_memory_insight_metadata_input(source["meta_data"])
    fallback_statements: list[str] = []
    if count_memory_insight_entries(metadata_input) < MEMORY_INSIGHT_MIN_ENTRIES:
        raw_statements = graph.recent_user_statements(
            end_user_id,
            ["FACT", "OPINION"],
            MEMORY_INSIGHT_STATEMENT_LIMIT,
        )
        fallback_statements = build_memory_insight_statement_input(
            raw_statements, metadata_input
        )

    total_entries = count_memory_insight_entries(metadata_input) + len(fallback_statements)
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
        output = generate_memory_insight_output(
            llm_provider(), metadata_input, fallback_statements, language
        )

    actionable = [item.model_dump() for item in output.key_findings]
    refreshed_at = datetime.now(UTC).replace(tzinfo=None)
    updated = repository.update_insight_if_source_unchanged(
        workspace_id,
        end_user_uuid,
        expected_write_time=source["write_time"],
        expected_metadata_row_exists=source["metadata_row_exists"],
        expected_metadata_updated_at=source["metadata_updated_at"],
        memory_insight=output.overview,
        key_findings=json.dumps(actionable, ensure_ascii=False),
        refreshed_at=refreshed_at,
    )

    status = "refreshed" if updated else "superseded"
    logger.info(
        "memory insight refresh user=%s workspace=%s status=%s input_count=%s elapsed=%.3fs",
        end_user_id,
        workspace_id,
        status,
        total_entries,
        time.monotonic() - started_at,
    )
    if not updated:
        return _result(
            success=False,
            output=output,
            status=status,
            error="源数据已更新，本轮洞察未写入",
        )
    return _result(success=True, output=output, status=status)
