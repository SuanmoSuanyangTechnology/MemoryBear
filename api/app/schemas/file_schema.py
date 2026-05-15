from typing import List, Optional

from pydantic import BaseModel, Field, field_serializer, ConfigDict
import datetime
import uuid


class BatchDownloadRequest(BaseModel):
    file_ids: List[uuid.UUID] = Field(
        ...,
        min_length=1,
        max_length=500,
        description="要下载的文件ID列表，最多500个",
    )
    zip_filename: Optional[str] = Field(
        None,
        description="ZIP文件名，默认为 download.zip",
    )


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
    def _serialize_created_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None
