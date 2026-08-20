from typing import Any

from app.core.memory.storage.models import (
    FilterCondition,
    FilterLogic,
    FilterOperator,
    NodeFilter,
)


def compile_neo4j_filter(
    node_filter: NodeFilter,
    variable: str = "n",
) -> tuple[str, dict[str, Any]]:
    parameters: dict[str, Any] = {}
    predicate = _compile_group(
        node_filter,
        variable=variable,
        parameters=parameters,
        path=(),
    )
    return predicate, parameters


def _compile_group(
    node_filter: NodeFilter,
    *,
    variable: str,
    parameters: dict[str, Any],
    path: tuple[int, ...],
) -> str:
    predicates: list[str] = []

    for index, expression in enumerate(node_filter.conditions):
        expression_path = (*path, index)
        if isinstance(expression, NodeFilter):
            predicate = _compile_group(
                expression,
                variable=variable,
                parameters=parameters,
                path=expression_path,
            )
        else:
            parameter_prefix = "filter_" + "_".join(map(str, expression_path))
            field_parameter = f"{parameter_prefix}_field"
            value_parameter = f"{parameter_prefix}_value"
            property_expression = f"{variable}[${field_parameter}]"
            parameters[field_parameter] = expression.field
            predicate = _compile_condition(
                expression,
                property_expression=property_expression,
                value_parameter=value_parameter,
                parameters=parameters,
            )

        predicates.append(f"({predicate})")

    conjunction = " AND " if node_filter.logic == FilterLogic.AND else " OR "
    return conjunction.join(predicates)


def _compile_condition(
    condition: FilterCondition,
    *,
    property_expression: str,
    value_parameter: str,
    parameters: dict[str, Any],
) -> str:
    operator = condition.operator

    if operator == FilterOperator.EXISTS:
        return f"{property_expression} IS {'NOT ' if condition.value else ''}NULL"
    if condition.value is None and operator in {FilterOperator.EQ, FilterOperator.NE}:
        return f"{property_expression} IS {'NOT ' if operator == FilterOperator.NE else ''}NULL"

    if operator == FilterOperator.NOT_IN:
        parameters[value_parameter] = list(condition.value)
        return f"NOT {property_expression} IN ${value_parameter}"

    symbols = {
        FilterOperator.EQ: "=",
        FilterOperator.NE: "<>",
        FilterOperator.GT: ">",
        FilterOperator.GTE: ">=",
        FilterOperator.LT: "<",
        FilterOperator.LTE: "<=",
        FilterOperator.IN: "IN",
    }
    try:
        symbol = symbols[operator]
    except KeyError as exc:
        raise ValueError(f"unsupported Neo4j filter operator: {operator}") from exc

    value = condition.value
    if operator == FilterOperator.IN:
        value = list(value)
    parameters[value_parameter] = value
    return f"{property_expression} {symbol} ${value_parameter}"
