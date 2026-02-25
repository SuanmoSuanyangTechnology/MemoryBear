import datetime
import uuid
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base

class McpMarket(Base):
    __tablename__ = "mcp_markets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String, index=True, nullable=False, comment="mcp market name")
    description = Column(String, index=True, nullable=True, comment="mcp market description")
    logo_url = Column(String, index=True, nullable=True, comment="logo url")
    mcp_count = Column(Integer, default=1, comment="mcp count")
    url = Column(String, index=True, nullable=False, comment="mcp market url")
    category = Column(String, index=True, nullable=False, comment="category")
    created_by = Column(UUID(as_uuid=True), nullable=False, comment="users.id")
    created_at = Column(DateTime, default=datetime.datetime.now)