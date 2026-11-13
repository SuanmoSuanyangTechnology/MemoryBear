"""Knowledge document request and response schemas copied from the legacy API."""

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_serializer

from ...utils.datetime_utils import (
    normalize_progress_message_timestamps,
    to_timestamp_ms,
)
from .storage_fields import DatabaseInt, NonNullUpdate


class DocumentBase(BaseModel):
    kb_id: uuid.UUID
    created_by: uuid.UUID | None = None
    file_id: uuid.UUID
    file_name: str
    file_ext: str
    file_size: int
    file_meta: dict
    parser_id: str
    parser_config: dict


class DocumentCreate(DocumentBase):
    file_size: DatabaseInt


class DocumentUpdate(BaseModel):
    file_id: NonNullUpdate[uuid.UUID]
    file_name: NonNullUpdate[str]
    file_ext: NonNullUpdate[str]
    file_size: NonNullUpdate[DatabaseInt]
    file_meta: NonNullUpdate[dict]
    parser_id: NonNullUpdate[str]
    parser_config: dict | None = Field(None)
    chunk_num: NonNullUpdate[DatabaseInt]
    progress: NonNullUpdate[FiniteFloat]
    progress_msg: NonNullUpdate[str]
    process_begin_at: NonNullUpdate[datetime.datetime]
    process_duration: NonNullUpdate[FiniteFloat]
    run: NonNullUpdate[DatabaseInt]
    status: NonNullUpdate[DatabaseInt]


class Document(DocumentBase):
    id: uuid.UUID
    parent_child_mode: bool = False
    chunk_num: int
    progress: float
    progress_msg: str
    process_begin_at: datetime.datetime
    process_duration: float
    run: int
    status: int
    created_at: datetime.datetime
    updated_at: datetime.datetime

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at", "updated_at", "process_begin_at", when_used="json")
    def _serialize_time(self, value: datetime.datetime) -> int | None:
        return to_timestamp_ms(value)

    @field_serializer("progress_msg", when_used="json")
    def _serialize_progress_msg(self, value: str) -> str | None:
        return normalize_progress_message_timestamps(value, self.process_begin_at)
