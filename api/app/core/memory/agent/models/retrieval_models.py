"""Pydantic models for retrieval operations."""

from typing import List, Dict, Any
from pydantic import BaseModel


class RetrievalResult(BaseModel):
    """Result model for retrieval operation."""
    
    Query: str
    Expansion_issue: List[Dict[str, Any]]


class DistinguishTypeResponse(BaseModel):
    """Response model for data type differentiation."""
    
    type: str
