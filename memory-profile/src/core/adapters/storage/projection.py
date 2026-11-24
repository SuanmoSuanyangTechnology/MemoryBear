"""Node projections for the read paths.

Ported from ``api/app/core/memory/storage/models/projection.py`` plus the Neo4j
and Elasticsearch projection compilers. A projection is an ordered tuple of
fields; the provider compiler turns it into a Cypher map projection or an ES
``_source`` include list. ``score`` is a *virtual* field: the provider query
supplies it (full-text index score, cosine similarity or ES ``_score``) instead
of reading it from the stored node.

The default field lists are not hand-copied: they are rebuilt from
``storage_index_contract.json`` (generated from the API source), so a writer-side
projection change is caught by the contract check instead of silently returning
different fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.core.adapters.storage.indexes import DEFAULT_PROJECTION_SPECS

VIRTUAL_SCORE_FIELD = "score"


@dataclass(frozen=True, slots=True)
class ProjectionField:
    """One stored field, optionally renamed."""

    field: str
    alias: str | None = None

    @property
    def output_name(self) -> str:
        return self.alias or self.field


@dataclass(frozen=True, slots=True)
class CoalesceField:
    """First non-null source field; the alias is always emitted."""

    fields: tuple[str, ...]
    alias: str
    default: Any = None

    @property
    def output_name(self) -> str:
        return self.alias


ProjectionItem = str | ProjectionField | CoalesceField
NodeProjection = tuple[ProjectionItem, ...]


def projection_of(*fields: ProjectionItem) -> NodeProjection:
    return tuple(fields)


def _projection_item(spec: dict[str, Any]) -> ProjectionItem:
    """Rebuild one projection item from the generated contract spec."""

    coalesce = spec.get("coalesce")
    if coalesce is not None:
        return CoalesceField(
            fields=tuple(str(field) for field in coalesce),
            alias=str(spec["alias"]),
            default=spec.get("default"),
        )
    field = str(spec["field"])
    alias = spec.get("alias")
    if alias:
        return ProjectionField(field=field, alias=str(alias))
    return field


#: Host ``DEFAULT_PROJECTION``: the exact field set each read family returns.
#: Generated from the API source into ``storage_index_contract.json``; this module
#: only rebuilds the typed tuples, so the field list cannot drift from the writer.
DEFAULT_PROJECTIONS: dict[str, NodeProjection] = {
    label: tuple(_projection_item(spec) for spec in specs)
    for label, specs in DEFAULT_PROJECTION_SPECS.items()
}

def projection_for(label: str) -> NodeProjection:
    """Return the default projection of one node family."""

    try:
        return DEFAULT_PROJECTIONS[label]
    except KeyError:
        raise KeyError(f"no default projection for node type {label!r}") from None


def score_output_names(projection: NodeProjection | None) -> list[str]:
    """Output names that carry the virtual score, if the projection requests it."""

    if projection is None:
        return []
    names: list[str] = []
    for item in projection:
        if isinstance(item, str):
            if item == VIRTUAL_SCORE_FIELD:
                names.append(item)
        elif isinstance(item, ProjectionField) and item.field == VIRTUAL_SCORE_FIELD:
            names.append(item.output_name)
    return names


def _escape_identifier(identifier: str) -> str:
    return identifier.replace("`", "``")


def compile_neo4j_projection(
    projection: NodeProjection | None,
    *,
    variable: str = "n",
    virtual_fields: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Compile a node projection into a Cypher map expression."""

    if projection is None:
        return variable, {}
    parameters: dict[str, Any] = {}
    virtual_fields = virtual_fields or {}
    selectors = ", ".join(
        _compile_neo4j_field(item, variable, index, parameters, virtual_fields)
        for index, item in enumerate(projection)
    )
    return f"{variable} {{ {selectors} }} AS {variable}", parameters


def _compile_neo4j_field(
    item: ProjectionItem,
    variable: str,
    index: int,
    parameters: dict[str, Any],
    virtual_fields: dict[str, str],
) -> str:
    if isinstance(item, str):
        if item in virtual_fields:
            return f"`{_escape_identifier(item)}`: {virtual_fields[item]}"
        return f".`{_escape_identifier(item)}`"

    if isinstance(item, CoalesceField):
        arguments = [
            f"{variable}.`{_escape_identifier(source_field)}`"
            for source_field in item.fields
        ]
        if item.default is not None:
            parameter_name = f"projection_{index}_default"
            arguments.append(f"${parameter_name}")
            parameters[parameter_name] = item.default
        escaped_alias = _escape_identifier(item.alias)
        return f"`{escaped_alias}`: coalesce({', '.join(arguments)})"

    escaped_field = _escape_identifier(item.field)
    source = virtual_fields.get(item.field, f"{variable}.`{escaped_field}`")
    if item.alias is None:
        if item.field in virtual_fields:
            return f"`{escaped_field}`: {source}"
        return f".`{escaped_field}`"
    return f"`{_escape_identifier(item.alias)}`: {source}"


def compile_elasticsearch_projection(
    projection: NodeProjection | None,
    *,
    virtual_fields: set[str] | None = None,
) -> list[str] | None:
    """Return the ``_source`` includes for an ES search (``None`` = all fields)."""

    if projection is None:
        return None
    virtual_fields = virtual_fields or set()
    source_fields: list[str] = []
    for item in projection:
        if isinstance(item, str):
            if item not in virtual_fields:
                source_fields.append(item)
        elif isinstance(item, CoalesceField):
            source_fields.extend(item.fields)
        elif item.field not in virtual_fields:
            source_fields.append(item.field)
    return list(dict.fromkeys(source_fields))


def apply_elasticsearch_projection(
    source: dict[str, Any],
    projection: NodeProjection | None,
    *,
    virtual_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Trim one ES ``_source`` document to the projection (host compiler)."""

    if projection is None:
        return dict(source)
    virtual_fields = virtual_fields or {}
    result: dict[str, Any] = {}
    for item in projection:
        if isinstance(item, str):
            if item in virtual_fields:
                result[item] = virtual_fields[item]
            elif item in source:
                result[item] = source[item]
            continue
        if isinstance(item, CoalesceField):
            result[item.alias] = next(
                (
                    source[source_field]
                    for source_field in item.fields
                    if source.get(source_field) is not None
                ),
                item.default,
            )
            continue
        if item.field in virtual_fields:
            result[item.output_name] = virtual_fields[item.field]
        elif item.field in source:
            result[item.output_name] = source[item.field]
    return result


@dataclass(frozen=True, slots=True)
class RelationshipProjectionField:
    """One output field read from the source node, the edge, or the target node."""

    field: str
    scope: str = "target"
    alias: str | None = None

    @property
    def output_name(self) -> str:
        return self.alias or self.field


_SCOPE_VARIABLES: dict[str, str] = {
    "source": "source",
    "relationship": "r",
    "target": "target",
}


def compile_neo4j_relationship_projection(
    projection: tuple[RelationshipProjectionField, ...] | None,
) -> str:
    """Compile a flat relationship projection into ``{...} AS item``."""

    if projection is None:
        return "target AS item"
    selectors = ", ".join(
        f"`{_escape_identifier(item.output_name)}`: "
        f"{_SCOPE_VARIABLES[item.scope]}.`{_escape_identifier(item.field)}`"
        for item in projection
    )
    return f"{{ {selectors} }} AS item"


__all__ = [
    "CoalesceField",
    "DEFAULT_PROJECTIONS",
    "NodeProjection",
    "ProjectionField",
    "ProjectionItem",
    "RelationshipProjectionField",
    "VIRTUAL_SCORE_FIELD",
    "apply_elasticsearch_projection",
    "compile_elasticsearch_projection",
    "compile_neo4j_projection",
    "compile_neo4j_relationship_projection",
    "projection_for",
    "projection_of",
    "score_output_names",
]