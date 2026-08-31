"""Knowledge metadata schemas copied from the legacy API."""

import datetime
import uuid
from enum import StrEnum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
)

from ...utils.datetime_utils import to_timestamp_ms

FIELD_NAME_PATTERN = r"^[a-z][a-z0-9_]*$"


class MetadataFieldType(StrEnum):
    STRING = "string"
    NUMBER = "number"
    TIME = "time"


class FilterCondition(BaseModel):
    field: str = Field(..., description="Metadata field name")
    operator: str = Field(..., description="Operator")
    value: Any | None = Field(None, description="Value")
    value_type: str | None = Field("constant", description="constant or variable")


class GroupLogic(StrEnum):
    AND = "and"
    OR = "or"


class FilterGroup(BaseModel):
    conditions: list[FilterCondition] = Field(..., description="Conditions")
    logic: GroupLogic = Field(GroupLogic.AND, description="and or or")

    @field_validator("logic", mode="before")
    @classmethod
    def normalize_logic(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value


class MetadataFilterMode(StrEnum):
    DISABLED = "disabled"
    MANUAL = "manual"
    AUTO = "auto"


class KnowledgeMetadataCreate(BaseModel):
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        pattern=FIELD_NAME_PATTERN,
        description="Lowercase metadata field name",
    )
    type: MetadataFieldType = Field(..., description="Metadata field type")


class KnowledgeMetadataUpdate(BaseModel):
    name: str | None = Field(
        None,
        min_length=1,
        max_length=255,
        pattern=FIELD_NAME_PATTERN,
        description="Lowercase metadata field name",
    )


class KnowledgeMetadataResponse(BaseModel):
    id: uuid.UUID | None = Field(None, description="Field ID")
    type: str = Field(..., description="Field type")
    name: str = Field(..., description="Field name")
    is_builtin: bool = Field(False, description="Builtin field")
    count: int | None = Field(None, description="Document usage count")
    created_at: datetime.datetime | int | None = Field(None)
    updated_at: datetime.datetime | int | None = Field(None)

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at", "updated_at", when_used="json")
    def _serialize_time(self, value: datetime.datetime | int | None) -> int | None:
        if isinstance(value, int):
            return value
        return to_timestamp_ms(value)


class BuiltinMetadataEnableRequest(BaseModel):
    enabled: bool = Field(..., description="Enable builtin metadata")


class BuiltinMetadataListResponse(BaseModel):
    enabled: bool
    fields: list[KnowledgeMetadataResponse]


class KnowledgeMetadataFieldsRequest(BaseModel):
    kb_ids: list[uuid.UUID] = Field(..., min_length=1)


class DocumentMetadataItem(BaseModel):
    document_id: uuid.UUID
    metadata: dict[str, Any]


class BatchUpdateMetadataRequest(BaseModel):
    items: list[DocumentMetadataItem] = Field(..., min_length=1, max_length=100)


class DocumentMetadataUpdateRequest(BaseModel):
    metadata: dict[str, Any]


class DocumentMetadataDeleteRequest(BaseModel):
    field_names: list[str] | None = None


class DocumentMetadataFieldResponse(BaseModel):
    field_id: str
    name: str
    type: str
    value: Any | None = None


class DocumentMetadataResponse(BaseModel):
    document_id: str
    metadata: dict[str, Any]
    fields: list[DocumentMetadataFieldResponse]


class DocumentMetadataDeleteResponse(BaseModel):
    document_id: str
    deleted_fields: list[str]
