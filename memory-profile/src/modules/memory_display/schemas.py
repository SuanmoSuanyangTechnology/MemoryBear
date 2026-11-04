"""Payloads for workspace activity endpoints."""

from pydantic import BaseModel


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
