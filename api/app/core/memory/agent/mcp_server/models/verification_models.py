"""Pydantic models for verification operations."""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class VerificationResult(BaseModel):
    """Result model for verification operation."""
    
    query: str
    expansion_issue: List[Dict[str, Any]]
    split_result: str
    reason: Optional[str] = None
    history: List[Dict[str, Any]] = Field(default_factory=list)
