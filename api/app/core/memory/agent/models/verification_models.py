"""Pydantic models for verification operations."""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class VerificationItem(BaseModel):
    """Individual verification item for a query-answer pair."""
    
    query_small: str = Field(..., description="子问题")
    answer_small: str = Field(..., description="子问题的回答")
    status: str = Field(..., description="验证状态：True 或 False")
    query_answer: str = Field(..., description="问题的答案（与 answer_small 相同）")


class VerificationResult(BaseModel):
    """Result model for verification operation."""
    
    query: str = Field(..., description="原始查询问题")
    history: List[Dict[str, Any]] = Field(default_factory=list, description="历史对话记录")
    expansion_issue: List[VerificationItem] = Field(
        default_factory=list, 
        description="验证后的数据列表，包含所有通过验证的问答对"
    )
    split_result: str = Field(
        ..., 
        description="验证结果状态：success（expansion_issue 非空）或 failed（expansion_issue 为空）"
    )
    reason: Optional[str] = Field(
        None, 
        description="验证结果的说明和分析"
    )
