"""Pydantic models for summary operations."""

from typing import List
from pydantic import BaseModel, Field


class SummaryData(BaseModel):
    """Data structure for summary input."""
    
    query: str
    history: List[str] = Field(default_factory=list)
    retrieve_info: List[str] = Field(default_factory=list)


class SummaryResponse(BaseModel):
    """Response model for summary operation."""
    
    data: SummaryData
    query_answer: str


class RetrieveSummaryData(BaseModel):
    """Data structure for retrieve summary response."""
    
    query_answer: str = Field(default="")


class RetrieveSummaryResponse(BaseModel):
    """Response model for retrieve summary operation."""
    
    data: RetrieveSummaryData
