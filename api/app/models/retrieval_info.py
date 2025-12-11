import datetime
import uuid
from sqlalchemy import Column, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base

class RetrievalInfo(Base):
    __tablename__ = "retrieval_info"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False, index=True)
    host_id = Column(UUID(as_uuid=True), nullable=False)
    retrieve_info = Column(Text, default="", nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.now)
