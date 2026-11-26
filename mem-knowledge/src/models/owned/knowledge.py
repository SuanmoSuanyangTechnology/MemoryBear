"""Knowledge model copied from the legacy API service."""

import enum
import uuid

from sqlalchemy import JSON, CheckConstraint, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from ...db import KnowledgeBase
from ...parser_configuration.schema import INDEX_STRATEGY_CHECK, SOURCE_TYPE_CHECK
from ...rag.parser_config import build_default_knowledge_parser_config
from ...utils.datetime_utils import utcnow_naive
from ..references.model_registry import ModelConfig
from ..references.user import User


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


class Knowledge(KnowledgeBase):
    __tablename__ = "knowledges"
    __table_args__ = (
        CheckConstraint(SOURCE_TYPE_CHECK, name="ck_knowledges_source_type"),
        CheckConstraint(INDEX_STRATEGY_CHECK, name="ck_knowledges_index_strategy"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    external_id = Column(
        String(36),
        nullable=True,
        index=True,
        unique=False,
        comment="user-defined external identifier, workspace-unique",
    )
    workspace_id = Column(UUID(as_uuid=True), nullable=False, comment="workspaces.id")
    created_by = Column(
        UUID(as_uuid=True), ForeignKey(User.__table__.c.id), nullable=False, comment="users.id",
    )
    parent_id = Column(
        UUID(as_uuid=True),
        nullable=True,
        default=None,
        comment="parent folder id when type is Folder",
    )
    name = Column(String, index=True, nullable=False, comment="KB name")
    description = Column(String, comment="KB description")
    avatar = Column(String, comment="avatar url")
    type = Column(String, default="General", comment="Type:General|Web|Third-party|Folder")
    source = Column(String(16), nullable=True, comment="来源；Folder 为空，存储 type 保持不变")
    index_strategy = Column(String(16), nullable=True, comment="索引策略；Folder 为空")
    permission_id = Column(
        String,
        default="Private",
        comment="permission ID:Private|Share|Memory",
    )
    embedding_id = Column(
        UUID(as_uuid=True),
        ForeignKey(ModelConfig.__table__.c.id, ondelete="SET NULL"),
        nullable=True,
        comment="default embedding model ID",
    )
    reranker_id = Column(
        UUID(as_uuid=True),
        ForeignKey(ModelConfig.__table__.c.id, ondelete="SET NULL"),
        nullable=True,
        comment="default reranker model ID",
    )
    llm_id = Column(
        UUID(as_uuid=True),
        ForeignKey(ModelConfig.__table__.c.id, ondelete="SET NULL"),
        nullable=True,
        comment="default llm model ID",
    )
    image2text_id = Column(
        UUID(as_uuid=True),
        ForeignKey(ModelConfig.__table__.c.id, ondelete="SET NULL"),
        nullable=True,
        comment="default image2text model ID",
    )
    audio2text_id = Column(
        UUID(as_uuid=True),
        ForeignKey(ModelConfig.__table__.c.id, ondelete="SET NULL"),
        nullable=True,
        comment="audio transcription model ID",
    )
    video2text_id = Column(
        UUID(as_uuid=True),
        ForeignKey(ModelConfig.__table__.c.id, ondelete="SET NULL"),
        nullable=True,
        comment="video understanding model ID",
    )
    doc_num = Column(Integer, default=0, comment="doc num")
    chunk_num = Column(Integer, default=0, comment="chunk num")
    parser_id = Column(String, index=True, default="naive", comment="default parser ID")
    legacy_parser_config_snapshot = Column(
        "parser_config",
        JSON,
        nullable=False,
        default=build_default_knowledge_parser_config,
        comment="Rollback snapshot; effective configuration lives in knowledge_parser_configs",
    )
    status = Column(
        Integer,
        index=True,
        default=1,
        comment="is it validate(0: disable, 1: enable, 2:Soft-delete)",
    )
    builtin_metadata_enabled = Column(
        Integer,
        default=0,
        nullable=False,
        server_default="0",
        comment="builtin metadata switch (0: disabled, 1: enabled)",
    )
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive)
    configuration = relationship(
        "KnowledgeParserConfig", uselist=False, lazy="selectin", viewonly=True
    )

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
        """Return the legacy knowledge chunk policy state."""

        if self.configuration is None:
            raise ValueError("Knowledge configuration has not been migrated")
        return {None: 0, "default": 1, "custom": 1, "parent_child": 2}[
            self.configuration.chunk_method
        ]
