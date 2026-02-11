import datetime
import uuid
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base

class File(Base):
    __tablename__ = "files"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    kb_id = Column(UUID(as_uuid=True), nullable=False, comment="knowledges.id")
    created_by = Column(UUID(as_uuid=True), nullable=False, comment="users.id")
    parent_id = Column(UUID(as_uuid=True), nullable=True, default=None, comment="parent folder id")
    file_name = Column(String, index=True, nullable=False, comment="file name or folder name,default folder name is /")
    file_ext = Column(String, index=True, nullable=False, comment="file extension:folder|pdf")
    file_size = Column(Integer, default=0, comment="file size(byte)")
    file_url = Column(String, index=True, nullable=True, comment="file comes from a website url")
    created_at = Column(DateTime, default=datetime.datetime.now)