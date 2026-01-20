"""Pydantic models for problem breakdown and extension operations."""

from typing import List, Optional
from pydantic import BaseModel, Field, RootModel


class ProblemBreakdownItem(BaseModel):
    """Individual item in problem breakdown response."""
    
    id: str
    question: str
    type: str
    reason: Optional[str] = None


class ProblemBreakdownResponse(RootModel[List[ProblemBreakdownItem]]):
    """Response model for problem breakdown containing list of breakdown items."""
    
    pass


class ExtendedQuestionItem(BaseModel):
    """Individual extended question item with reasoning."""
    
    original_question: str = Field(..., description="原始初步问题")
    extended_question: str = Field(..., description="扩展后的问题")
    type: str = Field(..., description="类型（事实检索 / 澄清 / 定义 / 比较 / 行动建议等）")
    reason: str = Field(..., description="生成该扩展问题的理由")


class ProblemExtensionResponse(RootModel[List[ExtendedQuestionItem]]):
    """Response model for problem extension containing list of extended questions."""
    
    pass
