import uuid
from datetime import datetime

from pydantic import BaseModel, Field, ConfigDict, field_serializer
from typing import Any, Dict, List, Optional, Union
from uuid import UUID
from enum import Enum
from app.core.utils.datetime_utils import to_timestamp_ms


class OptimizationStrategy(str, Enum):
    """优化策略枚举"""
    SPEED_FIRST = "speed_first"
    ACCURACY_FIRST = "accuracy_first"
    BALANCED = "balanced"

class SubProblemEnum(str, Enum):
    """反思引擎子问题类型枚举"""
    ENTITY_DEDUP = "entity_dedup"
    DESCRIPTION_MERGE = "description_merge"
    STALE_DETECTION = "stale_detection"
    FACT_CONTRADICTION = "fact_contradiction"
    METADATA_VALIDATION = "metadata_validation"
    UNRESOLVED_ENTITY = "unresolved_entity"

class TriggerTypeEnum(str, Enum):
    """触发方式枚举"""
    SCHEDULED = "scheduled"
    CONVERSATION = "conversation"
    MANUAL = "manual"

class LogStatusEnum(str, Enum):
    """日志状态枚举"""
    RESOLVED = "resolved"
    RECORDED = "recorded"

class ReflectionLogListItem(BaseModel):
    """列表页单条日志（轻量字段，不含 JSONB 详情）"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sub_problem: str
    trigger_type: str
    baseline: Optional[str] = None
    strategy: Optional[str] = None
    confidence: Optional[float] = None
    status: str
    summary_text: Optional[str] = None
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime):
        return to_timestamp_ms(dt)

class ReflectionLogDetail(BaseModel):
    """详情页完整日志（含 JSONB 字段）"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    end_user_id: uuid.UUID
    sub_problem: str
    trigger_type: str
    baseline: Optional[str] = None
    strategy: Optional[str] = None
    confidence: Optional[float] = None
    status: str
    summary_text: Optional[str] = None
    entity_ids: Optional[List[str]] = None
    statement_ids: Optional[List[str]] = None
    trigger_detail: Optional[Dict[str, Any]] = None
    solution_detail: Optional[Dict[str, Any]] = None
    execution_detail: Optional[Dict[str, Any]] = None
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime):
        return to_timestamp_ms(dt)


class Memory_Reflection(BaseModel):
    config_id:  Union[uuid.UUID, int, str]  = None
    reflection_enabled: bool
    reflection_period_in_hours: str
    reflexion_range: Optional[str] = "partial"
    baseline: Optional[str] = "TIME"
    reflection_model_id: str
    memory_verify: bool
    quality_assessment: bool
    
    # 新增快速引擎优化参数
    optimization_strategy: Optional[OptimizationStrategy] = OptimizationStrategy.BALANCED
    use_fast_model: Optional[bool] = True
    enable_caching: Optional[bool] = True
    enable_streaming: Optional[bool] = True
    batch_size: Optional[int] = Field(default=3, ge=1, le=10)
    max_concurrent: Optional[int] = Field(default=5, ge=1, le=20)
    
    class Config:
        use_enum_values = True


class FastReflectionRequest(BaseModel):
    """快速反思请求模型"""
    reflection: Memory_Reflection
    host_id: Optional[str] = "88a459f5_text02"
    optimization_strategy: Optional[OptimizationStrategy] = OptimizationStrategy.BALANCED
    
    class Config:
        use_enum_values = True


class ReflectionBenchmarkRequest(BaseModel):
    """反思基准测试请求模型"""
    reflection: Memory_Reflection
    host_id: Optional[str] = "88a459f5_text02"
    iterations: Optional[int] = Field(default=3, ge=1, le=10)
    
    class Config:
        use_enum_values = True


