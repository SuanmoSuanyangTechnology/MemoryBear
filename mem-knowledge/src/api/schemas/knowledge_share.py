"""Knowledge share request and response schemas copied from the legacy API."""

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, field_serializer

from ...utils.datetime_utils import to_timestamp_ms
from .knowledge import Knowledge, UserSummary


class WorkspaceSummary(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: str | None = None
    icon: str | None = None
    iconType: str | None = None
    storage_type: str | None = None
    is_default_config: bool = False
    llm: str | None = None
    embedding: str | None = None
    rerank: str | None = None
    vision: str | None = None
    audio: str | None = None
    video: str | None = None
    created_at: datetime.datetime

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, value: datetime.datetime) -> int | None:
        return to_timestamp_ms(value)


class KnowledgeShareBase(BaseModel):
    source_kb_id: uuid.UUID
    source_workspace_id: uuid.UUID | None = None
    target_kb_id: uuid.UUID | None = None
    target_workspace_id: uuid.UUID
    shared_by: uuid.UUID | None = None


class KnowledgeShareCreate(KnowledgeShareBase):
    pass


class KnowledgeShare(KnowledgeShareBase):
    id: uuid.UUID
    created_at: datetime.datetime
    updated_at: datetime.datetime
    target_kb: Knowledge
    target_workspace: WorkspaceSummary
    shared_user: UserSummary

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at", "updated_at", when_used="json")
    def _serialize_time(self, value: datetime.datetime) -> int | None:
        return to_timestamp_ms(value)
