from pydantic import BaseModel, Field, field_serializer, ConfigDict
import datetime
import uuid


class FileBase(BaseModel):
    kb_id: uuid.UUID
    created_by: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None
    file_name: str
    file_ext: str
    file_size: int


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


class File(FileBase):
    id: uuid.UUID
    created_at: datetime.datetime

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None
