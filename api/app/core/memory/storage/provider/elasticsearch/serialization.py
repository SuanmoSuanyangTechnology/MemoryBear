import math
from collections.abc import Collection, Mapping
from datetime import date, datetime, time, timezone
from numbers import Integral, Real
from typing import Any


def _normalize_elasticsearch_value(value: Any) -> Any:
    """Convert a value into the JSON-compatible form used by Elasticsearch."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if hasattr(value, "to_native"):
        return _normalize_elasticsearch_value(value.to_native())
    if isinstance(value, datetime):
        aware = (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value
        )
        return aware.astimezone(timezone.utc).isoformat().replace(
            "+00:00",
            "Z",
        )
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real) and math.isfinite(value):
        return float(value)
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("Elasticsearch document keys must be strings")
        return {
            key: _normalize_elasticsearch_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_elasticsearch_value(item) for item in value]
    raise ValueError("Unsupported Elasticsearch document value")


def normalize_elasticsearch_document(
        value: Mapping[str, Any],
        *,
        date_fields: Collection[str],
) -> dict[str, Any]:
    """Normalize one document and clear blank values for mapped date fields."""
    document = _normalize_elasticsearch_value(value)
    if not isinstance(document, dict):
        raise ValueError("Elasticsearch document must be a mapping")
    return {
        field: (
            None
            if field in date_fields
            and isinstance(item, str)
            and not item.strip()
            else item
        )
        for field, item in document.items()
    }
