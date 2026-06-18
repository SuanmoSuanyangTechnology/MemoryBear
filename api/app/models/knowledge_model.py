import datetime
import uuid
import enum
from sqlalchemy import Column, Integer, String, JSON, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base
from app.core.utils.datetime_utils import utcnow_naive
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
    external_id = Column(String(36), nullable=True, index=True, unique=False,
                         comment="user-defined external identifier, workspace-unique")
    workspace_id = Column(UUID(as_uuid=True), nullable=False, comment="workspaces.id")
    created_by = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False, comment="users.id")
    parent_id = Column(UUID(as_uuid=True), nullable=True, default=None, comment="parent folder id when type is Folder")
    name = Column(String, index=True, nullable=False, comment="KB name")
    description = Column(String, comment="KB description")
    avatar = Column(String, comment="avatar url")
    type = Column(String, default="General", comment="Type:General|Web|Third-party|Folder")
    permission_id = Column(String, default="Private", comment="permission ID:Private|Share|Memory")
    embedding_id = Column(UUID(as_uuid=True), ForeignKey('model_configs.id', ondelete="SET NULL"), nullable=True,
                          comment="default embedding model ID")
    reranker_id = Column(UUID(as_uuid=True), ForeignKey('model_configs.id', ondelete="SET NULL"), nullable=True,
                         comment="default reranker model ID")
    llm_id = Column(UUID(as_uuid=True), ForeignKey('model_configs.id', ondelete="SET NULL"), nullable=True,
                    comment="default llm model ID")
    image2text_id = Column(UUID(as_uuid=True), ForeignKey('model_configs.id', ondelete="SET NULL"), nullable=True,
                           comment="default image2text model ID")
    doc_num = Column(Integer, default=0, comment="doc num")
    chunk_num = Column(Integer, default=0, comment="chunk num")
    parser_id = Column(String, index=True, default="naive", comment="default parser ID")
    parser_config = Column(JSON, nullable=False,
                           default={
                               "entry_url": "https://ai.redbearai.com",
                               "max_pages": 20,
                               "delay_seconds": 1.0,
                               "timeout_seconds": 10,
                               "user_agent": "KnowledgeBaseCrawler/1.0",
                               "yuque_user_id": "User ID",
                               "yuque_token": "Token",
                               "feishu_app_id": "App ID",
                               "feishu_app_secret": "App Secret",
                               "feishu_folder_token": "Folder Token",
                               "sync_cron": "30 7 * * 1-5",
                               "layout_recognize": "DeepDOC",
                               "chunk_token_num": 128,
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
                           },
                           comment="default parser config")
    status = Column(Integer, index=True, default=1, comment="is it validate(0: disable, 1: enable, 2:Soft-delete)")
    builtin_metadata_enabled = Column(Integer, default=0, nullable=False, server_default='0',
                                      comment="builtin metadata switch (0: disabled, 1: enabled)")
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive)

    # Relationships
    created_user = relationship("User", backref="created_user")
    embedding = relationship("ModelConfig", foreign_keys=[embedding_id], uselist=False, backref="embedding")
    reranker = relationship("ModelConfig", foreign_keys=[reranker_id], uselist=False, backref="reranker")
    llm = relationship("ModelConfig", foreign_keys=[llm_id], uselist=False, backref="llm")
    image2text = relationship("ModelConfig", foreign_keys=[image2text_id], uselist=False, backref="image2text")

    @property
    def is_folder(self) -> bool:
        return self.type == KnowledgeType.FOLDER

    @property
    def is_active(self) -> bool:
        return self.status == 1

    @property
    def is_retrievable_leaf(self) -> bool:
        return self.is_active and not self.is_folder and (self.chunk_num or 0) > 0

    @property
    def chunk_mode(self) -> int:
        """获取知识库是否已经使用父子分块模式"""
        # 1. None 还为设置模式
        if "auto_questions" not in self.parser_config and "parent_chunk_mode" not in self.parser_config:
            return 0
        # 2. 此时为通用分块模式
        elif "auto_questions" in self.parser_config and "parent_chunk_mode" not in self.parser_config and not self.parser_config.get(
                "parent_child_mode", False):
            return 1
        # 3. 此时为父子分块模式
        else:
            return 2
