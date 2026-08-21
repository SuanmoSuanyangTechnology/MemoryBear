"""LLM-assisted metadata extraction copied from the legacy retrieval service."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import json_repair
from langchain_core.messages import HumanMessage, SystemMessage

from ...utils.datetime_utils import parse_metadata_time_to_utc_naive
from .filter_engine import FilterCondition, FilterGroup

_ALIASES = {
    "contains": "contains",
    "not contains": "not_contains",
    "not_contains": "not_contains",
    "start with": "starts_with",
    "starts with": "starts_with",
    "starts_with": "starts_with",
    "end with": "ends_with",
    "ends with": "ends_with",
    "ends_with": "ends_with",
    "is": "eq",
    "=": "eq",
    "==": "eq",
    "eq": "eq",
    "is not": "ne",
    "!=": "ne",
    "≠": "ne",
    "ne": "ne",
    ">": "gt",
    "gt": "gt",
    "<": "lt",
    "lt": "lt",
    "≥": "gte",
    ">=": "gte",
    "gte": "gte",
    "≤": "lte",
    "<=": "lte",
    "lte": "lte",
    "before": "before",
    "after": "after",
    "empty": "is_empty",
    "is empty": "is_empty",
    "not empty": "not_empty",
    "is not empty": "not_empty",
    "missing": "is_missing",
    "is missing": "is_missing",
    "exists": "not_missing",
    "not missing": "not_missing",
}
_SUPPORTED = {
    "string": {
        "eq",
        "ne",
        "contains",
        "not_contains",
        "starts_with",
        "ends_with",
        "is_empty",
        "not_empty",
        "is_missing",
        "not_missing",
    },
    "number": {
        "eq",
        "ne",
        "gt",
        "lt",
        "gte",
        "lte",
        "is_empty",
        "not_empty",
        "is_missing",
        "not_missing",
    },
    "time": {"eq", "before", "after", "is_empty", "not_empty", "is_missing", "not_missing"},
}


async def generate_filter_groups(
    query: str,
    metadata_defs: dict[str, dict],
    llm: Any,
) -> list[FilterGroup]:
    if not metadata_defs:
        return []
    fields = [
        {"name": name, "type": definition.get("type")} for name, definition in metadata_defs.items()
    ]
    prompt = (
        "### Job Description\n"
        "You are a text metadata extract engine that extracts text metadata based on user input.\n"
        "### Task\n"
        "Only extract metadata that exists in the input text from the provided metadata list. "
        "Use operators contains, not contains, start with, end with, is, is not, empty, "
        "not empty, missing, exists, =, ≠, >, <, ≥, ≤, before, or after.\n"
        "### Format\n"
        "Return JSON with key metadata_fields. Each item contains metadata_field_name, "
        "metadata_field_value, and comparison_operator. Return JSON only.\n\n"
        f"input_text:\n{query}\n\nmetadata_fields:\n{json.dumps(fields, ensure_ascii=False)}"
    )
    try:
        response = await llm.ainvoke(
            [
                SystemMessage(
                    content=(
                        "Extract only clearly present metadata filters and only use "
                        "provided fields."
                    )
                ),
                HumanMessage(content=prompt),
            ]
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        return []
    raw = getattr(response, "content", response)
    parsed = json_repair.loads(str(raw))
    items = parsed.get("metadata_fields") if isinstance(parsed, dict) else parsed
    if not isinstance(items, list):
        return []
    conditions = []
    for item in items:
        condition = _normalize(item, metadata_defs) if isinstance(item, dict) else None
        if condition is not None:
            conditions.append(condition)
    return [FilterGroup(conditions, "AND")] if conditions else []


def _normalize(item: dict[str, Any], metadata_defs: dict[str, dict]) -> FilterCondition | None:
    name = item.get("metadata_field_name", item.get("field", item.get("name")))
    if not isinstance(name, str) or name not in metadata_defs:
        return None
    raw_operator = item.get("comparison_operator", item.get("operator"))
    if not isinstance(raw_operator, str):
        return None
    operator = _ALIASES.get(" ".join(raw_operator.strip().lower().split()))
    field_type = metadata_defs[name].get("type")
    if operator not in _SUPPORTED.get(field_type, set()):
        return None
    value = item.get("metadata_field_value", item.get("value"))
    if operator in {"is_empty", "not_empty", "is_missing", "not_missing"}:
        value = None
    elif field_type == "string":
        if value is None:
            return None
        value = str(value)
    elif field_type == "number":
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        value = int(number) if number.is_integer() else number
    elif field_type == "time":
        try:
            if parse_metadata_time_to_utc_naive(value) is None:
                return None
        except (TypeError, ValueError):
            return None
    return FilterCondition(name, operator, value)


__all__ = ["generate_filter_groups"]
