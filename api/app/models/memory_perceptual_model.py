import datetime
import uuid
from enum import IntEnum

from sqlalchemy import Column, ForeignKey, Integer, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.dialects.postgresql import JSONB

from app.db import Base


class PerceptualType(IntEnum):
    VISION = 1
    AUDIO = 2
    TEXT = 3
    CONVERSATION = 4


class FileStorageService(IntEnum):
    LOCAL = 1
    REMOTE = 2


class MemoryPerceptualModel(Base):
    __tablename__ = "memory_perceptual"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    end_user_id = Column(UUID(as_uuid=True), ForeignKey("end_users.id"), index=True)

    perceptual_type = Column(Integer, index=True, nullable=False, comment="感知类型")

    storage_service = Column(Integer, default=0, comment="存储服务类型")
    file_path = Column(String, nullable=False, comment="文件路径")
    file_name = Column(String, nullable=False, comment="文件名称")
    file_ext = Column(String, nullable=False, comment="文件后缀名")

    summary = Column(String, comment="摘要")
    meta_data = Column(JSONB, comment="元信息")

    created_time = Column(DateTime, default=datetime.datetime.now, comment="创建时间")
