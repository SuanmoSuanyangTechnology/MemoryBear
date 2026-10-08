"""Knowledge document model copied from the legacy API service."""

import uuid
from typing import Any

from sqlalchemy import JSON, Column, DateTime, Float, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID

from ...db import KnowledgeBase
from ...rag.parser_config import build_default_document_parser_config
from ...utils.datetime_utils import utcnow_naive


def _parse_bool_config(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y", "on"}
    return bool(value)


class Document(KnowledgeBase):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    kb_id = Column(UUID(as_uuid=True), nullable=False, comment="knowledges.id")
    created_by = Column(UUID(as_uuid=True), nullable=False, comment="users.id")
    file_id = Column(UUID(as_uuid=True), nullable=False, comment="files.id")
    file_name = Column(String, index=True, nullable=False, comment="file name")
    file_ext = Column(String, index=True, nullable=False, comment="file extension")
    file_size = Column(Integer, default=0, comment="file size(byte)")
    file_meta = Column(JSON, nullable=False, default={})
    meta_data = Column(
        "meta_data",
        JSONB,
        nullable=False,
        default={},
        server_default="{}",
        comment="{field_name: value}",
    )
    parser_id = Column(String, index=True, nullable=False, comment="default parser ID")
    parser_config = Column(
        JSON,
        nullable=False,
        default=build_default_document_parser_config,
        comment="default parser config",
    )
    chunk_num = Column(Integer, default=0, comment="chunk num")
    progress = Column(Float, default=0)
    progress_msg = Column(String, default="", comment="process message")
    process_begin_at = Column(DateTime, default=utcnow_naive)
    process_duration = Column(Float, default=0)
    run = Column(
        Integer,
        default=0,
        comment="start to run processing or cancel.(1: run it; 2: cancel)",
    )
    status = Column(Integer, default=1, comment="is it validate(0: wasted, 1: validate)")
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive)

    @property
    def parent_child_mode(self) -> bool:
        """Return whether the document uses parent-child chunk mode."""

        parser_config = self.parser_config or {}
        if "parent_child_mode" in parser_config:
            return _parse_bool_config(parser_config.get("parent_child_mode"))
        return parser_config.get("parent_chunk_mode") in ["paragraph", "full-doc"]

    @property
    def is_parent_child_mode(self) -> bool:
        """Return whether the document uses parent-child chunk mode."""

        return self.parent_child_mode
