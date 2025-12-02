from pydantic import BaseModel, Field, field_serializer, ConfigDict
import datetime
import uuid
from .knowledge_schema import Knowledge
from .workspace_schema import Workspace
from .user_schema import User


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
    target_workspace: Workspace
    shared_user: User

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None
