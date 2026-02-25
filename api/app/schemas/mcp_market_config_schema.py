from pydantic import BaseModel, Field, field_serializer, ConfigDict
import datetime
import uuid


class McpMarketConfigBase(BaseModel):
    mcp_market_id: uuid.UUID
    token: str | None = None
    status: int | None = None
    tenant_id: uuid.UUID | None = None
    created_by: uuid.UUID | None = None


class McpMarketConfigCreate(McpMarketConfigBase):
    pass


class McpMarketConfigUpdate(BaseModel):
    token: str | None = None
    status: int | None = None


class McpMarketConfig(McpMarketConfigBase):
    id: uuid.UUID
    created_at: datetime.datetime

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None
