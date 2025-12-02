from pydantic import BaseModel, Field, field_serializer, ConfigDict
import datetime
import uuid


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
    pass


class DocumentUpdate(BaseModel):
    file_id: uuid.UUID | None = Field(None)
    file_name: str | None = Field(None)
    file_ext: str | None = Field(None)
    file_size: int | None = Field(None)
    file_meta: dict | None = Field(None)
    parser_id: str | None = Field(None)
    parser_config: dict | None = Field(None)
    chunk_num: int | None = Field(None)
    progress: float | None = Field(None)
    progress_msg: str | None = Field(None)
    process_begin_at: datetime.datetime | None = Field(None)
    process_duration: float | None = Field(None)
    run: int | None = Field(None)
    status: int | None = Field(None)


class Document(DocumentBase):
    id: uuid.UUID
    chunk_num: int
    progress: float
    progress_msg: str
    process_begin_at: datetime.datetime
    process_duration: float
    run: int
    status: int
    created_at: datetime.datetime
    updated_at: datetime.datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None
    
    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None
    
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("process_begin_at", when_used="json")
    def _serialize_process_begin_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None
