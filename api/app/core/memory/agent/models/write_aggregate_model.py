"""Pydantic models for write aggregate judgment operations."""

from typing import List, Union
from pydantic import BaseModel, Field


class MessageItem(BaseModel):
    """Individual message item in conversation."""
    
    role: str = Field(..., description="角色：user 或 assistant")
    content: str = Field(..., description="消息内容")


class WriteAggregateResponse(BaseModel):
    """Response model for aggregate judgment containing judgment result and output."""
    
    is_same_event: bool = Field(
        ..., 
        description="是否是同一事件。True表示是同一事件，False表示不同事件"
    )
    output: Union[List[MessageItem], bool] = Field(
        ..., 
        description="如果is_same_event为True，返回False；如果is_same_event为False，返回消息列表"
    )


# 为了保持向后兼容，保留旧的类名作为别名
WriteAggregateModel = WriteAggregateResponse
