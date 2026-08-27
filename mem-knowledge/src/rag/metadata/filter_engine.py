"""Translate retrieval metadata filters into asynchronous SQL statements."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import DateTime, and_, exists, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...errors import KnowledgeError
from ...models.owned import Document, KnowledgeMetadataBinding
from ...utils.datetime_utils import parse_metadata_time_to_utc_naive
from .builtin_resolver import BuiltinFieldResolver
from .filter_strategies import (
    NumberFilterStrategy,
    StringFilterStrategy,
    TimeFilterStrategy,
    _escape_like,
)


@dataclass(frozen=True)
class FilterCondition:
    field: str
    operator: str
    value: Any = None


@dataclass(frozen=True)
class FilterGroup:
    conditions: list[FilterCondition]
    logic: str = "AND"

    def __post_init__(self) -> None:
        logic = self.logic.strip().upper()
        if logic not in {"AND", "OR"}:
            raise KnowledgeError.from_code("KB_VALIDATION_ERROR", "Filter logic must be AND or OR")
        object.__setattr__(self, "logic", logic)


class MetadataFilterEngine:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.strategies = {
            "string": StringFilterStrategy(),
            "number": NumberFilterStrategy(),
            "time": TimeFilterStrategy(),
        }

    def build_statement(
        self,
        knowledge_id: uuid.UUID,
        filter_groups: list[FilterGroup],
        metadata_defs: dict[str, dict],
    ) -> Any:
        groups = []
        for group in filter_groups:
            conditions = []
            for condition in group.conditions:
                field_def = metadata_defs.get(condition.field)
                if field_def is None:
                    raise KnowledgeError.from_code(
                        "KB_VALIDATION_ERROR",
                        f"Unknown metadata field: {condition.field}",
                    )
                if field_def.get("is_builtin"):
                    expression = self._builtin_expression(condition)
                else:
                    expression = self._custom_expression(condition, field_def)
                conditions.append(expression)
            if conditions:
                groups.append(or_(*conditions) if group.logic == "OR" else and_(*conditions))
        statement = select(Document.id).where(Document.kb_id == knowledge_id)
        return statement.where(and_(*groups)) if groups else statement

    async def execute_async(
        self,
        knowledge_id: uuid.UUID,
        filter_groups: list[FilterGroup],
        metadata_defs: dict[str, dict],
    ) -> list[uuid.UUID]:
        result = await self.db.execute(
            self.build_statement(knowledge_id, filter_groups, metadata_defs)
        )
        return list(result.scalars().all())

    def _custom_expression(self, condition: FilterCondition, field_def: dict) -> Any:
        metadata_id = field_def.get("id")
        if metadata_id is None:
            raise KnowledgeError.from_code("KB_VALIDATION_ERROR", "Metadata definition has no id")
        binding_exists = exists().where(
            and_(
                KnowledgeMetadataBinding.knowledge_id == Document.kb_id,
                KnowledgeMetadataBinding.document_id == Document.id,
                KnowledgeMetadataBinding.metadata_id == metadata_id,
            )
        )
        if condition.operator == "is_missing":
            return ~binding_exists
        if condition.operator == "not_missing":
            return binding_exists
        strategy = self.strategies.get(str(field_def.get("type")))
        if strategy is None or not strategy.supports(condition.operator):
            raise KnowledgeError.from_code("KB_VALIDATION_ERROR", "Unsupported metadata operator")
        return and_(
            binding_exists, strategy.apply(condition.field, condition.operator, condition.value)
        )

    @staticmethod
    def _builtin_expression(condition: FilterCondition) -> Any:
        field = BuiltinFieldResolver.resolve(condition.field)
        if field is None:
            raise KnowledgeError.from_code("KB_VALIDATION_ERROR", "Unknown builtin metadata field")
        column = getattr(Document, field.mapping)
        operator = condition.operator
        value = condition.value
        if field.type == "string":
            values = (
                list(value)
                if hasattr(value, "__iter__") and not isinstance(value, str)
                else [value]
            )
            match operator:
                case "eq":
                    return column == str(value)
                case "ne":
                    return column != str(value)
                case "contains":
                    return column.like(f"%{_escape_like(value)}%", escape="\\")
                case "not_contains":
                    return ~column.like(f"%{_escape_like(value)}%", escape="\\")
                case "starts_with":
                    return column.like(f"{_escape_like(value)}%", escape="\\")
                case "ends_with":
                    return column.like(f"%{_escape_like(value)}", escape="\\")
                case "is_empty" | "is_missing":
                    return or_(column.is_(None), column == "")
                case "not_empty" | "not_missing":
                    return and_(column.is_not(None), column != "")
                case "in":
                    return column.in_([str(item) for item in values])
                case "not_in":
                    return ~column.in_([str(item) for item in values])
        if field.type == "time":
            if operator in {"is_empty", "is_missing"}:
                return column.is_(None)
            if operator in {"not_empty", "not_missing"}:
                return column.is_not(None)
            parsed = parse_metadata_time_to_utc_naive(value)
            if parsed is not None:
                value_expr = literal(parsed, DateTime)
                if operator == "eq":
                    return func.date_trunc("minute", column) == func.date_trunc(
                        "minute", value_expr
                    )
                if operator == "before":
                    return func.date_trunc("minute", column) < func.date_trunc("minute", value_expr)
                if operator == "after":
                    return func.date_trunc("minute", column) > func.date_trunc("minute", value_expr)
        raise KnowledgeError.from_code(
            "KB_VALIDATION_ERROR", "Unsupported builtin metadata operator"
        )


__all__ = ["FilterCondition", "FilterGroup", "MetadataFilterEngine"]
