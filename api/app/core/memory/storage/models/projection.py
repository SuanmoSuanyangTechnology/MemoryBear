from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.memory.storage.enums import MemoryNodeType


class ProjectionField(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: str = Field(min_length=1)
    alias: str | None = None

    @field_validator("field")
    @classmethod
    def validate_field(cls, field: str) -> str:
        if not field.strip():
            raise ValueError("projection field cannot be blank")
        return field

    @field_validator("alias")
    @classmethod
    def validate_alias(cls, alias: str | None) -> str | None:
        if alias is not None and not alias.strip():
            raise ValueError("projection field alias cannot be blank")
        return alias

    @property
    def output_name(self) -> str:
        return self.alias or self.field


class CoalesceProjectionField(BaseModel):
    model_config = ConfigDict(frozen=True)

    fields: tuple[str, ...] = Field(min_length=1)
    alias: str = Field(min_length=1)
    default: Any = None

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, fields: tuple[str, ...]) -> tuple[str, ...]:
        if any(not field.strip() for field in fields):
            raise ValueError("coalesce projection fields cannot be blank")
        if len(fields) != len(set(fields)):
            raise ValueError("coalesce projection fields cannot contain duplicates")
        return fields

    @field_validator("alias")
    @classmethod
    def validate_alias(cls, alias: str) -> str:
        if not alias.strip():
            raise ValueError("coalesce projection alias cannot be blank")
        return alias

    @property
    def output_name(self) -> str:
        return self.alias


ProjectionItem = str | ProjectionField | CoalesceProjectionField


class NodeProjection(BaseModel):
    model_config = ConfigDict(frozen=True)

    fields: tuple[ProjectionItem, ...] = Field(min_length=1)

    @field_validator("fields")
    @classmethod
    def validate_string_fields(
            cls,
            fields: tuple[ProjectionItem, ...],
    ) -> tuple[ProjectionItem, ...]:
        if any(isinstance(field, str) and not field.strip() for field in fields):
            raise ValueError("projection fields cannot be blank")
        return fields

    @model_validator(mode="after")
    def validate_unique_fields_and_outputs(self) -> Self:
        direct_field_names: list[str] = []
        for item in self.fields:
            if isinstance(item, str):
                direct_field_names.append(item)
            elif isinstance(item, ProjectionField):
                direct_field_names.append(item.field)
        if len(direct_field_names) != len(set(direct_field_names)):
            raise ValueError("projection fields cannot contain duplicates")

        output_names = [
            item if isinstance(item, str) else item.output_name
            for item in self.fields
        ]
        if len(output_names) != len(set(output_names)):
            raise ValueError("projection output names cannot contain duplicates")
        return self

    @classmethod
    def of(cls, *fields: ProjectionItem) -> Self:
        return cls(fields=fields)


class RelationshipProjectionScope(StrEnum):
    """Graph element a relationship-projection field is read from."""

    SOURCE = "source"
    RELATIONSHIP = "relationship"
    TARGET = "target"


class RelationshipProjectionField(BaseModel):
    """A single output field read from one endpoint or the relationship."""

    model_config = ConfigDict(frozen=True)

    scope: RelationshipProjectionScope = RelationshipProjectionScope.TARGET
    field: str = Field(min_length=1)
    alias: str | None = None

    @field_validator("field")
    @classmethod
    def validate_field(cls, field: str) -> str:
        if not field.strip():
            raise ValueError("relationship projection field cannot be blank")
        return field

    @field_validator("alias")
    @classmethod
    def validate_alias(cls, alias: str | None) -> str | None:
        if alias is not None and not alias.strip():
            raise ValueError("relationship projection field alias cannot be blank")
        return alias

    @property
    def output_name(self) -> str:
        return self.alias or self.field


class RelationshipProjection(BaseModel):
    """Flat projection over a relationship traversal.

    Each field is read from the source node, the relationship itself, or
    the target node, and all selected fields are merged into one result item.
    """

    model_config = ConfigDict(frozen=True)

    fields: tuple[RelationshipProjectionField, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_output_names(self) -> Self:
        output_names = [field.output_name for field in self.fields]
        if len(output_names) != len(set(output_names)):
            raise ValueError(
                "relationship projection output names cannot contain duplicates"
            )
        return self

    @classmethod
    def of(cls, *fields: RelationshipProjectionField) -> Self:
        return cls(fields=fields)


DEFAULT_PROJECTION = {
    MemoryNodeType.EXTRACTED_ENTITY: NodeProjection(
        fields=(
            "id", "name", CoalesceProjectionField(fields=("aliases",), default=[], alias="aliases"),
            "description", "description_summary", "event_timeline", "created_at", "score"
        )
    ),
    MemoryNodeType.CHUNK: NodeProjection(
        fields=("id", "content", "created_at", "score")
    ),
    MemoryNodeType.STATEMENT: NodeProjection(
        fields=("id", "statement", "created_at", "score")
    ),
    MemoryNodeType.MEMORY_SUMMARY: NodeProjection(
        fields=("id", "name", "content", "created_at", "score")
    ),
    MemoryNodeType.PERCEPTUAL: NodeProjection(
        fields=(
            "id", "perceptual_type", "file_path", "file_name", "file_ext", "summary",
            "keywords", "topic", "domain", "created_at", "file_type", "score"
        )
    ),
    MemoryNodeType.COMMUNITY: NodeProjection(
        fields=(
            ProjectionField(field="community_id", alias="id"), "name",
            ProjectionField(field="summary"), "core_entities", "updated_at", "score"
        )
    ),
    MemoryNodeType.DIALOGUE: NodeProjection(
        fields=("id", "content", "created_at", "score")
    )
}
