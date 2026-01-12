"""
用户记忆相关的请求和响应模型
"""
from pydantic import BaseModel, Field
from typing import Optional


class EpisodicMemoryOverviewRequest(BaseModel):
    """情景记忆总览查询请求"""
    
    end_user_id: str = Field(..., description="终端用户ID")
    time_range: str = Field(
        default="all",
        description="时间范围筛选，可选值：all, today, this_week, this_month"
    )
    episodic_type: str = Field(
        default="all",
        description="情景类型筛选，可选值：all, conversation, project_work, learning, decision, important_event"
    )
    title_keyword: Optional[str] = Field(
        default=None,
        description="标题关键词，用于模糊搜索（可选）"
    )


class EpisodicMemoryDetailsRequest(BaseModel):
    """情景记忆详情查询请求"""
    
    end_user_id: str = Field(..., description="终端用户ID")
    summary_id: str = Field(..., description="情景记忆摘要ID")
