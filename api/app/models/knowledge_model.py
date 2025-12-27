import datetime
import uuid
import enum
from sqlalchemy import Column, Integer, String, JSON, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base
from sqlalchemy.orm import relationship


class KnowledgeType(enum.StrEnum):
    General = "General"
    Web = "Web"
    ThirdParty = "Third-party"
    FOLDER = "Folder"


class ParserType(enum.StrEnum):
    NAIVE = "naive"
    QA = "qa"
    MANUAL = "manual"
    TABLE = "table"
    PRESENTATION = "presentation"
    LAWS = "laws"
    PAPER = "paper"
    RESUME = "resume"
    BOOK = "book"
    ONE = "one"
    AUDIO = "audio"
    EMAIL = "email"
    TAG = "tag"
    KG = "knowledge_graph"


class PermissionType(enum.StrEnum):
    Private = "Private"
    Share = "Share"
    Memory = "Memory"

class Knowledge(Base):
    __tablename__ = "knowledges"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    workspace_id = Column(UUID(as_uuid=True), nullable=False, comment="workspaces.id")
    created_by = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False, comment="users.id")
    parent_id = Column(UUID(as_uuid=True), nullable=True, default=None, comment="parent folder id when type is Folder")
    name = Column(String, index=True, nullable=False, comment="KB name")
    description = Column(String, comment="KB description")
    avatar = Column(String, comment="avatar url")
    type = Column(String, default="General", comment="Type:General|Web|Third-party|Folder")
    permission_id = Column(String, default="Private", comment="permission ID:Private|Share|Memory")
    embedding_id = Column(UUID(as_uuid=True), ForeignKey('model_configs.id', ondelete="SET NULL"), nullable=True, comment="default embedding model ID")
    reranker_id = Column(UUID(as_uuid=True), ForeignKey('model_configs.id', ondelete="SET NULL"), nullable=True, comment="default reranker model ID")
    llm_id = Column(UUID(as_uuid=True), ForeignKey('model_configs.id', ondelete="SET NULL"), nullable=True, comment="default llm model ID")
    image2text_id = Column(UUID(as_uuid=True), ForeignKey('model_configs.id', ondelete="SET NULL"), nullable=True, comment="default image2text model ID")
    doc_num = Column(Integer, default=0, comment="doc num")
    chunk_num = Column(Integer, default=0, comment="chunk num")
    parser_id = Column(String, index=True, default="naive", comment="default parser ID")
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
                           },
                           comment="default parser config")
    status = Column(Integer, index=True, default=1, comment="is it validate(0: disable, 1: enable, 2:Soft-delete)")
    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now)

    # Relationships
    created_user = relationship("User", backref="created_user")
    embedding = relationship("ModelConfig", foreign_keys=[embedding_id], uselist=False, backref="embedding")
    reranker = relationship("ModelConfig", foreign_keys=[reranker_id], uselist=False, backref="reranker")
    llm = relationship("ModelConfig", foreign_keys=[llm_id], uselist=False, backref="llm")
    image2text = relationship("ModelConfig", foreign_keys=[image2text_id], uselist=False, backref="image2text")
