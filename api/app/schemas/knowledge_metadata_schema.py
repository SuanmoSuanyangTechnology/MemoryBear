import datetime
import uuid
from typing import Any
from pydantic import BaseModel, Field, field_serializer, field_validator, ConfigDict
from enum import StrEnum
from app.core.utils.datetime_utils import to_timestamp_ms


FIELD_NAME_PATTERN = r"^[a-z][a-z0-9_]*$"

class MetadataFieldType(StrEnum):
    STRING = "string"
    NUMBER = "number"
    TIME = "time"


class FilterCondition(BaseModel):
    field: str = Field(..., description="元数据字段名")
    operator: str = Field(..., description="操作符")
    value: Any | None = Field(None, description="值")


class GroupLogic(StrEnum):
    AND = "and"
    OR = "or"


class FilterGroup(BaseModel):
    conditions: list[FilterCondition] = Field(..., description="条件列表")
    logic: GroupLogic = Field(GroupLogic.AND, description="组内逻辑: and | or")

    @field_validator("logic", mode="before")
    @classmethod
    def normalize_logic(cls, value):
        if isinstance(value, str):
            return value.strip().lower()
        return value


class MetadataFilterMode(StrEnum):
    MANUAL = "manual"
    AUTO = "auto"


# === KnowledgeMetadata CRUD Schemas ===

class KnowledgeMetadataCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, pattern=FIELD_NAME_PATTERN,
                      description="字段名（小写字母开头，仅含小写字母、数字、下划线）")
    type: MetadataFieldType = Field(..., description="字段类型")


class KnowledgeMetadataUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255, pattern=FIELD_NAME_PATTERN,
                             description="字段名（小写字母开头，仅含小写字母、数字、下划线）")


class KnowledgeMetadataResponse(BaseModel):
    id: uuid.UUID | None = Field(None, description="字段ID（内置字段无ID）")
    type: str = Field(..., description="字段类型")
    name: str = Field(..., description="字段名")
    is_builtin: bool = Field(False, description="是否内置字段")
    count: int | None = Field(None, description="字段被有效文档使用的数量（仅自定义字段）")
    created_at: datetime.datetime | int | None = Field(None, description="创建时间戳(ms)")
    updated_at: datetime.datetime | int | None = Field(None, description="更新时间戳(ms)")

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime | int | None):
        if isinstance(dt, int):
            return dt
        return to_timestamp_ms(dt)

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime | int | None):
        if isinstance(dt, int):
            return dt
        return to_timestamp_ms(dt)

    model_config = ConfigDict(from_attributes=True)


# === Builtin Metadata Schemas ===

class BuiltinMetadataEnableRequest(BaseModel):
    enabled: bool = Field(..., description="是否启用内置元数据")


class BuiltinMetadataListResponse(BaseModel):
    enabled: bool = Field(..., description="开关状态")
    fields: list[KnowledgeMetadataResponse] = Field(..., description="内置字段列表")


class KnowledgeMetadataFieldsRequest(BaseModel):
    kb_ids: list[uuid.UUID] = Field(..., min_length=1, description="知识库ID列表")


# === Batch Update Document Metadata Schemas ===

class DocumentMetadataItem(BaseModel):
    document_id: uuid.UUID = Field(..., description="文档ID")
    metadata: dict[str, Any] = Field(..., description="元数据 {field_name: value}")


class BatchUpdateMetadataRequest(BaseModel):
    items: list[DocumentMetadataItem] = Field(..., min_length=1, max_length=100, description="文档元数据列表，最多100条")


class DocumentMetadataUpdateRequest(BaseModel):
    metadata: dict[str, Any] = Field(..., description="元数据 {field_name: value}")


class DocumentMetadataDeleteRequest(BaseModel):
    field_names: list[str] | None = Field(None, description="要删除的字段名列表，不传则清空全部")


class DocumentMetadataFieldResponse(BaseModel):
    field_id: str = Field(..., description="字段定义ID")
    name: str = Field(..., description="字段名")
    type: str = Field(..., description="字段类型")
    value: Any | None = Field(None, description="字段值")


class DocumentMetadataResponse(BaseModel):
    document_id: str = Field(..., description="文档ID")
    metadata: dict[str, Any] = Field(..., description="元数据 {field_name: value}")
    fields: list[DocumentMetadataFieldResponse] = Field(..., description="字段列表")


class DocumentMetadataDeleteResponse(BaseModel):
    document_id: str = Field(..., description="文档ID")
    deleted_fields: list[str] = Field(..., description="已删除的字段名列表")
