from pydantic import BaseModel, Field, field_serializer, ConfigDict
import datetime
import uuid
from app.core.utils.datetime_utils import to_timestamp_ms


class McpMarketBase(BaseModel):
    name: str
    description: str | None = None
    logo_url: str | None = None
    mcp_count: int
    url: str
    category: str
    created_by: uuid.UUID | None = None


class McpMarketCreate(McpMarketBase):
    pass


class McpMarketUpdate(BaseModel):
    name: str | None = Field(None)
    description: str | None = Field(None)
    logo_url: str | None = Field(None)
    mcp_count: int | None = Field(None)
    url: str | None = Field(None)
    category: str | None = Field(None)


class McpMarket(McpMarketBase):
    id: uuid.UUID
    created_at: datetime.datetime

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)
