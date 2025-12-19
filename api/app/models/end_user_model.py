import datetime
import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.db import Base

class EndUser(Base):
    __tablename__ = "end_users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False, index=True)
    app_id = Column(UUID(as_uuid=True), ForeignKey("apps.id"), nullable=False)
    # end_user_id = Column(String, nullable=False, index=True)
    other_id = Column(String, nullable=True)  # Store original user_id
    other_name = Column(String, default="", nullable=False)
    other_address = Column(String, default="", nullable=False)
    reflection_time = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now)

    # 与 App 的反向关系
    app = relationship(
        "App",
        back_populates="end_users"
    )