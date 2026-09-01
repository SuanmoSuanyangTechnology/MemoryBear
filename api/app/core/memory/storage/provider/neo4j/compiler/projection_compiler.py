from collections.abc import Mapping
from typing import Any

from app.core.memory.storage.enums import RelationshipScope
from app.core.memory.storage.models import (
    CoalesceProjectionField,
    NodeProjection,
    RelationshipProjection,
)
from app.core.memory.storage.models.projection import ProjectionItem


def compile_neo4j_projection(
    projection: NodeProjection | None,
    variable: str = "n",
    virtual_fields: Mapping[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    if projection is None:
        return variable, {}

    parameters: dict[str, Any] = {}
    virtual_fields = virtual_fields or {}
    property_selectors = ", ".join(
        _compile_field(field, variable, index, parameters, virtual_fields)
        for index, field in enumerate(projection.fields)
    )
    return f"{variable} {{ {property_selectors} }} AS {variable}", parameters


def _compile_field(
    field: ProjectionItem,
    variable: str,
    index: int,
    parameters: dict[str, Any],
    virtual_fields: Mapping[str, str],
) -> str:
    if isinstance(field, str):
        if field in virtual_fields:
            return f"`{_escape_identifier(field)}`: {virtual_fields[field]}"
        return f".`{_escape_identifier(field)}`"

    if isinstance(field, CoalesceProjectionField):
        arguments = [
            f"{variable}.`{_escape_identifier(source_field)}`"
            for source_field in field.fields
        ]
        if field.default is not None:
            parameter_name = f"projection_{index}_default"
            arguments.append(f"${parameter_name}")
            parameters[parameter_name] = field.default
        escaped_alias = _escape_identifier(field.alias)
        return f"`{escaped_alias}`: coalesce({', '.join(arguments)})"

    escaped_field = _escape_identifier(field.field)
    source = virtual_fields.get(field.field, f"{variable}.`{escaped_field}`")
    if field.alias is None:
        if field.field in virtual_fields:
            return f"`{escaped_field}`: {source}"
        return f".`{escaped_field}`"

    escaped_alias = _escape_identifier(field.alias)
    return f"`{escaped_alias}`: {source}"


def _escape_identifier(identifier: str) -> str:
    return identifier.replace("`", "``")


_RELATIONSHIP_SCOPE_VARIABLES: Mapping[RelationshipScope, str] = {
    RelationshipScope.SOURCE: "source",
    RelationshipScope.RELATIONSHIP: "r",
    RelationshipScope.TARGET: "target",
}


def compile_neo4j_relationship_projection(
    projection: RelationshipProjection | None,
) -> tuple[str, dict[str, Any]]:
    """Compile a relationship traversal projection into a flat map expression.

    Without a projection the full target node is returned. With a projection,
    each field is read from the source node, the relationship, or the target
    node and merged into a single map item.
    """
    if projection is None:
        return "target", {}

    selectors = ", ".join(
        f"`{_escape_identifier(field.output_name)}`: "
        f"{_RELATIONSHIP_SCOPE_VARIABLES[field.scope]}"
        f".`{_escape_identifier(field.field)}`"
        for field in projection.fields
    )
    return f"{{ {selectors} }} AS item", {}
