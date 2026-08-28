from __future__ import annotations

from enum import StrEnum
from typing import Any, Self, Mapping, Collection

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FilterOperator(StrEnum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    EXISTS = "exists"


class FilterLogic(StrEnum):
    AND = "and"
    OR = "or"


class FilterCondition(BaseModel):

    model_config = ConfigDict(frozen=True)

    field: str = Field(min_length=1)
    operator: FilterOperator = FilterOperator.EQ
    value: Any = None

    @model_validator(mode="after")
    def validate_value(self) -> Self:
        if not self.field.strip():
            raise ValueError("filter field cannot be blank")

        if self.operator in {FilterOperator.IN, FilterOperator.NOT_IN}:
            if (
                isinstance(self.value, (str, bytes, Mapping))
                or not isinstance(self.value, Collection)
                or not self.value
            ):
                raise ValueError(
                    f"{self.operator.value} filter value must be a non-empty collection"
                )

        comparison_operators = {
            FilterOperator.GT,
            FilterOperator.GTE,
            FilterOperator.LT,
            FilterOperator.LTE,
        }
        if self.operator in comparison_operators and self.value is None:
            raise ValueError(f"{self.operator.value} filter value cannot be None")

        if self.operator == FilterOperator.EXISTS and not isinstance(self.value, bool):
            raise ValueError("exists filter value must be a boolean")

        return self


class NodeFilter(BaseModel):

    model_config = ConfigDict(frozen=True)

    conditions: tuple[FilterCondition | NodeFilter, ...] = Field(min_length=1)
    logic: FilterLogic = FilterLogic.AND

    @classmethod
    def eq(cls, field: str, value: Any) -> Self:
        return cls(conditions=(FilterCondition(field=field, value=value),))

    @classmethod
    def all_of(cls, *conditions: FilterCondition | NodeFilter) -> Self:
        return cls(logic=FilterLogic.AND, conditions=conditions)

    @classmethod
    def any_of(cls, *conditions: FilterCondition | NodeFilter) -> Self:
        return cls(logic=FilterLogic.OR, conditions=conditions)


class RelationshipFilterScope(StrEnum):
    SOURCE = "source"
    RELATIONSHIP = "relationship"
    TARGET = "target"


class ScopedNodeFilter(BaseModel):

    model_config = ConfigDict(frozen=True)

    scope: RelationshipFilterScope
    node_filter: NodeFilter


class RelationshipFilter(BaseModel):

    model_config = ConfigDict(frozen=True)

    conditions: tuple[ScopedNodeFilter | RelationshipFilter, ...] = Field(
        min_length=1
    )
    logic: FilterLogic = FilterLogic.AND

    @classmethod
    def all_of(
            cls,
            *conditions: ScopedNodeFilter | RelationshipFilter,
    ) -> Self:
        return cls(logic=FilterLogic.AND, conditions=conditions)

    @classmethod
    def any_of(
            cls,
            *conditions: ScopedNodeFilter | RelationshipFilter,
    ) -> Self:
        return cls(logic=FilterLogic.OR, conditions=conditions)

    @staticmethod
    def source(node_filter: NodeFilter) -> ScopedNodeFilter:
        """将节点过滤器绑定到关系的起点节点（Cypher 变量 source）。"""
        return ScopedNodeFilter(
            scope=RelationshipFilterScope.SOURCE,
            node_filter=node_filter,
        )

    @staticmethod
    def relationship(node_filter: NodeFilter) -> ScopedNodeFilter:
        """将节点过滤器绑定到关系本身（Cypher 变量 r）。"""
        return ScopedNodeFilter(
            scope=RelationshipFilterScope.RELATIONSHIP,
            node_filter=node_filter,
        )

    @staticmethod
    def target(node_filter: NodeFilter) -> ScopedNodeFilter:
        """将节点过滤器绑定到关系的终点节点（Cypher 变量 target）。"""
        return ScopedNodeFilter(
            scope=RelationshipFilterScope.TARGET,
            node_filter=node_filter,
        )
