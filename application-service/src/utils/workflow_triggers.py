"""
Workflow trigger utilities (host ``core/workflow/triggers`` subset).

Covers only the parts needed by the definition-side write path:
normalization and validation of ``trigger`` nodes.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

SUPPORTED_TRIGGER_TYPES = {"webhook", "schedule"}
_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
TRIGGER_NODES_PREPARED_FLAG = "_trigger_nodes_prepared"


def get_trigger_config(node: dict[str, Any] | None) -> dict[str, Any]:
    config = (node or {}).get("config") or {}
    return config if isinstance(config, dict) else {}


def get_trigger_type(node: dict[str, Any] | None) -> str:
    return str(get_trigger_config(node).get("trigger_type") or "").strip()


def iter_trigger_nodes(
    nodes: list[dict[str, Any]] | None,
    trigger_type: str | None = None,
) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for node in nodes or []:
        if node.get("type") != "trigger":
            continue
        if trigger_type and get_trigger_type(node) != trigger_type:
            continue
        matched.append(node)
    return matched


def normalize_trigger_nodes(nodes: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized_nodes: list[dict[str, Any]] = []
    for index, raw in enumerate(nodes or []):
        node = dict(raw)
        if node.get("type") != "trigger":
            normalized_nodes.append(node)
            continue

        config = dict(get_trigger_config(node))
        trigger_type = str(config.get("trigger_type") or "").strip()
        runtime = dict(node.get("runtime") or {})

        node["id"] = node.get("id") or f"trigger_{uuid.uuid4().hex[:12]}"
        node["name"] = node.get("name") or f"{trigger_type or 'trigger'}_{index + 1}"
        config["trigger_type"] = trigger_type
        config.setdefault("enabled", True)
        node["config"] = config
        node["runtime"] = runtime
        node.pop("trigger_type", None)
        node.pop("enabled", None)

        if trigger_type == "webhook":
            config.setdefault("method", "POST")
            config.setdefault("route_key", f"wh_{uuid.uuid4().hex}")
            config.setdefault("content_type", "application/json")
            config.setdefault("query_params", [])
            config.setdefault("header_params", [])
            config.setdefault("req_body_params", [])
            config.setdefault("response", {"status_code": 200, "body": None})

        if trigger_type == "schedule":
            config.setdefault("timezone", "UTC")
            runtime.setdefault("last_triggered_at", None)

        normalized_nodes.append(node)

    validate_trigger_nodes(normalized_nodes)
    return normalized_nodes


def validate_trigger_nodes(nodes: list[dict[str, Any]] | None) -> None:
    route_keys: set[str] = set()
    for node in iter_trigger_nodes(nodes):
        trigger_type = get_trigger_type(node)
        if trigger_type not in SUPPORTED_TRIGGER_TYPES:
            raise ValueError(f"不支持的触发器类型: {trigger_type}")

        config = node.get("config") or {}
        runtime = node.get("runtime") or {}
        if not isinstance(config, dict):
            raise ValueError(f"触发器节点 {node.get('id')} 的 config 必须为对象")
        if not isinstance(runtime, dict):
            raise ValueError(f"触发器节点 {node.get('id')} 的 runtime 必须为对象")

        if trigger_type == "webhook":
            method = str(config.get("method", "POST")).upper()
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                raise ValueError(f"Webhook 触发器 method 不合法: {method}")
            route_key = str(config.get("route_key") or "").strip()
            if not route_key:
                raise ValueError("Webhook 触发器缺少 route_key")
            if route_key in route_keys:
                raise ValueError(f"Webhook route_key 重复: {route_key}")
            route_keys.add(route_key)
            _validate_webhook_param_defs(config.get("query_params") or [], "query_params")
            _validate_webhook_param_defs(config.get("header_params") or [], "header_params")
            _validate_webhook_param_defs(config.get("req_body_params") or [], "req_body_params")

        if trigger_type == "schedule":
            has_cron = bool(config.get("cron"))
            has_interval = config.get("interval_seconds") is not None
            if not has_cron and not has_interval:
                raise ValueError("Schedule 触发器必须配置 cron 或 interval_seconds")
            if has_interval and int(config["interval_seconds"]) <= 0:
                raise ValueError("Schedule 触发器的 interval_seconds 必须大于 0")


def _validate_webhook_param_defs(param_defs: list[dict[str, Any]], section_name: str) -> None:
    names: set[str] = set()
    for param_def in param_defs:
        if not isinstance(param_def, dict):
            raise ValueError(f"{section_name} 必须为对象数组")
        name = str(param_def.get("name") or "").strip()
        if not name:
            raise ValueError(f"{section_name} 中存在缺少 name 的参数")
        if not _IDENTIFIER_PATTERN.match(name):
            raise ValueError(f"{section_name} 参数名不合法: {name}")
        if name in names:
            raise ValueError(f"{section_name} 参数名重复: {name}")
        names.add(name)


__all__ = [
    "SUPPORTED_TRIGGER_TYPES",
    "TRIGGER_NODES_PREPARED_FLAG",
    "get_trigger_config",
    "get_trigger_type",
    "iter_trigger_nodes",
    "normalize_trigger_nodes",
    "validate_trigger_nodes",
]
