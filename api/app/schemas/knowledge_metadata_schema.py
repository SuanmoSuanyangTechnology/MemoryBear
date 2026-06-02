import datetime
import uuid
from typing import Any
from pydantic import BaseModel, Field, field_serializer, ConfigDict
from enum import StrEnum


class MetadataFieldType(StrEnum):
    STRING = "string"
    NUMBER = "number"
    TIME = "time"


class FilterCondition(BaseModel):
    field: str = Field(..., description="元数据字段名")
    operator: str = Field(..., description="操作符")
    value: Any | None = Field(None, description="值")


class FilterGroup(BaseModel):
    conditions: list[FilterCondition] = Field(..., description="条件列表")
    logic: str = Field("AND", description="组内逻辑: AND | OR")


class MetadataFilterMode(StrEnum):
    MANUAL = "manual"
    AUTO = "auto"


# === KnowledgeMetadata CRUD Schemas ===

class KnowledgeMetadataCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="字段名")
    type: MetadataFieldType = Field(..., description="字段类型")


class KnowledgeMetadataUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255, description="字段名")


class KnowledgeMetadataResponse(BaseModel):
    id: uuid.UUID | None = Field(None, description="字段ID（内置字段无ID）")
    type: str = Field(..., description="字段类型")
    name: str = Field(..., description="字段名")
    is_builtin: bool = Field(False, description="是否内置字段")
    created_at: int | None = Field(None, description="创建时间戳(ms)")
    updated_at: int | None = Field(None, description="更新时间戳(ms)")

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime | int | None):
        if isinstance(dt, int):
            return dt
        return int(dt.timestamp() * 1000) if dt else None

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime | int | None):
        if isinstance(dt, int):
            return dt
        return int(dt.timestamp() * 1000) if dt else None

    model_config = ConfigDict(from_attributes=True)


# === Builtin Metadata Schemas ===

class BuiltinMetadataEnableRequest(BaseModel):
    enabled: bool = Field(..., description="是否启用内置元数据")


class BuiltinMetadataListResponse(BaseModel):
    enabled: bool = Field(..., description="开关状态")
    fields: list[KnowledgeMetadataResponse] = Field(..., description="内置字段列表")


# === Batch Update Document Metadata Schemas ===

class DocumentMetadataItem(BaseModel):
    document_id: uuid.UUID = Field(..., description="文档ID")
    metadata: dict[str, Any] = Field(..., description="元数据 {field_name: value}")


class BatchUpdateMetadataRequest(BaseModel):
    items: list[DocumentMetadataItem] = Field(..., min_length=1, description="文档元数据列表")
