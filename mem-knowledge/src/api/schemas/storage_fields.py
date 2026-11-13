"""Request fields constrained by the existing persisted representation."""

from typing import Annotated, TypeVar

from pydantic import AfterValidator, Field
from pydantic.json_schema import SkipJsonSchema

INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1
DatabaseInt = Annotated[int, Field(ge=INT32_MIN, le=INT32_MAX)]

T = TypeVar("T")


def _reject_null[T](value: T | None) -> T:
    if value is None:
        raise ValueError("value cannot be null when supplied")
    return value


NonNullUpdate = Annotated[
    T | SkipJsonSchema[None],
    AfterValidator(_reject_null),
    Field(default_factory=lambda: None),
]
