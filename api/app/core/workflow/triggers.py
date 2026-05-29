"""
工作流触发器工具。

负责：
1. `trigger` 节点配置归一化与校验
2. Webhook / Schedule 事件转 TriggerNode 输出
3. Schedule cron 的轻量判断
"""

from __future__ import annotations

import datetime
import re
import uuid
from typing import Any
from zoneinfo import ZoneInfo


SUPPORTED_TRIGGER_TYPES = {"webhook", "schedule"}
_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
TRIGGER_NODES_PREPARED_FLAG = "_trigger_nodes_prepared"
SCHEDULE_DISPATCH_LEASE_SECONDS = 600


def resolve_schedule_timezone(timezone_name: str | None) -> tuple[datetime.tzinfo, str]:
    normalized_name = str(timezone_name or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(normalized_name), normalized_name
    except Exception:
        return datetime.timezone.utc, "UTC"


def build_schedule_now_payload(
    current: datetime.datetime | None = None,
    timezone_name: str | None = None,
) -> dict[str, Any]:
    current_time = current or datetime.datetime.now(datetime.timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=datetime.timezone.utc)

    timezone, normalized_name = resolve_schedule_timezone(timezone_name)
    localized = current_time.astimezone(timezone)
    return {
        "iso": localized.isoformat(),
        "timestamp": int(current_time.timestamp()),
        "date": localized.date().isoformat(),
        "time": localized.time().replace(microsecond=0).isoformat(),
        "timezone": normalized_name,
    }


def normalize_schedule_now_payload(
    now: dict[str, Any] | None,
    timezone_name: str | None = None,
) -> dict[str, Any]:
    timezone_value = None
    if isinstance(now, dict):
        timezone_value = now.get("timezone")
    timezone_value = timezone_value or timezone_name or "UTC"

    base_time = None
    if isinstance(now, dict):
        iso_value = now.get("iso")
        if iso_value:
            base_time = parse_datetime(str(iso_value))

        if base_time is None and now.get("timestamp") is not None:
            try:
                base_time = datetime.datetime.fromtimestamp(
                    int(now["timestamp"]),
                    tz=datetime.timezone.utc,
                )
            except (TypeError, ValueError, OSError):
                base_time = None

    normalized = build_schedule_now_payload(base_time, timezone_value)
    if isinstance(now, dict):
        return {
            **now,
            **normalized,
        }
    return normalized


def find_start_node_id(nodes: list[dict[str, Any]]) -> str | None:
    for node in nodes or []:
        if node.get("type") in {"start", "trigger"}:
            return node.get("id")
    return None


def get_trigger_config(node: dict[str, Any] | None) -> dict[str, Any]:
    config = (node or {}).get("config") or {}
    return config if isinstance(config, dict) else {}


def get_trigger_type(node: dict[str, Any] | None) -> str:
    return str(get_trigger_config(node).get("trigger_type") or "").strip()


def is_trigger_enabled(node: dict[str, Any] | None) -> bool:
    return bool(get_trigger_config(node).get("enabled", True))


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


def get_value_by_path(payload: dict[str, Any], path: Any) -> Any:
    if not isinstance(path, str):
        return path

    normalized = path.strip()
    if normalized.startswith("$."):
        normalized = normalized[2:]

    if not normalized:
        return None

    current: Any = payload
    for part in normalized.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            if not part.isdigit():
                return None
            index = int(part)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
    return current


def _extract_named_params(
    values: Any,
    param_defs: list[dict[str, Any]],
    section_name: str,
) -> dict[str, Any]:
    if not param_defs:
        return values if isinstance(values, dict) else {}

    extracted: dict[str, Any] = {}
    for param_def in param_defs:
        name = param_def["name"]
        value = get_value_by_path(values or {}, name)
        if value is None and param_def.get("required"):
            raise ValueError(f"Webhook 缺少必填 {section_name} 参数: {name}")
        extracted[name] = value
    return extracted


def build_webhook_trigger_output(
    trigger_node: dict[str, Any],
    event: dict[str, Any],
) -> dict[str, Any]:
    config = trigger_node.get("config") or {}
    body = event.get("body")
    query = event.get("query") or {}
    headers = event.get("headers") or {}
    return {
        "query_params": _extract_named_params(query, config.get("query_params") or [], "query"),
        "header_params": _extract_named_params(headers, config.get("header_params") or [], "header"),
        "req_body_params": _extract_named_params(body, config.get("req_body_params") or [], "body"),
        "webhook_raw": {
            "body": body,
            "query": query,
            "headers": headers,
            "meta": event.get("meta") or {},
        },
    }


def build_schedule_trigger_output(
    trigger_node: dict[str, Any],
    event: dict[str, Any],
) -> dict[str, Any]:
    config = trigger_node.get("config") or {}
    now = normalize_schedule_now_payload(
        event.get("now"),
        config.get("timezone", "UTC"),
    )
    return {
        "schedule": now,
    }


def parse_datetime(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _match_cron_part(part: str, value: int) -> bool:
    if part == "*":
        return True
    if part.startswith("*/"):
        step = int(part[2:])
        return value % step == 0
    if "," in part:
        return any(_match_cron_part(item.strip(), value) for item in part.split(","))
    if "-" in part:
        start, end = part.split("-", 1)
        return int(start) <= value <= int(end)
    return int(part) == value


def cron_matches(cron_expr: str, current: datetime.datetime) -> bool:
    parts = cron_expr.split()
    if len(parts) != 5:
        raise ValueError("cron 表达式必须为 5 段")

    minute, hour, day, month, weekday = parts
    weekday_value = (current.weekday() + 1) % 7
    return (
        _match_cron_part(minute, current.minute)
        and _match_cron_part(hour, current.hour)
        and _match_cron_part(day, current.day)
        and _match_cron_part(month, current.month)
        and _match_cron_part(weekday, weekday_value)
    )


def is_schedule_trigger_due(
    trigger: dict[str, Any],
    now: datetime.datetime | None = None,
) -> bool:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    config = trigger.get("config") or {}
    runtime = trigger.get("runtime") or {}
    last_triggered_at = parse_datetime(runtime.get("last_triggered_at"))
    last_dispatched_at = parse_datetime(runtime.get("last_dispatched_at"))
    dispatch_status = str(runtime.get("dispatch_status") or "").strip()
    dispatch_lease_seconds = SCHEDULE_DISPATCH_LEASE_SECONDS
    raw_dispatch_lease_seconds = config.get("dispatch_lease_seconds")
    if raw_dispatch_lease_seconds not in (None, ""):
        try:
            parsed_dispatch_lease_seconds = int(raw_dispatch_lease_seconds)
            if parsed_dispatch_lease_seconds > 0:
                dispatch_lease_seconds = parsed_dispatch_lease_seconds
        except (TypeError, ValueError):
            dispatch_lease_seconds = SCHEDULE_DISPATCH_LEASE_SECONDS

    if dispatch_status in {"queued", "running"} and last_dispatched_at is not None:
        if (now - last_dispatched_at).total_seconds() < dispatch_lease_seconds:
            return False

    if config.get("interval_seconds") is not None:
        interval_seconds = int(config["interval_seconds"])
        if last_triggered_at is None:
            return True
        return (now - last_triggered_at).total_seconds() >= interval_seconds

    cron_expr = config.get("cron")
    if not cron_expr:
        return False

    timezone, _ = resolve_schedule_timezone(config.get("timezone", "UTC"))
    local_now = now.astimezone(timezone)

    if not cron_matches(cron_expr, local_now):
        return False

    if last_triggered_at is None:
        return True

    # 避免同一时区的同一分钟内重复触发
    last_local = last_triggered_at.astimezone(timezone)
    return last_local.replace(second=0, microsecond=0) != local_now.replace(second=0, microsecond=0)
