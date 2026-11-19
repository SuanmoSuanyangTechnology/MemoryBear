"""Bridge between the core read DSL and the ported host storage DSL.

``src.core`` speaks its own small DSL (dataclass filters/projections, plain
string labels, plain string backends, :mod:`src.core.adapters.storage.results`).
The ported storage layer under :mod:`src.storage` speaks the host's pydantic DSL
(``FilterOperator.EQ``/``EXISTS``, ``NodeProjection(fields=...)``, ``MemoryNodeType``
labels, ``BackendType`` backends). This module is the only place that knows both
vocabularies; keeping the mapping here means neither side has to mirror the
other's spelling.

Operator correspondence (the compilers of both sides already agree on semantics):

=====================  ============================================
core                   ported
=====================  ============================================
``EQUALS(value)``      ``FilterCondition(operator=EQ, value)``
``NOT_EQUALS(value)``  ``FilterCondition(operator=NE, value)``
``EXISTS``             ``FilterCondition(operator=EXISTS, value=True)``
``NOT_EXISTS``         ``FilterCondition(operator=EXISTS, value=False)``
``InCondition``        ``FilterCondition(operator=IN, value=tuple)``
=====================  ============================================

``EQUALS(None)`` / ``NOT_EQUALS(None)`` keep their "IS NULL"/"IS NOT NULL"
meaning: both compilers special-case a ``None`` value for ``EQ``/``NE``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.core.adapters.storage.filters import (
    FilterCondition as CoreCondition,
)
from src.core.adapters.storage.filters import (
    FilterOperator as CoreOperator,
)
from src.core.adapters.storage.filters import (
    InCondition as CoreInCondition,
)
from src.core.adapters.storage.filters import (
    NodeFilter as CoreNodeFilter,
)
from src.core.adapters.storage.filters import (
    NodeFilterSpec as CoreNodeFilterSpec,
)
from src.core.adapters.storage.projection import (
    CoalesceField as CoreCoalesceField,
)
from src.core.adapters.storage.projection import (
    ProjectionField as CoreProjectionField,
)
from src.core.adapters.storage.projection import (
    RelationshipProjectionField as CoreRelationshipProjectionField,
)
from src.core.adapters.storage.results import (
    StorageItem as CoreStorageItem,
)
from src.core.adapters.storage.results import (
    StorageReadResult as CoreStorageReadResult,
)
from src.storage.enums import (
    BackendType,
    MemoryNodeType,
    RelationshipScope,
)
from src.storage.models import (
    CoalesceProjectionField,
    FilterCondition,
    FilterLogic,
    FilterOperator,
    NodeFilter,
    NodeProjection,
    ProjectionField,
    RelationshipFilter,
    RelationshipPattern,
    RelationshipProjection,
    RelationshipProjectionField,
    StorageReadResult,
)

_OPERATORS: dict[CoreOperator, FilterOperator] = {
    CoreOperator.EQUALS: FilterOperator.EQ,
    CoreOperator.NOT_EQUALS: FilterOperator.NE,
}


# ────────────────────────────────────────────────────────────────
# Labels / backends
# ────────────────────────────────────────────────────────────────


def to_backend_label(label: str) -> MemoryNodeType | None:
    """Map a core label to the host label enum; ``None`` when it is not one.

    The core DSL passes plain strings (``MemorySource`` values, plus the
    ``HISTORY`` pseudo source). Only real node families exist in the ported
    registry, so an unknown string is reported as "not a storage label" and the
    caller answers with an empty result — exactly what the old adapter did for a
    label without an index.
    """

    try:
        return MemoryNodeType(label)
    except ValueError:
        return None


def to_core_backend(backend: BackendType | None) -> str | None:
    """``BackendType.ELASTIC`` -> ``"elastic"`` (the string the read engine reads)."""

    return backend.value.lower() if backend is not None else None


# ────────────────────────────────────────────────────────────────
# Filters
# ────────────────────────────────────────────────────────────────


def _to_backend_condition(condition: CoreCondition) -> FilterCondition:
    operator = condition.operator
    if operator == CoreOperator.EXISTS:
        return FilterCondition(
            field=condition.field, operator=FilterOperator.EXISTS, value=True
        )
    if operator == CoreOperator.NOT_EXISTS:
        return FilterCondition(
            field=condition.field, operator=FilterOperator.EXISTS, value=False
        )
    try:
        mapped = _OPERATORS[operator]
    except KeyError:
        raise ValueError(f"unsupported core filter operator: {operator}") from None
    return FilterCondition(field=condition.field, operator=mapped, value=condition.value)


def to_backend_filter(node_filter: Any) -> NodeFilter:
    """Convert a core :class:`NodeFilter`/``NodeFilterSpec`` (AND groups)."""

    if isinstance(node_filter, CoreNodeFilterSpec):
        conditions: Sequence[Any] = node_filter.conditions
    elif isinstance(node_filter, CoreNodeFilter):
        conditions = node_filter.conditions
    else:  # already a ported filter
        return node_filter
    converted = []
    for entry in conditions:
        if isinstance(entry, CoreInCondition):
            converted.append(
                FilterCondition(
                    field=entry.field,
                    operator=FilterOperator.IN,
                    value=list(entry.values),
                )
            )
        elif isinstance(entry, CoreCondition):
            converted.append(_to_backend_condition(entry))
        elif isinstance(entry, (CoreNodeFilter, CoreNodeFilterSpec)):
            converted.append(to_backend_filter(entry))
        else:
            raise ValueError(f"unsupported core filter condition: {type(entry).__name__}")
    return NodeFilter(logic=FilterLogic.AND, conditions=tuple(converted))


def to_backend_relationship_filter(rel_filter: Any) -> RelationshipFilter | None:
    """Convert the core scoped relationship filter; ``None`` = no constraint."""

    if rel_filter is None:
        return None
    if isinstance(rel_filter, RelationshipFilter):
        return rel_filter
    source = getattr(rel_filter, "source", None)
    target = getattr(rel_filter, "target", None)
    relationship = getattr(rel_filter, "relationship", None)
    return RelationshipFilter(
        source=to_backend_filter(source) if source is not None else None,
        target=to_backend_filter(target) if target is not None else None,
        relationship=(
            to_backend_filter(relationship) if relationship is not None else None
        ),
    )


# ────────────────────────────────────────────────────────────────
# Projections
# ────────────────────────────────────────────────────────────────


def _to_backend_projection_item(item: Any) -> Any:
    if isinstance(item, str):
        return item
    if isinstance(item, CoreCoalesceField):
        return CoalesceProjectionField(
            fields=item.fields,
            alias=item.alias,
            default=item.default,
        )
    if isinstance(item, CoreProjectionField):
        return ProjectionField(field=item.field, alias=item.alias)
    if isinstance(item, (ProjectionField, CoalesceProjectionField)):
        return item
    raise ValueError(f"unsupported core projection item: {type(item).__name__}")


def to_backend_projection(projection: Any) -> NodeProjection | None:
    """Convert a core projection tuple into the host ``NodeProjection`` model."""

    if projection is None:
        return None
    if isinstance(projection, NodeProjection):
        return projection
    fields = tuple(_to_backend_projection_item(item) for item in projection)
    return NodeProjection(fields=fields)


def to_backend_relationship_projection(
    projection: Sequence[Any] | None,
) -> RelationshipProjection | None:
    """Convert the core flat relationship projection (scope strings -> enum)."""

    if projection is None:
        return None
    if isinstance(projection, RelationshipProjection):
        return projection
    fields: list[RelationshipProjectionField] = []
    for item in projection:
        if isinstance(item, RelationshipProjectionField):
            fields.append(item)
            continue
        if not isinstance(item, CoreRelationshipProjectionField):
            raise ValueError(
                f"unsupported core relationship projection item: {type(item).__name__}"
            )
        fields.append(
            RelationshipProjectionField(
                scope=RelationshipScope(item.scope),
                field=item.field,
                alias=item.alias,
            )
        )
    return RelationshipProjection(fields=tuple(fields))


# ────────────────────────────────────────────────────────────────
# Relationship patterns
# ────────────────────────────────────────────────────────────────


def to_backend_relationship_pattern(pattern: Any) -> RelationshipPattern:
    """Convert the core pattern dataclass into the host pattern model.

    The ported model requires real ``MemoryNodeLabel`` instances (its validator
    uses ``is_instance_of``), so the string labels of the core DSL are resolved
    here; an unknown label is a caller error, not an empty result.
    """

    if isinstance(pattern, RelationshipPattern):
        return pattern
    source_label = getattr(pattern, "source_label", None)
    target_label = getattr(pattern, "target_label", None)
    relationship_type = getattr(pattern, "relationship_type", None)
    converted_source = None
    converted_target = None
    if source_label is not None:
        converted_source = to_backend_label(str(source_label))
        if converted_source is None:
            raise ValueError(f"unknown source label {source_label!r}")
    if target_label is not None:
        converted_target = to_backend_label(str(target_label))
        if converted_target is None:
            raise ValueError(f"unknown target label {target_label!r}")
    return RelationshipPattern(
        source_label=converted_source,
        target_label=converted_target,
        relationship_type=relationship_type,
        directed=bool(getattr(pattern, "directed", True)),
    )


# ────────────────────────────────────────────────────────────────
# Results
# ────────────────────────────────────────────────────────────────


def to_core_result(result: StorageReadResult) -> CoreStorageReadResult:
    """Convert one ported read result into the core result value object."""

    items: list[CoreStorageItem] = []
    for item in result.items:
        items.append(
            CoreStorageItem(
                label=item.label.value if item.label is not None else "",
                data=dict(item.data),
            )
        )
    return CoreStorageReadResult(
        items=items,
        backend=to_core_backend(result.backend),
        total=result.total if result.total else len(items),
    )


__all__ = [
    "to_backend_filter",
    "to_backend_label",
    "to_backend_projection",
    "to_backend_relationship_filter",
    "to_backend_relationship_pattern",
    "to_backend_relationship_projection",
    "to_core_backend",
    "to_core_result",
]
