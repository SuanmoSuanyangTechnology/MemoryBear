"""情绪分析相关的请求和响应模型"""

from typing import Optional
from pydantic import BaseModel, Field


class EmotionTagsRequest(BaseModel):
    """获取情绪标签统计请求"""
    group_id: str = Field(..., description="组ID")
    emotion_type: Optional[str] = Field(None, description="情绪类型过滤（joy/sadness/anger/fear/surprise/neutral）")
    start_date: Optional[str] = Field(None, description="开始日期（ISO格式，如：2024-01-01）")
    end_date: Optional[str] = Field(None, description="结束日期（ISO格式，如：2024-12-31）")
    limit: int = Field(10, ge=1, le=100, description="返回数量限制")


class EmotionWordcloudRequest(BaseModel):
    """获取情绪词云数据请求"""
    group_id: str = Field(..., description="组ID")
    emotion_type: Optional[str] = Field(None, description="情绪类型过滤（joy/sadness/anger/fear/surprise/neutral）")
    limit: int = Field(50, ge=1, le=200, description="返回词语数量")


class EmotionHealthRequest(BaseModel):
    """获取情绪健康指数请求"""
    group_id: str = Field(..., description="组ID")
    time_range: str = Field("30d", description="时间范围（7d/30d/90d）")


class EmotionSuggestionsRequest(BaseModel):
    """获取个性化情绪建议请求"""
    group_id: str = Field(..., description="组ID")
    config_id: Optional[int] = Field(None, description="配置ID（用于指定LLM模型）")
