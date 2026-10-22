"""Annotation schemas (wire-compatible port of the host ``annotation_schema``)."""

from __future__ import annotations

import datetime
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from ..utils.datetime_utils import to_timestamp_ms


class AnnotationCreate(BaseModel):
    """Create-annotation request."""

    question: str = Field(..., min_length=1, max_length=5000, description="提问")
    answer: str = Field(..., min_length=1, max_length=10000, description="答案")


class AnnotationUpdate(BaseModel):
    """Update-annotation request."""

    question: Optional[str] = Field(None, min_length=1, max_length=5000, description="提问")
    answer: Optional[str] = Field(None, min_length=1, max_length=10000, description="答案")


class AnnotationSettingUpdate(BaseModel):
    """Update-annotation-settings request."""

    similarity_threshold: Optional[float] = Field(None, ge=0.0, le=1.0, description="相似度阈值")
    model_config_id: Optional[uuid.UUID] = Field(None, description="Embedding模型配置ID")
    enabled: Optional[int] = Field(None, ge=0, le=1, description="是否启用: 1=是, 0=否")


class Annotation(BaseModel):
    """Full annotation record."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    app_id: uuid.UUID
    workspace_id: uuid.UUID
    created_by: uuid.UUID
    question: str
    answer: str
    hit_count: int
    is_active: int
    created_at: datetime.datetime
    updated_at: datetime.datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)


class AnnotationListItem(BaseModel):
    """Annotation list item."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    question: str
    answer: str
    hit_count: int
    created_at: datetime.datetime
    updated_at: datetime.datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)


class AnnotationSettingResponse(BaseModel):
    """Annotation settings response (exposed fields only)."""

    app_id: str
    workspace_id: str
    similarity_threshold: float
    model_config_id: Optional[str] = None
    enabled: int


class BatchImportResult(BaseModel):
    """Batch import result."""

    count: int = Field(..., description="成功导入数量")


class BatchDeleteResult(BaseModel):
    """Batch delete result."""

    count: int = Field(..., description="成功删除数量")


class AnnotationHitLogItem(BaseModel):
    """Annotation hit-history item."""

    model_config = ConfigDict(from_attributes=True)

    query: str = Field(..., description="提问")
    matched_question: str = Field(..., description="匹配")
    answer: str = Field(..., description="回复")
    source: str = Field(..., description="来源")
    similarity: float = Field(..., description="分数")
    hit_at: datetime.datetime = Field(..., description="时间")

    @field_serializer("similarity", when_used="json")
    def _serialize_similarity(self, v: float):
        return round(v, 2)

    @field_serializer("hit_at", when_used="json")
    def _serialize_hit_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)


__all__ = [
    "Annotation",
    "AnnotationCreate",
    "AnnotationHitLogItem",
    "AnnotationListItem",
    "AnnotationSettingResponse",
    "AnnotationSettingUpdate",
    "AnnotationUpdate",
    "BatchDeleteResult",
    "BatchImportResult",
]
