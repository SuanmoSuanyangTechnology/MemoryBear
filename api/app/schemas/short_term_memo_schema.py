"""Management memo API contracts, using UTC Unix milliseconds."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.schemas.response_schema import ApiResponse, PageMeta

MemoScope = Literal["current", "ended"]
MemoDateType = Literal["all", "dated", "undated"]
MemoStatus = Literal["pending", "ongoing", "ended"]
TimestampMs = Annotated[
    int, Field(description="UTC Unix timestamp in milliseconds", json_schema_extra={"format": "int64"})
]


class ShortTermMemoItem(BaseModel):
    id: str
    statement: str
    status: MemoStatus
    start_date: TimestampMs | None
    end_date: TimestampMs | None
    is_time_unspecified: bool
    retain_until: TimestampMs | None
    relative_days: int = Field(ge=0)


class ShortTermMemoGroupCounts(BaseModel):
    total: int = Field(ge=0)
    dated: int = Field(ge=0)
    undated: int = Field(ge=0)


class ShortTermMemoCounts(BaseModel):
    total: int = Field(ge=0)
    current: ShortTermMemoGroupCounts
    ended: ShortTermMemoGroupCounts


class ShortTermMemoList(BaseModel):
    counts: ShortTermMemoCounts
    items: list[ShortTermMemoItem]
    page: PageMeta


class ShortTermMemoResponse(ApiResponse):
    data: ShortTermMemoList
    time: TimestampMs
