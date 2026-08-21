"""Schemas for permanent-memory management under memory value ranking."""

from typing import Optional

from pydantic import BaseModel, Field, field_validator

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
    is_permanent: bool = Field(default=False, description="是否永久记忆")
    value_score: Optional[float] = Field(
        default=None,
        description="动态价值分：永久=1.0；普通=0.75*topology_score + 0.25*T（T 为时间新近度）",
    )

    @field_validator("value_score")
    @classmethod
    def round_value_score(cls, v):
        return round(v, 1) if v is not None else None


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
