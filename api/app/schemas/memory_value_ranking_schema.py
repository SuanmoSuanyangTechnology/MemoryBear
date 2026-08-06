"""Schemas for permanent-memory management under memory value ranking."""

from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.response_schema import ApiResponse, PageMeta


class PermanentMemoryQuota(BaseModel):
    total_memory_limit: int = Field(..., gt=0, description="当前有效记忆总容量")
    permanent_limit: int = Field(..., ge=0, description="记忆总容量向下取整后的 10%")
    used: int = Field(..., ge=0)
    remaining: int = Field(..., ge=0)


class PermanentMemoryProperties(BaseModel):
    statement: str
    created_at: Optional[int] = Field(
        default=None,
        description="UTC Unix timestamp in milliseconds",
    )
    is_permanent: bool = True


class PermanentMemoryItem(BaseModel):
    id: str = Field(..., description="Neo4j elementId")
    label: str = Field(default="Statement")
    properties: PermanentMemoryProperties


class PermanentMemoryList(BaseModel):
    page: PageMeta
    quota: PermanentMemoryQuota
    items: list[PermanentMemoryItem]


class PermanentMemoryUnmarkRequest(BaseModel):
    end_user_id: str = Field(..., min_length=1)


class PermanentMemoryUnmarkResult(BaseModel):
    id: str = Field(..., description="Neo4j elementId")
    is_permanent: bool = False
    quota: PermanentMemoryQuota


class PermanentMemoryQuotaApiResponse(ApiResponse):
    data: PermanentMemoryQuota


class PermanentMemoryListApiResponse(ApiResponse):
    data: PermanentMemoryList


class PermanentMemoryUnmarkApiResponse(ApiResponse):
    data: PermanentMemoryUnmarkResult
