from collections.abc import Collection, Mapping
from typing import Any

from app.core.memory.storage.models import (
    CoalesceProjectionField,
    NodeProjection,
)


def compile_elasticsearch_projection(
    projection: NodeProjection | None,
    virtual_fields: Collection[str] | None = None,
) -> list[str] | None:
    if projection is None:
        return None

    virtual_fields = virtual_fields or ()
    source_fields: list[str] = []
    for item in projection.fields:
        if isinstance(item, str):
            if item not in virtual_fields:
                source_fields.append(item)
        elif isinstance(item, CoalesceProjectionField):
            source_fields.extend(item.fields)
        elif item.field not in virtual_fields:
            source_fields.append(item.field)
    return list(dict.fromkeys(source_fields))


def apply_elasticsearch_projection(
    source: Mapping[str, Any],
    projection: NodeProjection | None,
    virtual_fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if projection is None:
        return dict(source)

    virtual_fields = virtual_fields or {}
    result: dict[str, Any] = {}
    for item in projection.fields:
        if isinstance(item, str):
            if item in virtual_fields:
                result[item] = virtual_fields[item]
            elif item in source:
                result[item] = source[item]
            continue

        if isinstance(item, CoalesceProjectionField):
            result[item.alias] = next(
                (
                    source[field]
                    for field in item.fields
                    if source.get(field) is not None
                ),
                item.default,
            )
            continue

        if item.field in virtual_fields:
            result[item.output_name] = virtual_fields[item.field]
        elif item.field in source:
            result[item.output_name] = source[item.field]
    return result
