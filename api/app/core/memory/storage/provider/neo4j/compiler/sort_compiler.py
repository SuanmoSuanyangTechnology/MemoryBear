from typing import Any

from app.core.memory.storage.models import NodeSort


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
