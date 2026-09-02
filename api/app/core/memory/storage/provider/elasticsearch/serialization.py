import math
from collections.abc import Mapping
from datetime import date, datetime, time, timezone
from numbers import Integral, Real
from typing import Any


def normalize_elasticsearch_value(value: Any) -> Any:
    """Convert a value into the JSON-compatible form used by Elasticsearch."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if hasattr(value, "to_native"):
        return normalize_elasticsearch_value(value.to_native())
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
            key: normalize_elasticsearch_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [normalize_elasticsearch_value(item) for item in value]
    raise ValueError("Unsupported Elasticsearch document value")
