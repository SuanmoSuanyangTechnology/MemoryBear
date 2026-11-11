"""Short-term memory API contracts, using UTC Unix milliseconds."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from src.common.pagination import PageMeta
from src.common.responses import ApiResponse

MemoScope = Literal["current", "ended"]
MemoDateType = Literal["all", "dated", "undated"]
MemoStatus = Literal["pending", "ongoing", "ended"]
TimestampMs = Annotated[
    int,
    Field(description="UTC Unix timestamp in milliseconds", json_schema_extra={"format": "int64"}),
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


class ShortTermMemoResponse(ApiResponse[ShortTermMemoList]):
    data: ShortTermMemoList
    time: TimestampMs


class ShortTermRetrievalItem(BaseModel):
    query: str
    retrieval: Any
    source: str


class ShortTermItem(BaseModel):
    retrieval: list[ShortTermRetrievalItem]
    message: str
    answer: str | None


class LongTermItem(BaseModel):
    query: str
    retrieval: str


class ShortTermData(BaseModel):
    short_term: list[ShortTermItem]
    long_term: list[LongTermItem]
    entity: int
    retrieval_number: int
    long_term_number: int


class ShortTermResponse(ApiResponse[ShortTermData]):
    time: TimestampMs
