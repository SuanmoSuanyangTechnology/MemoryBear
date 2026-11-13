"""Knowledge file request and response schemas copied from the legacy API."""

import datetime
import re
import uuid
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_serializer

from ...utils.datetime_utils import to_timestamp_ms
from .storage_fields import INT32_MAX, INT32_MIN, NonNullUpdate


class BatchDownloadRequest(BaseModel):
    file_ids: list[uuid.UUID] = Field(
        ...,
        min_length=1,
        max_length=500,
        description="File IDs to download, at most 500",
    )
    zip_filename: str | None = Field(None, description="Optional ZIP filename")


class KBBatchDownloadRequest(BaseModel):
    zip_filename: str | None = Field(None, description="Optional ZIP filename")


class FileBase(BaseModel):
    kb_id: uuid.UUID
    created_by: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None
    file_name: str
    file_ext: str
    file_size: int
    file_url: str | None = None
    file_key: str | None = None
    created_at: datetime.datetime | None = None


class FileCreate(FileBase):
    pass


class CustomTextFileCreate(BaseModel):
    title: str
    content: str


def _validate_file_size_text(value: str) -> str:
    if not re.fullmatch(r"[+-]?[0-9]+", value.strip()):
        raise ValueError("file size must be an integer")
    if not INT32_MIN <= int(value) <= INT32_MAX:
        raise ValueError("file size exceeds the supported integer range")
    return value


FileSizeInput = (
    Annotated[
        str, AfterValidator(_validate_file_size_text), Field(description="Legacy integer text")
    ]
    | Annotated[int, Field(strict=True, ge=0, le=INT32_MAX)]
)


class FileUpdate(BaseModel):
    parent_id: uuid.UUID | None = Field(None)
    file_name: NonNullUpdate[str]
    file_ext: NonNullUpdate[str]
    file_size: NonNullUpdate[FileSizeInput]
    file_url: str | None = Field(None)


class File(FileBase):
    id: uuid.UUID
    created_at: datetime.datetime

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, value: datetime.datetime) -> int | None:
        return to_timestamp_ms(value)
