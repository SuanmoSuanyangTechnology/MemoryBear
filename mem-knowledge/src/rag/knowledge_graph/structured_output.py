"""Normalize structured model responses."""

from collections.abc import Mapping
from typing import Any

from json_repair import json_repair


def unwrap_structured_result(raw_result: Any, schema: type) -> Any:
    if not isinstance(raw_result, Mapping) or "parsed" not in raw_result:
        return raw_result
    parsed = raw_result.get("parsed")
    if parsed is not None:
        return parsed
    raw_message = raw_result.get("raw")
    payloads: list[Any] = []
    for tool_call in getattr(raw_message, "tool_calls", None) or []:
        if not isinstance(tool_call, Mapping):
            continue
        if tool_call.get("name") not in (None, schema.__name__):
            continue
        arguments = tool_call.get("args")
        if isinstance(arguments, Mapping):
            payloads.append(arguments)
    additional = getattr(raw_message, "additional_kwargs", None)
    tool_calls = additional.get("tool_calls", []) if isinstance(additional, Mapping) else []
    for tool_call in tool_calls or []:
        if not isinstance(tool_call, Mapping):
            continue
        function = tool_call.get("function")
        if not isinstance(function, Mapping):
            continue
        if function.get("name") not in (None, schema.__name__):
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, str) and arguments.strip():
            payloads.append(arguments)
    content = getattr(raw_message, "content", None)
    if isinstance(content, str) and content.strip():
        payloads.append(content)
    parsing_error = raw_result.get("parsing_error")
    for payload in payloads:
        try:
            repaired = (
                json_repair.repair_json(payload, return_objects=True)
                if isinstance(payload, str)
                else payload
            )
            return schema.model_validate(repaired)
        except (ValueError, TypeError) as exc:
            parsing_error = exc
    raise ValueError("structured output is not parseable") from (
        parsing_error if isinstance(parsing_error, Exception) else None
    )


__all__ = ["unwrap_structured_result"]
