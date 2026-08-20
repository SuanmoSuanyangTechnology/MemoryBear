"""Knowledge file request and response schemas copied from the legacy API."""

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from ...utils.datetime_utils import to_timestamp_ms


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


class FileUpdate(BaseModel):
    parent_id: uuid.UUID | None = Field(None)
    file_name: str | None = Field(None)
    file_ext: str | None = Field(None)
    file_size: str | None = Field(None)
    file_url: str | None = Field(None)


class File(FileBase):
    id: uuid.UUID
    created_at: datetime.datetime

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, value: datetime.datetime) -> int | None:
        return to_timestamp_ms(value)
