"""Dashboard HTTP request bodies."""

from pydantic import BaseModel, Field


class MemoryCountsRequest(BaseModel):
    # Semantic validation intentionally remains a legacy business error, not 422.
    end_user_ids: list[str]


class GenerateRAGProfileRequest(BaseModel):
    end_user_id: str
    limit: int = Field(15, gt=0, le=100)
    max_tags: int = Field(10, ge=1, le=100)
