from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class OptimizationStrategy(str, Enum):
    """优化策略枚举"""
    SPEED_FIRST = "speed_first"
    ACCURACY_FIRST = "accuracy_first"
    BALANCED = "balanced"


class Memory_Reflection(BaseModel):
    config_id: Optional[int] = None
    reflectionenabled: bool
    reflection_period_in_hours: str
    reflexion_range: str
    baseline: str
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


