import datetime
import uuid
from sqlalchemy import Column, Integer, String, JSON, DateTime, ForeignKey, Float
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base

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
    parser_id = Column(String, index=True, nullable=False, comment="default parser ID")
    parser_config = Column(JSON, nullable=False,
                           default={
                               "layout_recognize": "DeepDOC",
                               "chunk_token_num": 128,
                               "delimiter": "\n",
                               "auto_keywords": 0,
                               "auto_questions": 0,
                               "html4excel": False,
                               "graphrag": {
                                    "use_graphrag": False,
                                    "entity_types": [
                                        "organization",
                                        "person",
                                        "geo",
                                        "event",
                                        "category"
                                    ],
                                    "method": "general"
                                }
                           }, comment="default parser config")
    chunk_num = Column(Integer, default=0, comment="chunk num")
    progress = Column(Float, default=0)
    progress_msg = Column(String, default="", comment="process message")
    process_begin_at = Column(DateTime, default=datetime.datetime.now)
    process_duration = Column(Float, default=0)
    run = Column(Integer, default=0, comment="start to run processing or cancel.(1: run it; 2: cancel)")
    status = Column(Integer, default=1, comment="is it validate(0: wasted, 1: validate)")
    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now)