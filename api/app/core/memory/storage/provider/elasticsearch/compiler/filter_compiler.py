from typing import Any

from app.core.memory.storage.models import (
    FilterCondition,
    FilterLogic,
    FilterOperator,
    NodeFilter,
)


def compile_elasticsearch_filter(node_filter: NodeFilter) -> dict[str, Any]:
    return _compile_group(node_filter)


def _compile_group(node_filter: NodeFilter) -> dict[str, Any]:
    clauses = [
        _compile_group(expression)
        if isinstance(expression, NodeFilter)
        else _compile_condition(expression)
        for expression in node_filter.conditions
    ]
    if node_filter.logic == FilterLogic.AND:
        return {"bool": {"filter": clauses}}
    return {"bool": {"should": clauses, "minimum_should_match": 1}}


def _compile_condition(condition: FilterCondition) -> dict[str, Any]:
    field = condition.field
    operator = condition.operator
    value = condition.value

    if operator == FilterOperator.EQ:
        if value is None:
            return {"bool": {"must_not": {"exists": {"field": field}}}}
        return {"term": {field: value}}
    if operator == FilterOperator.NE:
        if value is None:
            return {"exists": {"field": field}}
        return {
            "bool": {
                "filter": {"exists": {"field": field}},
                "must_not": {"term": {field: value}},
            }
        }
    if operator in {
        FilterOperator.GT,
        FilterOperator.GTE,
        FilterOperator.LT,
        FilterOperator.LTE,
    }:
        return {"range": {field: {operator.value: value}}}
    if operator == FilterOperator.IN:
        return {"terms": {field: list(value)}}
    if operator == FilterOperator.NOT_IN:
        return {
            "bool": {
                "filter": {"exists": {"field": field}},
                "must_not": {"terms": {field: list(value)}},
            }
        }
    if operator == FilterOperator.EXISTS:
        clause = {"exists": {"field": field}}
        return clause if value else {"bool": {"must_not": clause}}

    raise ValueError(f"unsupported Elasticsearch filter operator: {operator}")
