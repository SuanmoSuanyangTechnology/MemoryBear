import uuid
import datetime
from sqlalchemy import Column, ForeignKey, Integer, Date, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.db import Base

class MemoryIncrement(Base):
    __tablename__ = "memory_increments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), index=True, nullable=False)
    total_num = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now)

    # 与 App 的关系（指向映射类名，而非表名）
    workspace = relationship("Workspace", back_populates="memory_increments")
