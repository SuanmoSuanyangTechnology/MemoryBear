"""Dashboard read payload contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class PageInfo(BaseModel):
    page: int
    pagesize: int
    total: int
    hasnext: bool


class EndUserItem(BaseModel):
    end_user_id: str
    end_user: dict[str, Any]
    tags: list[str]
    memory_num: dict[str, int | float]
    memory_config: dict[str, str | None]


class EndUsersData(BaseModel):
    items: list[EndUserItem]
    page: PageInfo


class MemoryIncrementItem(BaseModel):
    date: int
    total_num: int


class MemoryIncrementData(BaseModel):
    items: list[MemoryIncrementItem]


class MemoryCountItem(BaseModel):
    end_user_id: str
    other_name: str | None = None
    memory_count: int


class MemoryCountsData(BaseModel):
    total: int
    items: list[MemoryCountItem]


class TotalMemoryDetail(BaseModel):
    end_user_id: str
    count: int
    name: str | None


class TotalMemoryData(BaseModel):
    total_memory_count: int
    host_count: int
    details: list[TotalMemoryDetail]


class RAGMessage(BaseModel):
    id: str
    role: str
    content: str
    dialog_at: str
    source: str
    legacy: bool


class RAGContentData(BaseModel):
    items: list[RAGMessage]
    page: PageInfo


class ChunkSummaryData(BaseModel):
    summary: str
    tags: list[Any]
    personas: list[Any]
    generated: bool


class ChunkInsightData(BaseModel):
    insight: str
    behavior_pattern: str
    key_findings: str
    growth_trajectory: str
    generated: bool


class DashboardCounters(BaseModel):
    total_memory: int | None
    total_app: int | None
    total_knowledge: int | None
    total_api_call: int | None
    total_memory_change: float | None
    total_app_change: float | None
    total_knowledge_change: float | None
    total_api_call_change: float | None


class DashboardData(BaseModel):
    storage_type: str
    memory_update: int | None
    memory_update_change: int | None
    neo4j_data: DashboardCounters | None
    rag_data: DashboardCounters | None
    workspace_statistics: dict[str, Any] | None
