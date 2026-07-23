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
    additional = getattr(raw_message, "additional_kwargs", None)
    tool_calls = (
        additional.get("tool_calls", [])
        if isinstance(additional, Mapping)
        else []
    )
    payloads: list[str] = []
    for tool_call in tool_calls:
        if not isinstance(tool_call, Mapping):
            continue
        function = tool_call.get("function")
        if not isinstance(function, Mapping):
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, str) and arguments.strip():
            payloads.append(arguments)

    content = getattr(raw_message, "content", None)
    if isinstance(content, str) and content.strip():
        payloads.append(content)

    for payload in payloads:
        repaired = json_repair.repair_json(
            payload,
            return_objects=True,
        )
        return schema.model_validate(repaired)

    parsing_error = raw_result.get("parsing_error")
    raise ValueError("structured output is not parseable") from (
        parsing_error if isinstance(parsing_error, Exception) else None
    )
