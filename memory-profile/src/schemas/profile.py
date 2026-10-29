"""Profile response value objects and their row parsing."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from src.common.normalization import collapse_whitespace, datetime_to_epoch_ms


def parse_findings(value: object) -> tuple[list[str], list[dict[str, str]]]:
    """解析 ``key_findings`` 列（api ``_parse_findings`` 同款三分支）。

    兼容三种存储形态：JSON 数组、纯字符串列表、以及带 ``finding`` /
    ``recommended_action`` 的字典列表；其余一律空。
    """

    if value in (None, ""):
        return [], []
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return [item.strip() for item in value.split("•") if item.strip()], []
    if not isinstance(parsed, list):
        return [], []
    if all(isinstance(item, str) for item in parsed):
        return [normalized for item in parsed if (normalized := collapse_whitespace(item))], []
    if not all(isinstance(item, dict) for item in parsed):
        return [], []
    findings: list[str] = []
    actionable: list[dict[str, str]] = []
    for item in parsed:
        finding_value = item.get("finding")
        action_value = item.get("recommended_action")
        if not isinstance(finding_value, str) or not isinstance(action_value, str):
            continue
        finding, action = collapse_whitespace(finding_value), collapse_whitespace(action_value)
        if finding and action:
            findings.append(finding)
            actionable.append({"finding": finding, "recommended_action": action})
    return findings, actionable


def normalize_tags(value: object) -> list[str]:
    """规范化 ``memory_tags``：去空、去重、截断 5 个、单条最长 12 字符。"""

    if not isinstance(value, list):
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        normalized = collapse_whitespace(item)
        key = normalized.casefold()
        if not normalized or len(normalized) > 12 or key in seen:
            continue
        seen.add(key)
        tags.append(normalized)
        if len(tags) == 5:
            break
    return tags


class MemoryInsight(BaseModel):
    """记忆洞察的响应载体；字段名与最终响应 key 一一对应。

    ``key_findings`` 在空态为 ``None``（用户不存在/非法），在用户存在但无
    内容时为 ``[]``——与 api 的响应口径一致，不能合并。
    """

    memory_insight: str | None
    behavior_pattern: str | None
    key_findings: list[str] | None
    actionable_findings: list[dict[str, str]]
    growth_trajectory: str | None
    updated_at: int | None
    is_cached: bool
    message_key: str | None = None

    @classmethod
    def empty(cls, message_key: str) -> MemoryInsight:
        return cls(
            memory_insight=None,
            behavior_pattern=None,
            key_findings=None,
            actionable_findings=[],
            growth_trajectory=None,
            updated_at=None,
            is_cached=False,
            message_key=message_key,
        )

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> MemoryInsight:
        findings, actionable = parse_findings(row["key_findings"])
        cached = any(
            row[key]
            for key in (
                "memory_insight",
                "behavior_pattern",
                "key_findings",
                "growth_trajectory",
            )
        )
        return cls(
            memory_insight=row["memory_insight"],
            behavior_pattern=row["behavior_pattern"],
            key_findings=findings,
            actionable_findings=actionable,
            growth_trajectory=row["growth_trajectory"],
            updated_at=datetime_to_epoch_ms(row["memory_insight_updated_at"]),
            is_cached=cached,
        )


class UserSummary(BaseModel):
    """用户摘要的响应载体；字段名与最终响应 key 一一对应。"""

    user_summary: str | None
    personality: str | None
    core_values: str | None
    one_sentence: str | None
    tags: list[str]
    updated_at: int | None
    is_cached: bool
    message_key: str | None = None

    @classmethod
    def empty(cls, message_key: str) -> UserSummary:
        return cls(
            user_summary=None,
            personality=None,
            core_values=None,
            one_sentence=None,
            tags=[],
            updated_at=None,
            is_cached=False,
            message_key=message_key,
        )

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> UserSummary:
        cached = any(
            row[key]
            for key in (
                "user_summary",
                "personality_traits",
                "core_values",
                "one_sentence_summary",
            )
        )
        return cls(
            user_summary=row["user_summary"],
            personality=row["personality_traits"],
            core_values=row["core_values"],
            one_sentence=row["one_sentence_summary"],
            tags=normalize_tags(row["memory_tags"]),
            updated_at=datetime_to_epoch_ms(row["user_summary_updated_at"]),
            is_cached=cached,
        )
