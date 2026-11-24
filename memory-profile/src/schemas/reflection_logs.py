"""Filters and response models for reflection logs."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, field_serializer

from src.common.normalization import datetime_to_epoch_ms


class SubProblem(StrEnum):
    ENTITY_DEDUP = "entity_dedup"
    DESCRIPTION_MERGE = "description_merge"
    STALE_DETECTION = "stale_detection"
    FACT_CONTRADICTION = "fact_contradiction"
    METADATA_VALIDATION = "metadata_validation"
    UNRESOLVED_ENTITY = "unresolved_entity"
    ALIAS_MERGE = "alias_merge"


class TriggerType(StrEnum):
    SCHEDULED = "scheduled"
    CONVERSATION = "conversation"
    MANUAL = "manual"


class LogStatus(StrEnum):
    RESOLVED = "resolved"
    RECORDED = "recorded"


class ReflectionLogListItem(BaseModel):
    id: UUID
    sub_problem: str
    trigger_type: str
    baseline: str | None = None
    strategy: str | None = None
    confidence: float | None = None
    status: str
    summary_text: str | None = None
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, value: datetime) -> int | None:
        return datetime_to_epoch_ms(value)


class ReflectionLogDetail(ReflectionLogListItem):
    end_user_id: UUID
    entity_ids: list[str] | None = None
    statement_ids: list[str] | None = None
    trigger_detail: dict[str, Any] | None = None
    solution_detail: dict[str, Any] | None = None
    execution_detail: dict[str, Any] | None = None


class ReflectionLogStats(BaseModel):
    total: int
    sub_problem: dict[str, int]
    status: dict[str, int]
    resolve_rate: float


class ReflectionLogPageInfo(BaseModel):
    page: int
    pagesize: int
    total: int
    hasnext: bool


class ReflectionLogPage(BaseModel):
    items: list[ReflectionLogListItem]
    page: ReflectionLogPageInfo
