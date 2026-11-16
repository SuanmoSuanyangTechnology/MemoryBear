"""Save-time validation for app tool selections (second/third level).

Wire rules come from ``PUT /api/apps/{app_id}/config`` (contract §2.7):
enabled entries must reference a live tool of the tenant, third-level
operations must equal an ``operation_key`` of that tool, and an MCP whole-tool
selection requires a non-empty cached method list. ``(tool_id, operation)``
pairs already present in the stored config are grandfathered and only
normalized; a whole-tool entry overrides every third-level entry of the same
tool, and duplicates keep their first occurrence.

Each entry also carries a ``name`` snapshot, refreshed from the tenant's tool row
on every save. When the row can no longer be resolved (cross-tenant / hard
deleted / malformed id) the last stored snapshot is kept, so the tool list can
still render a name instead of a bare uuid.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any

from ..errors import BizCode, BusinessException
from ..models.tool_model import ToolType
from ..tools.selection import normalize_tool_selection

if TYPE_CHECKING:
    from .tool_service import ToolService


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _reject(
    code: BizCode,
    message: str,
    tool_id: str | None,
    operation: str | None,
) -> None:
    detail = {"tool_id": tool_id, "operation": operation}
    raise BusinessException(message, code, context=detail, data=detail)


def _stored_keys(
    stored_tools: list[dict[str, Any]] | dict[str, Any] | None,
) -> set[tuple[str, str | None]]:
    if not isinstance(stored_tools, list):
        return set()
    keys: set[tuple[str, str | None]] = set()
    for item in stored_tools:
        if not isinstance(item, dict):
            continue
        tool_id = _text(item.get("tool_id"))
        if tool_id:
            keys.add((tool_id, _text(item.get("operation"))))
    return keys


def _stored_names(
    stored_tools: list[dict[str, Any]] | dict[str, Any] | None,
) -> dict[tuple[str, str | None], str]:
    """Name snapshots already persisted in the stored config, by (tool_id, operation)."""
    if not isinstance(stored_tools, list):
        return {}
    names: dict[tuple[str, str | None], str] = {}
    for item in stored_tools:
        if not isinstance(item, dict):
            continue
        tool_id = _text(item.get("tool_id"))
        name = _text(item.get("name"))
        if tool_id and name:
            names[(tool_id, _text(item.get("operation")))] = name
    return names


def _any_stored_name(
    stored_names: dict[tuple[str, str | None], str],
    tool_id: str,
) -> str | None:
    """Any snapshot recorded for this tool, whatever operation it was saved under."""
    for (stored_id, _operation), name in stored_names.items():
        if stored_id == tool_id:
            return name
    return None


def _requested_ids(entries: list[dict[str, Any]]) -> list[str]:
    requested: list[str] = []
    for entry in entries:
        tool_id = _text(entry.get("tool_id"))
        if tool_id and tool_id not in requested:
            requested.append(tool_id)
    return requested


def _normalize(
    entry: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    stored_names: dict[tuple[str, str | None], str],
) -> dict[str, Any]:
    tool_id = _text(entry.get("tool_id"))
    item = by_id.get(tool_id) if tool_id else None
    methods = (item or {}).get("methods") or []
    normalized = dict(entry)
    normalized["tool_id"] = tool_id
    normalized["operation"] = normalize_tool_selection(
        item.get("tool_type") if item else None,
        _text(entry.get("operation")),
        has_operation_enum=any(
            method.get("operation_key") is not None for method in methods
        ),
    )
    # 名称快照：命中时用库里的最新名称覆盖；未命中（跨租户 / 已删除 / id 非法）
    # 时沿用请求里的快照或已存快照——保存请求通常只回传 tool_id/operation/enabled，
    # 不回传 name，所以这里必须以库里的旧快照兜底，否则一次保存就会把名称抹掉。
    name = _text((item or {}).get("name"))
    if not name and tool_id:
        name = (
            _text(normalized.get("name"))
            or stored_names.get((tool_id, normalized["operation"]))
            or stored_names.get((tool_id, _text(entry.get("operation"))))
            or stored_names.get((tool_id, None))
            # 引用层级变化（方法级 ↔ 整工具级）时键对不上，同 tool_id 的任意一条
            # 快照都比裸 id 有用：名称属于工具而不是方法。
            or _any_stored_name(stored_names, tool_id)
        )
    if name:
        normalized["name"] = name
    else:
        # 空值不落库，避免给每条引用平白多出一个 ``"name": null``。
        normalized.pop("name", None)
    return normalized


def _validate_enabled(
    entry: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    stored_keys: set[tuple[str, str | None]],
) -> None:
    tool_id = entry["tool_id"]
    operation = entry["operation"]
    if not tool_id:
        _reject(BizCode.MISSING_PARAMETER, "工具 ID 不能为空", tool_id, operation)
    if (tool_id, operation) in stored_keys:
        return

    item = by_id.get(tool_id)
    if item is None or not item.get("is_active"):
        _reject(
            BizCode.TOOL_SELECTION_INACTIVE, "工具已停用或删除", tool_id, operation
        )

    methods = item.get("methods") or []
    if operation is not None:
        operation_keys = {
            method.get("operation_key")
            for method in methods
            if method.get("operation_key") is not None
        }
        if operation not in operation_keys:
            _reject(
                BizCode.TOOL_OPERATION_INVALID,
                "工具方法不存在或不属于该工具",
                tool_id,
                operation,
            )
    elif item.get("tool_type") == ToolType.MCP.value and not methods:
        _reject(
            BizCode.TOOL_METHODS_UNAVAILABLE,
            "MCP 工具方法缓存为空",
            tool_id,
            operation,
        )


def validate_tool_selection(
    service: ToolService,
    tenant_id: uuid.UUID,
    tools: list[dict[str, Any]] | None,
    stored_tools: list[dict[str, Any]] | dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Validate, normalize and deduplicate the tools block of an app config."""

    entries = [dict(item) for item in (tools or []) if isinstance(item, dict)]
    if not entries:
        return []

    stored_keys = _stored_keys(stored_tools)
    stored_names = _stored_names(stored_tools)
    requested = _requested_ids(entries)
    items, _missing = (
        asyncio.run(service.get_tool_methods_batch(requested, tenant_id))
        if requested
        else ([], [])
    )
    by_id = {item["tool_id"]: item for item in items}

    normalized = [_normalize(entry, by_id, stored_names) for entry in entries]
    whole_tool_ids = {
        entry["tool_id"]
        for entry in normalized
        if entry["tool_id"] and entry["operation"] is None
    }
    kept = [
        entry
        for entry in normalized
        if not (
            entry["tool_id"] in whole_tool_ids and entry["operation"] is not None
        )
    ]

    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()
    for entry in kept:
        tool_id = entry["tool_id"]
        if tool_id:
            key = (tool_id, entry["operation"])
            if key in seen:
                continue
            seen.add(key)
        if entry.get("enabled"):
            _validate_enabled(entry, by_id, stored_keys)
        result.append(entry)
    return result


__all__ = ["validate_tool_selection"]
