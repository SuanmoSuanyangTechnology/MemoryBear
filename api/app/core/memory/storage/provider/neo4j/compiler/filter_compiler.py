from typing import Any

from app.core.memory.storage.models import (
    FilterCondition,
    FilterLogic,
    FilterOperator,
    NodeFilter,
    RelationshipFilter,
    RelationshipFilterScope,
)


_RELATIONSHIP_VARIABLES = {
    RelationshipFilterScope.SOURCE: "source",
    RelationshipFilterScope.RELATIONSHIP: "r",
    RelationshipFilterScope.TARGET: "target",
}


def compile_neo4j_filter(
    node_filter: NodeFilter,
    variable: str = "n",
    parameter_prefix: str = "filter",
) -> tuple[str, dict[str, Any]]:
    parameters: dict[str, Any] = {}
    predicate = _compile_group(
        node_filter,
        variable=variable,
        parameters=parameters,
        path=(),
        parameter_prefix=parameter_prefix,
    )
    return predicate, parameters


def compile_neo4j_relationship_filter(
    relationship_filter: RelationshipFilter,
    parameter_prefix: str = "relationship_filter",
) -> tuple[str, dict[str, Any]]:
    """将关系过滤树编译为 Cypher WHERE 谓词及其参数字典。"""
    parameters: dict[str, Any] = {}
    predicate = _compile_relationship_group(
        relationship_filter,
        parameters=parameters,
        path=(),
        parameter_prefix=parameter_prefix,
    )
    return predicate, parameters


def _compile_relationship_group(
    relationship_filter: RelationshipFilter,
    *,
    parameters: dict[str, Any],
    path: tuple[int, ...],
    parameter_prefix: str,
) -> str:
    """递归编译一个关系过滤分组，并将叶子参数汇总到共享参数字典。"""
    predicates: list[str] = []

    for index, expression in enumerate(relationship_filter.conditions):
        expression_path = (*path, index)
        if isinstance(expression, RelationshipFilter):
            predicate = _compile_relationship_group(
                expression,
                parameters=parameters,
                path=expression_path,
                parameter_prefix=parameter_prefix,
            )
        else:
            scope_path = "_".join(map(str, expression_path))
            scope_prefix = (
                f"{parameter_prefix}_{scope_path}_{expression.scope.value}"
            )
            predicate, scoped_parameters = compile_neo4j_filter(
                expression.node_filter,
                variable=_RELATIONSHIP_VARIABLES[expression.scope],
                parameter_prefix=scope_prefix,
            )
            parameters.update(scoped_parameters)

        predicates.append(f"({predicate})")

    conjunction = (
        " AND "
        if relationship_filter.logic == FilterLogic.AND
        else " OR "
    )
    return conjunction.join(predicates)


def _compile_group(
    node_filter: NodeFilter,
    *,
    variable: str,
    parameters: dict[str, Any],
    path: tuple[int, ...],
    parameter_prefix: str,
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
                parameter_prefix=parameter_prefix,
            )
        else:
            condition_prefix = (
                f"{parameter_prefix}_" + "_".join(map(str, expression_path))
            )
            field_parameter = f"{condition_prefix}_field"
            value_parameter = f"{condition_prefix}_value"
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
