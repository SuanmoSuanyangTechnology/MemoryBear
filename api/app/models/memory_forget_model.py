from enum import IntEnum
import uuid

from sqlalchemy import Column, DateTime, Integer, String, Boolean
from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class ForgetTrigger(IntEnum):
    Manual = 1
    Scheduled = 2


class ForgetAuditModel(Base):
    __tablename__ = 'memory_forget_log'

    id = Column(UUID(as_uuid=True), default=uuid.uuid4, primary_key=True)
    end_user_id = Column(UUID(as_uuid=True), ForeignKey("end_users.id"), index=True, nullable=False)
    node_id = Column(String, nullable=False, index=True)
    node_type = Column(String, nullable=False, index=True)
    content = Column(String, nullable=False)
    trigger = Column(Integer, nullable=False)
    reason = Column(String, nullable=False)
    recoverable = Column(Boolean, nullable=False)
    operator = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    delete_at = Column(DateTime, nullable=False)
    is_recovered = Column(Boolean, default=False, nullable=False)
