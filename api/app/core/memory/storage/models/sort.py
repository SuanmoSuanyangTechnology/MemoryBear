from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SortDirection(StrEnum):
    ASC = "ASC"
    DESC = "DESC"


class SortField(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: str = Field(min_length=1)
    direction: SortDirection = SortDirection.ASC

    @field_validator("field")
    @classmethod
    def validate_field(cls, field: str) -> str:
        if not field.strip():
            raise ValueError("sort field cannot be blank")
        return field


class NodeSort(BaseModel):
    model_config = ConfigDict(frozen=True)

    fields: tuple[SortField, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_fields(self) -> Self:
        field_names = [sort_field.field for sort_field in self.fields]
        if len(field_names) != len(set(field_names)):
            raise ValueError("sort fields cannot contain duplicates")
        return self

    @classmethod
    def asc(cls, *fields: str) -> Self:
        return cls(
            fields=tuple(
                SortField(field=field, direction=SortDirection.ASC)
                for field in fields
            )
        )

    @classmethod
    def desc(cls, *fields: str) -> Self:
        return cls(
            fields=tuple(
                SortField(field=field, direction=SortDirection.DESC)
                for field in fields
            )
        )
