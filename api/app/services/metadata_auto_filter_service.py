import asyncio
import json
import logging
from typing import Any, Protocol

import json_repair
from langchain_core.messages import HumanMessage, SystemMessage

from app.core.rag.metadata.filter_engine import (
    FilterCondition as EngineFilterCondition,
    FilterGroup as EngineFilterGroup,
)
from app.core.utils.datetime_utils import parse_iso_to_utc_naive

logger = logging.getLogger(__name__)


class AsyncMetadataLLM(Protocol):
    """Shared asynchronous LLM contract used by metadata filtering."""

    async def ainvoke(self, input: Any, config: dict | None = None, **kwargs: Any) -> Any:
        """Invoke the shared model asynchronously."""


class MetadataAutoFilterService:
    """Generate metadata filter groups from a user query with LLM assistance."""

    _OPERATOR_ALIASES = {
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
        "is_empty": "is_empty",
        "not empty": "not_empty",
        "is not empty": "not_empty",
        "not_empty": "not_empty",
        "missing": "is_missing",
        "is missing": "is_missing",
        "not exists": "is_missing",
        "not_exist": "is_missing",
        "not_exists": "is_missing",
        "is_missing": "is_missing",
        "exists": "not_missing",
        "exist": "not_missing",
        "is exists": "not_missing",
        "is exist": "not_missing",
        "not missing": "not_missing",
        "not_missing": "not_missing",
    }
    _SUPPORTED_OPERATORS = {
        "string": {
            "eq", "ne", "contains", "not_contains",
            "starts_with", "ends_with", "is_empty", "not_empty",
            "is_missing", "not_missing",
        },
        "number": {
            "eq", "ne", "gt", "lt", "gte", "lte", "is_empty", "not_empty",
            "is_missing", "not_missing",
        },
        "time": {"eq", "before", "after", "is_empty", "not_empty", "is_missing", "not_missing"},
    }

    @classmethod
    def generate_filter_groups(
        cls,
        query: str,
        metadata_defs: dict[str, dict],
        llm: Any,
    ) -> list[EngineFilterGroup]:
        if not metadata_defs:
            return []

        # 参数已折叠进 llm 实例（RedBearLLM 的 extra_params，经 strip_unsupported_llm_params
        # 按 provider 剥离），此处不再单独传 gen_conf。
        raw_conditions = cls._extract_metadata_conditions(
            query=query,
            metadata_defs=metadata_defs,
            llm=llm,
        )
        conditions = []
        for raw_condition in raw_conditions:
            condition = cls._normalize_condition(raw_condition, metadata_defs)
            if condition:
                conditions.append(condition)

        logger.info(
            "[MetadataAutoFilter] extracted %s valid conditions from %s candidates",
            len(conditions),
            len(raw_conditions),
        )
        if not conditions:
            return []
        return [EngineFilterGroup(conditions=conditions, logic="AND")]

    @classmethod
    async def generate_filter_groups_async(
        cls,
        query: str,
        metadata_defs: dict[str, dict],
        llm: AsyncMetadataLLM,
    ) -> list[EngineFilterGroup]:
        """Generate metadata filters without blocking the request event loop."""

        if not metadata_defs:
            return []

        raw_conditions = await cls._extract_metadata_conditions_async(
            query=query,
            metadata_defs=metadata_defs,
            llm=llm,
        )
        conditions = []
        for raw_condition in raw_conditions:
            condition = cls._normalize_condition(raw_condition, metadata_defs)
            if condition:
                conditions.append(condition)

        logger.info(
            "[MetadataAutoFilter] async extracted %s valid conditions from %s candidates",
            len(conditions),
            len(raw_conditions),
        )
        if not conditions:
            return []
        return [EngineFilterGroup(conditions=conditions, logic="AND")]

    @classmethod
    def _extract_metadata_conditions(
        cls,
        query: str,
        metadata_defs: dict[str, dict],
        llm: Any,
    ) -> list[dict[str, Any]]:
        system_prompt = (
            "You are a text metadata extract engine. Extract only metadata filters that are clearly "
            "present in the user's input and only use fields from the provided metadata list."
        )
        # RedBearLLM.invoke 不会像 chat_model.Base.chat 那样自动把 system 注入 history，
        # 需自己在 messages 里放一条 SystemMessage。
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=cls._build_prompt(query=query, metadata_defs=metadata_defs)),
        ]
        try:
            response = llm.invoke(messages)
        except Exception as exc:
            logger.warning(
                "[MetadataAutoFilter] LLM invoke failed error_type=%s",
                type(exc).__name__,
            )
            return []
        content = response.content if hasattr(response, "content") else str(response)
        logger.info("[MetadataAutoFilter] LLM response received length=%s", len(str(content)))
        if not content:
            logger.warning("[MetadataAutoFilter] LLM returned no usable content")
            return []
        return cls._parse_llm_response(str(content))

    @classmethod
    async def _extract_metadata_conditions_async(
        cls,
        query: str,
        metadata_defs: dict[str, dict],
        llm: AsyncMetadataLLM,
    ) -> list[dict[str, Any]]:
        system_prompt = (
            "You are a text metadata extract engine. Extract only metadata filters that are clearly "
            "present in the user's input and only use fields from the provided metadata list."
        )
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=cls._build_prompt(query=query, metadata_defs=metadata_defs)),
        ]
        try:
            response = await llm.ainvoke(messages)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[MetadataAutoFilter] async LLM invoke failed error_type=%s",
                type(exc).__name__,
            )
            return []
        content = response.content if hasattr(response, "content") else str(response)
        if not content:
            logger.warning("[MetadataAutoFilter] async LLM returned no usable content")
            return []
        logger.info("[MetadataAutoFilter] async LLM response received length=%s", len(str(content)))
        return cls._parse_llm_response(str(content))

    @staticmethod
    def _build_prompt(query: str, metadata_defs: dict[str, dict]) -> str:
        metadata_fields = [
            {
                "name": field_name,
                "type": field_def.get("type"),
            }
            for field_name, field_def in metadata_defs.items()
        ]
        return (
            "### Job Description\n"
            "You are a text metadata extract engine that extracts text metadata based on user input.\n"
            "### Task\n"
            "Only extract metadata that exists in the input text from the provided metadata list. "
            "Use one of these operators: [\"contains\", \"not contains\", \"start with\", "
            "\"end with\", \"is\", \"is not\", \"empty\", \"not empty\", \"missing\", "
            "\"exists\", \"=\", \"≠\", \">\", \"<\", \"≥\", \"≤\", \"before\", \"after\"].\n"
            "### Format\n"
            "Return a JSON object with key \"metadata_fields\". The value must be an array of objects. "
            "Each object must contain \"metadata_field_name\", \"metadata_field_value\", "
            "and \"comparison_operator\".\n"
            "### Constraint\n"
            "Do not include any field that is not in metadata_fields. "
            "Do not include anything other than JSON in your response.\n\n"
            f"input_text:\n{query}\n\n"
            f"metadata_fields:\n{json.dumps(metadata_fields, ensure_ascii=False)}"
        )

    @staticmethod
    def _parse_llm_response(content: str) -> list[dict[str, Any]]:
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").strip()
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
        try:
            parsed = json_repair.loads(cleaned)
        except Exception:
            logger.warning("[MetadataAutoFilter] Failed to parse LLM response as JSON")
            return []

        if isinstance(parsed, dict):
            items = parsed.get("metadata_fields")
        elif isinstance(parsed, list):
            items = parsed
        else:
            return []

        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    @classmethod
    def _normalize_condition(
        cls,
        raw_condition: dict[str, Any],
        metadata_defs: dict[str, dict],
    ) -> EngineFilterCondition | None:
        field_name = cls._get_first_present(
            raw_condition,
            ("metadata_field_name", "field", "name"),
        )
        if not isinstance(field_name, str):
            return None
        field_name = field_name.strip()
        field_def = metadata_defs.get(field_name)
        if not field_def:
            return None

        operator = cls._normalize_operator(
            cls._get_first_present(raw_condition, ("comparison_operator", "operator")),
        )
        field_type = field_def.get("type")
        if operator not in cls._SUPPORTED_OPERATORS.get(field_type, set()):
            return None

        value = cls._get_first_present(raw_condition, ("metadata_field_value", "value"))
        normalized_value = cls._normalize_value(
            value=value,
            field_type=field_type,
            operator=operator,
        )
        if normalized_value is _InvalidValue:
            return None

        return EngineFilterCondition(
            field=field_name,
            operator=operator,
            value=normalized_value,
        )

    @staticmethod
    def _get_first_present(values: dict[str, Any], keys: tuple[str, ...]) -> Any:
        for key in keys:
            if key in values:
                return values[key]
        return None

    @classmethod
    def _normalize_operator(cls, operator: Any) -> str | None:
        if not isinstance(operator, str):
            return None
        return cls._OPERATOR_ALIASES.get(" ".join(operator.strip().lower().split()))

    @classmethod
    def _normalize_value(cls, value: Any, field_type: str, operator: str) -> Any:
        if operator in ("is_empty", "not_empty", "is_missing", "not_missing"):
            return None
        match field_type:
            case "string":
                if value is None:
                    return _InvalidValue
                return str(value)
            case "number":
                return cls._normalize_number(value)
            case "time":
                return cls._normalize_time(value)
        return _InvalidValue

    @staticmethod
    def _normalize_number(value: Any) -> int | float | object:
        if isinstance(value, bool) or value is None:
            return _InvalidValue
        try:
            number = float(value)
        except (TypeError, ValueError):
            return _InvalidValue
        return int(number) if number.is_integer() else number

    @staticmethod
    def _normalize_time(value: Any) -> str | object:
        if not isinstance(value, str) or not value.strip():
            return _InvalidValue
        try:
            if parse_iso_to_utc_naive(value.strip()) is None:
                return _InvalidValue
        except ValueError:
            return _InvalidValue
        return value.strip()


_InvalidValue = object()
