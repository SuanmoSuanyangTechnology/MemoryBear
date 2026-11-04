"""Public display records; nullable fields remain required response keys."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class WrittenItem(BaseModel):
    id: str
    end_user_id: str
    memory_id: str | None
    memory_type: str | None
    name: str | None
    content: str
    occurred_at: int | None


class RetrievedItem(BaseModel):
    id: str
    end_user_id: str
    search_mode: str | None
    query: str
    content: str
    occurred_at: int | None


class EngineItem(BaseModel):
    id: str
    end_user_id: str | None
    engine_type: str
    name: str
    content: str
    occurred_at: int


class AllWrittenItem(WrittenItem):
    source: Literal["written"]


class AllRetrievedItem(RetrievedItem):
    source: Literal["retrieved"]


class AllEngineItem(EngineItem):
    source: Literal["engines"]


AllItem = Annotated[
    AllWrittenItem | AllRetrievedItem | AllEngineItem, Field(discriminator="source")
]


class DailyActivity(BaseModel):
    date: str
    timestamp: int
    total_activities: int


class CategoryActivity(BaseModel):
    count: int
    percentage: float


class CompositionDay(DailyActivity):
    categories: dict[str, CategoryActivity]


class CompositionSummary(BaseModel):
    total_activities: int
    categories: dict[str, CategoryActivity]


class ActivityStatisticsData(BaseModel):
    total_activities: int
    active_days: int
    daily_average: float
    peak_daily_activities: int
    involved_users: int
    category_totals: dict[str, int]
    daily_activities: list[DailyActivity]


class ActivityCompositionData(BaseModel):
    summary: CompositionSummary
    daily_activities: list[CompositionDay]
