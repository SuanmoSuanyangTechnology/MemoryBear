import datetime
import uuid
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base

class McpMarketConfig(Base):
    __tablename__ = "mcp_market_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    mcp_market_id = Column(UUID(as_uuid=True), nullable=False, comment="mcp_markets.id")
    token = Column(String, nullable=True, comment="mcp market token")
    status = Column(Integer, default=0, comment="connect status(0: Not connected, 1: connected)")
    tenant_id = Column(UUID(as_uuid=True), nullable=False, comment="tenant.id")
    created_by = Column(UUID(as_uuid=True), nullable=False, comment="users.id")
    created_at = Column(DateTime, default=datetime.datetime.now)