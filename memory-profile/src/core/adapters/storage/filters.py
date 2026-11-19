"""Storage filter compilation for the read paths.

This module owns the storage query vocabulary: :class:`FilterCondition` (one
physical-property predicate), :class:`FilterOperator` and :class:`NodeFilterSpec`
(conditions for one node family), plus the wider internal :class:`NodeFilter`
that also supports ``id IN (...)``. Application code must not build these
objects — it passes domain criteria (for example ``CommunityRecallQuery``) to a
port and the adapter maps them here; that keeps physical property names out of
the domain/application layers.

Compilers mirror the host DSL semantics:

* ``equals`` with ``None`` -> ``IS NULL`` / ``must_not exists``;
* ``exists=True`` -> ``IS NOT NULL`` / ``exists``;
* ``exists=False`` -> ``IS NULL`` / ``must_not exists``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from src.core.domain.enums import MemorySource

#: ``write_mode`` of Dialogue nodes the read path is allowed to return
#: (host storage policy; only the graph payload knows this value).
DIALOGUE_WRITE_MODE = "fast"


class FilterOperator(StrEnum):
    """The filter operators the read paths need (a subset of the host DSL)."""

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"


@dataclass(frozen=True, slots=True)
class FilterCondition:
    """One storage filter condition; compilers turn it into Cypher/ES."""

    field: str
    operator: FilterOperator = FilterOperator.EQUALS
    value: Any = None


@dataclass(frozen=True, slots=True)
class NodeFilterSpec:
    """Conditions for one node family (label), combined with ``AND``."""

    conditions: tuple[FilterCondition, ...] = ()

    @classmethod
    def all_of(cls, *conditions: FilterCondition) -> NodeFilterSpec:
        return cls(conditions=tuple(conditions))


@dataclass(frozen=True, slots=True)
class InCondition:
    """``field IN (values)``; the host read paths use it for id batches."""

    field: str
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NodeFilter:
    """Conjunction of conditions; nested filters are parenthesised groups."""

    conditions: tuple[FilterCondition | InCondition | NodeFilter, ...] = field(
        default_factory=tuple
    )

    @classmethod
    def all_of(cls, *conditions: FilterCondition | InCondition | NodeFilter) -> NodeFilter:
        return cls(conditions=tuple(conditions))

    @classmethod
    def from_spec(cls, spec: NodeFilterSpec) -> NodeFilter:
        return cls(conditions=tuple(spec.conditions))


def merge_filters(base: NodeFilter, extra: NodeFilter) -> NodeFilter:
    """AND two filters; ``NodeFilterSpec`` has no logic flag, so nesting is fine."""

    return NodeFilter(conditions=(base, extra))


def build_content_search_filters(
    labels: tuple[str, ...] | list[str],
    end_user_id: str,
) -> dict[str, NodeFilter]:
    """Build per-label filters for memory content retrieval (host search_policy)."""

    common = NodeFilter.all_of(
        FilterCondition(field="end_user_id", value=end_user_id),
        FilterCondition(field="delete_at", operator=FilterOperator.NOT_EXISTS),
    )
    filters: dict[str, NodeFilter] = {}
    for label in labels:
        if label == MemorySource.DIALOGUE.value:
            filters[label] = merge_filters(
                common,
                NodeFilter.all_of(
                    FilterCondition(field="write_mode", value=DIALOGUE_WRITE_MODE)
                ),
            )
        else:
            filters[label] = common
    return filters


def _escape_identifier(identifier: str) -> str:
    return identifier.replace("`", "``")


def compile_neo4j_filter(
    node_filter: NodeFilter,
    *,
    variable: str = "n",
    parameter_prefix: str = "filter",
) -> tuple[str, dict[str, Any]]:
    """Compile one filter into a Cypher predicate and its parameters."""

    parameters: dict[str, Any] = {}
    predicate = _compile_neo4j_group(
        node_filter,
        variable=variable,
        parameters=parameters,
        parameter_prefix=parameter_prefix,
    )
    return predicate, parameters


def _compile_neo4j_group(
    node_filter: NodeFilter,
    *,
    variable: str,
    parameters: dict[str, Any],
    parameter_prefix: str,
) -> str:
    predicates: list[str] = []
    for index, expression in enumerate(node_filter.conditions):
        prefix = f"{parameter_prefix}_{index}"
        if isinstance(expression, NodeFilter):
            predicate = _compile_neo4j_group(
                expression,
                variable=variable,
                parameters=parameters,
                parameter_prefix=prefix,
            )
        elif isinstance(expression, InCondition):
            parameter = f"{prefix}_ids"
            parameters[parameter] = list(expression.values)
            property_expression = f"{variable}.`{_escape_identifier(expression.field)}`"
            predicate = f"{property_expression} IN ${parameter}"
        else:
            parameter = f"{prefix}_value"
            property_expression = f"{variable}.`{_escape_identifier(expression.field)}`"
            predicate = _compile_neo4j_condition(
                expression,
                property_expression=property_expression,
                parameter=parameter,
                parameters=parameters,
            )
        predicates.append(f"({predicate})")
    return " AND ".join(predicates)


def _compile_neo4j_condition(
    condition: FilterCondition,
    *,
    property_expression: str,
    parameter: str,
    parameters: dict[str, Any],
) -> str:
    operator = condition.operator
    if operator == FilterOperator.EXISTS:
        return f"{property_expression} IS NOT NULL"
    if operator == FilterOperator.NOT_EXISTS:
        return f"{property_expression} IS NULL"
    if condition.value is None:
        if operator == FilterOperator.NOT_EQUALS:
            return f"{property_expression} IS NOT NULL"
        return f"{property_expression} IS NULL"
    parameters[parameter] = condition.value
    symbol = "<>" if operator == FilterOperator.NOT_EQUALS else "="
    return f"{property_expression} {symbol} ${parameter}"


def compile_elasticsearch_filter(node_filter: NodeFilter) -> dict[str, Any]:
    """Compile one filter into an Elasticsearch bool query (host compiler)."""

    clauses = [
        compile_elasticsearch_filter(expression)
        if isinstance(expression, NodeFilter)
        else _compile_elasticsearch_expression(expression)
        for expression in node_filter.conditions
    ]
    return {"bool": {"filter": clauses}}


def _compile_elasticsearch_expression(
    condition: FilterCondition | InCondition,
) -> dict[str, Any]:
    if isinstance(condition, InCondition):
        return {"terms": {condition.field: list(condition.values)}}
    field = condition.field
    operator = condition.operator
    value = condition.value
    if operator == FilterOperator.EXISTS:
        return {"exists": {"field": field}}
    if operator == FilterOperator.NOT_EXISTS:
        return {"bool": {"must_not": {"exists": {"field": field}}}}
    if operator == FilterOperator.NOT_EQUALS:
        if value is None:
            return {"exists": {"field": field}}
        return {
            "bool": {
                "filter": {"exists": {"field": field}},
                "must_not": {"term": {field: value}},
            }
        }
    if value is None:
        return {"bool": {"must_not": {"exists": {"field": field}}}}
    return {"term": {field: value}}


__all__ = [
    "InCondition",
    "NodeFilter",
    "build_content_search_filters",
    "compile_elasticsearch_filter",
    "compile_neo4j_filter",
    "merge_filters",
]