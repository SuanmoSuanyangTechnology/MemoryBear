"""Check inputs that would fail when written or returned, after legacy business checks."""

import math
import re
from collections.abc import Iterable, Mapping
from typing import Any

from ..errors import KnowledgeError

INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1


def _invalid_field(field: str, reason: str) -> KnowledgeError:
    return KnowledgeError.from_code(
        "KB_HTTP_VALIDATION_ERROR", diagnostic=f"body.{field}: {reason}"
    )


def validate_write_fields(
    values: Mapping[str, Any],
    *,
    integers: Iterable[str] = (),
    non_nullable: Iterable[str] = (),
    finite: Iterable[str] = (),
) -> None:
    for field in non_nullable:
        if field in values and values[field] is None:
            raise _invalid_field(field, "value cannot be null when supplied")
    for field in integers:
        value = values.get(field)
        if value is not None and not INT32_MIN <= value <= INT32_MAX:
            raise _invalid_field(field, "integer is outside the supported range")
    for field in finite:
        value = values.get(field)
        if value is not None and not math.isfinite(value):
            raise _invalid_field(field, "number must be finite")


def validate_file_size_text(value: str) -> None:
    if not re.fullmatch(r"[+-]?[0-9]+", value.strip()):
        raise _invalid_field("file_size", "file size must be an integer")
    try:
        number = int(value)
    except ValueError as exc:
        raise _invalid_field("file_size", "file size exceeds the supported range") from exc
    validate_write_fields({"file_size": number}, integers=("file_size",))
