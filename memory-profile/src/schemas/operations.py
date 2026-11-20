"""Result payloads returned by the on-demand jobs."""

from pydantic import BaseModel


class RAGProfileResult(BaseModel):
    end_user_id: str
    summary_length: int
    tags_count: int
    personas_count: int
    insight_generated: bool


class EmotionSuggestion(BaseModel):
    type: str
    title: str
    content: str
    priority: str
    actionable_steps: list[str]


class EmotionSuggestionsResult(BaseModel):
    health_summary: str
    suggestions: list[EmotionSuggestion]


class ForgettingReport(BaseModel):
    merged_count: int
    nodes_before: int
    nodes_after: int
    reduction_rate: float
    duration_seconds: float
    start_time: str
    end_time: str
    failed_count: int
    success_rate: float
