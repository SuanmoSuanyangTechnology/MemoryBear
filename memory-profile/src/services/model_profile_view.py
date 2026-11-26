"""Runtime profile read helpers migrated from the API host."""
from __future__ import annotations

import logging
from typing import Any

from redbear_model import CompositeMember, ModelProfile
from redbear_model.contracts import ModelType as HostModelType

LEGACY_CHAT_TYPE = "chat"
logger = logging.getLogger(__name__)


def parse_members(config: dict | None) -> list[tuple[str, str]]:
    """组合 config JSON `members[]` → [(provider, model_name)]，脏项跳过告警，去重保序。"""
    raw = (config or {}).get("members")
    if not isinstance(raw, list):
        return []
    parsed: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            logger.warning("composite member entry is not an object: %r", item)
            continue
        provider = item.get("provider")
        model_name = item.get("model_name")
        if not isinstance(provider, str) or not provider or not isinstance(model_name, str) or not model_name:
            logger.warning("composite member entry missing provider/model_name: %r", item)
            continue
        pair = (provider, model_name)
        if pair in seen:
            continue
        seen.add(pair)
        parsed.append(pair)
    return parsed


def normalize_type(value) -> str | None:
    """类型值归一：`'chat' → 'llm'`（大小写不敏感，兼容 DB/YAML 存量字符串），结果统一小写。"""
    if value is None:
        return None
    raw = str(getattr(value, "value", value))
    normalized = raw.lower()
    if normalized == LEGACY_CHAT_TYPE:
        return HostModelType.LLM.value
    return normalized


def profile_of(row: Any) -> ModelProfile:
    """ORM 行 / 替身 → profile（ModelConfig 与 ModelBase 通用，getattr 容错）。

    ModelConfig 组合行装配 members（provider=composite 才解析 config.members[]）；
    ModelBase 无 tenant_id/config，按缺省处理。
    """
    config = getattr(row, "config", None)
    members: tuple[CompositeMember, ...] = ()
    if isinstance(config, dict) and getattr(row, "provider", None) == "composite":
        members = tuple(
            CompositeMember(provider=member_provider, model_name=model_name)
            for member_provider, model_name in parse_members(config)
        )
    return ModelProfile.from_stored_fields(
        model_id=getattr(row, "id", None),
        tenant_id=getattr(row, "tenant_id", None),
        type=getattr(row, "type", None),
        provider=getattr(row, "provider", None),
        input_modalities=_row_list(row, "input_modalities"),
        output_modalities=_row_list(row, "output_modalities"),
        features=_row_list(row, "features"),
        capabilities=_row_list(row, "capability"),
        is_omni=bool(getattr(row, "is_omni", False)),
        members=members,
    )


def _row_list(row: Any, name: str) -> list:
    return list(getattr(row, name, None) or ())
