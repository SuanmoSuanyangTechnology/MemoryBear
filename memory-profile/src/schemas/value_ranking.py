"""Data contracts for permanent-memory management."""

from pydantic import BaseModel, Field, field_validator

from src.common.pagination import PageData


class PermanentMemoryQuota(BaseModel):
    total_memory_limit: int = Field(gt=0)
    permanent_limit: int = Field(ge=0)
    used: int = Field(ge=0)
    remaining: int = Field(ge=0)


class PermanentMemoryProperties(BaseModel):
    statement: str
    created_at: int | None = Field(default=None, description="UTC Unix timestamp in milliseconds")
    is_permanent: bool = False
    value_score: float | None = None

    @field_validator("value_score")
    @classmethod
    def round_value_score(cls, value: float | None) -> float | None:
        return round(value, 1) if value is not None else None


class PermanentMemoryItem(BaseModel):
    id: str = Field(description="Neo4j elementId")
    label: str = "Statement"
    properties: PermanentMemoryProperties


class PermanentMemoryList(PageData[PermanentMemoryItem]):
    quota: PermanentMemoryQuota


class PermanentMemoryUnmarkRequest(BaseModel):
    end_user_id: str = Field(min_length=1)


class PermanentMemoryUnmarkResult(BaseModel):
    id: str = Field(description="Neo4j elementId")
    is_permanent: bool = False
    quota: PermanentMemoryQuota
