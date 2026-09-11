from typing import Any

from app.core.memory.storage.enums import RelationshipScope
from app.core.memory.storage.models import (
    NodeSort,
    RelationshipSort,
)


def compile_neo4j_sort(
    node_sort: NodeSort | None,
    variable: str = "n",
) -> tuple[str, dict[str, Any]]:
    if node_sort is None:
        return "", {}

    expressions: list[str] = []
    parameters: dict[str, Any] = {}

    for index, sort_field in enumerate(node_sort.fields):
        field_parameter = f"sort_{index}_field"
        parameters[field_parameter] = sort_field.field
        expressions.append(
            f"{variable}[${field_parameter}] {sort_field.direction.value}"
        )

    return f"ORDER BY {', '.join(expressions)}", parameters


_RELATIONSHIP_SCOPE_VARIABLES = {
    RelationshipScope.SOURCE: "source",
    RelationshipScope.RELATIONSHIP: "r",
    RelationshipScope.TARGET: "target",
}


def compile_neo4j_relationship_sort(
    relationship_sort: RelationshipSort | None,
) -> tuple[str, dict[str, Any]]:
    """Compile sorting across relationship traversal scopes."""
    if relationship_sort is None:
        return "", {}

    expressions: list[str] = []
    parameters: dict[str, Any] = {}
    for index, sort_field in enumerate(relationship_sort.fields):
        field_parameter = f"sort_{index}_field"
        parameters[field_parameter] = sort_field.field
        variable = _RELATIONSHIP_SCOPE_VARIABLES[sort_field.scope]
        expressions.append(
            f"{variable}[${field_parameter}] {sort_field.direction.value}"
        )

    return f"ORDER BY {', '.join(expressions)}", parameters
