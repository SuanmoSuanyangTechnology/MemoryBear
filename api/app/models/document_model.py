import datetime
import uuid
from sqlalchemy import Column, Integer, String, JSON, DateTime, ForeignKey, Float
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base
from app.core.utils.datetime_utils import utcnow_naive


class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    kb_id = Column(UUID(as_uuid=True), nullable=False, comment="knowledges.id")
    created_by = Column(UUID(as_uuid=True), nullable=False, comment="users.id")
    file_id = Column(UUID(as_uuid=True), nullable=False, comment="files.id")
    file_name = Column(String, index=True, nullable=False, comment="file name")
    file_ext = Column(String, index=True, nullable=False, comment="file extension")
    file_size = Column(Integer, default=0, comment="file size(byte)")
    file_meta = Column(JSON, nullable=False, default={})
    meta_data = Column("meta_data", JSONB, nullable=False, default={}, server_default='{}',
                       comment="{field_name: value}")
    parser_id = Column(String, index=True, nullable=False, comment="default parser ID")
    parser_config = Column(JSON, nullable=False,
                           default={
                               "layout_recognize": "DeepDOC",
                               "chunk_token_num": 130,
                               "delimiter": "\n",
                               "auto_keywords": 0,
                               "auto_questions": 0,
                               "html4excel": False,
                               "parent_child_mode": False,
                               "parent_chunk_token_num": 1024,
                               "parent_delimiter": "\n",
                               "graphrag": {
                                   "use_graphrag": False,
                                   "scene_name": "",
                                   "entity_types": [
                                       "organization",
                                       "person",
                                       "geo",
                                       "event",
                                       "category"
                                   ],
                                   "method": "general",
                                   "resolution": True,
                                   "community": True
                               }
                           }, comment="default parser config")
    chunk_num = Column(Integer, default=0, comment="chunk num")
    progress = Column(Float, default=0)
    progress_msg = Column(String, default="", comment="process message")
    process_begin_at = Column(DateTime, default=utcnow_naive)
    process_duration = Column(Float, default=0)
    run = Column(Integer, default=0, comment="start to run processing or cancel.(1: run it; 2: cancel)")
    status = Column(Integer, default=1, comment="is it validate(0: wasted, 1: validate)")
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive)

    @property
    def is_parent_child_mode(self) -> bool:
        """获取文档是否使用父子分块模式"""
        # parent_child_mode 显式存在时，以它的值为准
        if "parent_child_mode" in self.parser_config:
            return self.parser_config["parent_child_mode"]
        # 不存在时，fallback 到 parent_chunk_mode 判断
        return self.parser_config.get("parent_chunk_mode", None) in ["paragraph", "full-doc"]
