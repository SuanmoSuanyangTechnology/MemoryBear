from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ForgettingQuotaData(BaseModel):
    memory_limit: int
    trigger_count: int
    target_count: int
    breakdown: dict[str, int]
    active_count: int
    auxiliary_active_count: int
    auxiliary_breakdown: dict[str, int]
    forgotten_count: int


class ForgettingTrendItem(BaseModel):
    date: int
    count: int


class PageInfo(BaseModel):
    page: int
    pagesize: int
    total: int
    hasnext: bool


class ForgettingCandidate(BaseModel):
    model_config = ConfigDict(extra="allow")
    pool: str | None = None
    node_type: str | None = None
    created_at: int | None = None
    content: str | None = None
    forgetting_activation: float | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class ForgettingCandidatesData(BaseModel):
    items: list[ForgettingCandidate]
    page: PageInfo


class ForgottenLogItem(BaseModel):
    node_type: str
    content: str
    delete_at: int


class ForgottenLogsData(BaseModel):
    items: list[ForgottenLogItem]
    page: PageInfo


class RefreshCacheData(BaseModel):
    refreshed: bool
