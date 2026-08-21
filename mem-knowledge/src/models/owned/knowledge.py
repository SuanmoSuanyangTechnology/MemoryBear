"""Knowledge model copied from the legacy API service."""

import enum
import uuid

from sqlalchemy import JSON, Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from ...db import KnowledgeBase
from ...rag.parser_config import build_default_knowledge_parser_config
from ...utils.datetime_utils import utcnow_naive


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

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    external_id = Column(
        String(36),
        nullable=True,
        index=True,
        unique=False,
        comment="user-defined external identifier, workspace-unique",
    )
    workspace_id = Column(UUID(as_uuid=True), nullable=False, comment="workspaces.id")
    created_by = Column(UUID(as_uuid=True), nullable=False, comment="users.id")
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
    permission_id = Column(
        String,
        default="Private",
        comment="permission ID:Private|Share|Memory",
    )
    embedding_id = Column(
        UUID(as_uuid=True),
        nullable=True,
        comment="default embedding model ID",
    )
    reranker_id = Column(
        UUID(as_uuid=True),
        nullable=True,
        comment="default reranker model ID",
    )
    llm_id = Column(UUID(as_uuid=True), nullable=True, comment="default llm model ID")
    image2text_id = Column(
        UUID(as_uuid=True),
        nullable=True,
        comment="default image2text model ID",
    )
    doc_num = Column(Integer, default=0, comment="doc num")
    chunk_num = Column(Integer, default=0, comment="chunk num")
    parser_id = Column(String, index=True, default="naive", comment="default parser ID")
    parser_config = Column(
        JSON,
        nullable=False,
        default=build_default_knowledge_parser_config,
        comment="default parser config",
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

        if (
            "auto_questions" not in self.parser_config
            and "parent_chunk_mode" not in self.parser_config
        ):
            return 0
        if (
            "auto_questions" in self.parser_config
            and "parent_chunk_mode" not in self.parser_config
            and not self.parser_config.get("parent_child_mode", False)
        ):
            return 1
        return 2
