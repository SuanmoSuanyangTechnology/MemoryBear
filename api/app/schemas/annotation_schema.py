import datetime
import uuid
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict, field_serializer


# ==================== Input Schemas ====================

class AnnotationCreate(BaseModel):
    """创建标注请求"""
    question: str = Field(..., min_length=1, max_length=5000, description="提问")
    answer: str = Field(..., min_length=1, max_length=10000, description="答案")


class AnnotationUpdate(BaseModel):
    """更新标注请求"""
    question: Optional[str] = Field(None, min_length=1, max_length=5000, description="提问")
    answer: Optional[str] = Field(None, min_length=1, max_length=10000, description="答案")


class AnnotationSettingUpdate(BaseModel):
    """更新标注设置请求"""
    similarity_threshold: Optional[float] = Field(None, ge=0.0, le=1.0, description="相似度阈值")
    model_config_id: Optional[uuid.UUID] = Field(None, description="Embedding模型配置ID")
    enabled: Optional[int] = Field(None, ge=0, le=1, description="是否启用: 1=是, 0=否")


# ==================== Output Schemas ====================

class Annotation(BaseModel):
    """标注输出"""
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
        return int(dt.timestamp() * 1000) if dt else None

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None


class AnnotationListItem(BaseModel):
    """标注列表项"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    question: str
    answer: str
    hit_count: int
    created_at: datetime.datetime
    updated_at: datetime.datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None


class AnnotationSettingResponse(BaseModel):
    """标注设置响应（仅对外字段）"""
    app_id: str
    workspace_id: str
    similarity_threshold: float
    model_config_id: Optional[str] = None
    enabled: int


class AnnotationHitResult(BaseModel):
    """标注命中结果"""
    model_config = ConfigDict(from_attributes=True)

    matched: bool = Field(..., description="是否命中")
    annotation_id: Optional[uuid.UUID] = Field(None, description="命中标注ID")
    question: Optional[str] = Field(None, description="命中的问题")
    answer: Optional[str] = Field(None, description="命中答案")
    similarity: Optional[float] = Field(None, description="相似度分数")


class BatchImportResult(BaseModel):
    """批量导入结果"""
    count: int = Field(..., description="成功导入数量")


class BatchDeleteResult(BaseModel):
    """批量删除结果"""
    count: int = Field(..., description="成功删除数量")


class AnnotationExportItem(BaseModel):
    """标注导出项"""
    question: str
    answer: str


class AnnotationHitLogItem(BaseModel):
    """标注命中历史条目"""
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
        return int(dt.timestamp() * 1000) if dt else None
