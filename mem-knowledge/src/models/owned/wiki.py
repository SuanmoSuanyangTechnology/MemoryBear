"""Knowledge-owned Wiki tables, migrated exclusively by mem-knowledge."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import declared_attr

from ...db import KnowledgeBase

SCOPE = ("tenant_id", "workspace_id", "kb_id")


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def scope_fk(table: str, local: str, remote: str = "id", **kwargs):
    return ForeignKeyConstraint(
        [*SCOPE, local], [f"{table}.{column}" for column in (*SCOPE, remote)], **kwargs
    )


class WikiScope:
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    kb_id = Column(
        UUID(as_uuid=True), ForeignKey("knowledges.id", ondelete="CASCADE"), nullable=False
    )
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class WikiFolder(WikiScope, KnowledgeBase):
    __tablename__ = "wiki_folders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parent_id = Column(UUID(as_uuid=True))
    name = Column(String(200), nullable=False)
    normalized_name = Column(String(200), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)

    @declared_attr
    def __table_args__(cls):
        return (
            UniqueConstraint(*SCOPE, "id", name="uq_wiki_folder_scope"),
            scope_fk("wiki_folders", "parent_id", name="fk_wiki_folder_parent"),
            Index(
                "uq_wiki_folder_root",
                *SCOPE,
                "normalized_name",
                unique=True,
                postgresql_where=cls.parent_id.is_(None),
            ),
            Index(
                "uq_wiki_folder_child",
                *SCOPE,
                "parent_id",
                "normalized_name",
                unique=True,
                postgresql_where=cls.parent_id.is_not(None),
            ),
        )


class WikiPage(WikiScope, KnowledgeBase):
    __tablename__ = "wiki_pages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    page_type = Column(String(16), nullable=False)
    canonical_key = Column(String(80), nullable=False)
    title = Column(String(400), nullable=False)
    normalized_name = Column(String(400), nullable=False)
    topic_scope = Column(Text, nullable=False, default="")
    aliases = Column(JSONB, nullable=False, default=list)
    folder_id = Column(UUID(as_uuid=True))
    summary = Column(Text, nullable=False, default="")
    content = Column(Text, nullable=False, default="")
    published_sources = Column(JSONB, nullable=False, default=list)
    status = Column(String(24), nullable=False, default="pending")
    version = Column(BigInteger, nullable=False, default=0)
    row_version = Column(BigInteger, nullable=False, default=1)
    failure_attempt = Column(Integer, nullable=False, default=0)
    defer_count = Column(Integer, nullable=False, default=0)
    error_code = Column(String(80))

    @declared_attr
    def __table_args__(cls):
        return (
            UniqueConstraint(*SCOPE, "id", name="uq_wiki_page_scope"),
            UniqueConstraint("kb_id", "canonical_key", name="uq_wiki_page_key"),
            scope_fk("wiki_folders", "folder_id", name="fk_wiki_page_folder"),
            CheckConstraint(
                "page_type IN ('summary', 'entity', 'concept')", name="ck_wiki_page_type"
            ),
            CheckConstraint("version >= 0 AND row_version > 0", name="ck_wiki_page_version"),
            Index("ix_wiki_page_browse", "kb_id", "status", "page_type", "id"),
            Index("ix_wiki_page_identity", "kb_id", "normalized_name", "page_type"),
        )


class WikiDocumentState(WikiScope, KnowledgeBase):
    __tablename__ = "wiki_document_states"

    document_id = Column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    requested_version = Column(BigInteger, nullable=False, default=1)
    mapped_version = Column(BigInteger, nullable=False, default=0)
    source_ready = Column(Boolean, nullable=False, default=False)
    plan_ready = Column(Boolean, nullable=False, default=False)
    stage = Column(String(32), nullable=False, default="waiting_source")
    status = Column(String(24), nullable=False, default="pending")
    processing_mode = Column(String(16), nullable=False, default="wiki_only")
    operation = Column(String(16), nullable=False, default="upsert")
    task_id = Column(String(64))
    source_task_id = Column(String(64))
    admission_id = Column(String(64))
    publish_confirmed = Column(Boolean, nullable=False, default=False)
    input_hash = Column(String(64))
    config_hash = Column(String(64))
    map_progress = Column(JSONB, nullable=False, default=dict)
    failure_attempt = Column(Integer, nullable=False, default=0)
    defer_count = Column(Integer, nullable=False, default=0)
    error_code = Column(String(80))
    error_stage = Column(String(32))
    # Progress already exposed to the UI is a monotonic presentation cache.
    last_progress = Column(Float, nullable=False, default=0.0)

    @declared_attr
    def __table_args__(cls):
        return (
            UniqueConstraint(*SCOPE, "document_id", name="uq_wiki_document_scope"),
            CheckConstraint(
                "requested_version > 0 AND mapped_version >= 0", name="ck_wiki_document_version"
            ),
            Index("ix_wiki_document_admission", "kb_id", "stage", "status", "document_id"),
            Index("ix_wiki_document_handoff", "admission_id", "publish_confirmed"),
        )


class WikiPageSource(WikiScope, KnowledgeBase):
    __tablename__ = "wiki_page_sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    page_id = Column(UUID(as_uuid=True), nullable=False)
    document_id = Column(UUID(as_uuid=True), nullable=False)
    document_wiki_version = Column(BigInteger, nullable=False)
    content_hash = Column(String(64), nullable=False)
    facts = Column(JSONB, nullable=False)
    chunk_refs = Column(JSONB, nullable=False)
    source_context = Column(JSONB, nullable=False)

    @declared_attr
    def __table_args__(cls):
        return (
            UniqueConstraint("kb_id", "page_id", "document_id", name="uq_wiki_source"),
            scope_fk("wiki_pages", "page_id", name="fk_wiki_source_page", ondelete="CASCADE"),
            scope_fk(
                "wiki_document_states",
                "document_id",
                "document_id",
                name="fk_wiki_source_document",
                ondelete="CASCADE",
            ),
            Index("ix_wiki_source_document", "document_id", "page_id"),
        )


class WikiPageLink(WikiScope, KnowledgeBase):
    __tablename__ = "wiki_page_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_page_id = Column(UUID(as_uuid=True), nullable=False)
    target_page_id = Column(UUID(as_uuid=True), nullable=False)

    @declared_attr
    def __table_args__(cls):
        return (
            UniqueConstraint("kb_id", "source_page_id", "target_page_id", name="uq_wiki_link"),
            scope_fk(
                "wiki_pages", "source_page_id", name="fk_wiki_link_source", ondelete="CASCADE"
            ),
            scope_fk(
                "wiki_pages", "target_page_id", name="fk_wiki_link_target", ondelete="CASCADE"
            ),
            CheckConstraint("source_page_id <> target_page_id", name="ck_wiki_link_not_self"),
            Index("ix_wiki_link_reverse", "kb_id", "target_page_id"),
        )


class WikiDocumentPageTask(WikiScope, KnowledgeBase):
    __tablename__ = "wiki_document_page_tasks"

    subtask_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), nullable=False)
    requested_version = Column(BigInteger, nullable=False)
    page_id = Column(UUID(as_uuid=True), nullable=False)
    operation = Column(String(16), nullable=False)
    status = Column(String(24), nullable=False, default="pending")
    failure_attempt = Column(Integer, nullable=False, default=0)
    defer_count = Column(Integer, nullable=False, default=0)
    error_code = Column(String(80))
    error_stage = Column(String(32))

    @declared_attr
    def __table_args__(cls):
        return (
            UniqueConstraint(
                "document_id", "requested_version", "page_id", name="uq_wiki_document_page_task"
            ),
            scope_fk(
                "wiki_document_states",
                "document_id",
                "document_id",
                name="fk_wiki_task_document",
                ondelete="CASCADE",
            ),
            scope_fk("wiki_pages", "page_id", name="fk_wiki_task_page", ondelete="CASCADE"),
            CheckConstraint("operation IN ('upsert', 'retract')", name="ck_wiki_task_operation"),
            Index("ix_wiki_task_page", "kb_id", "page_id", "status"),
            Index("ix_wiki_task_document", "document_id", "requested_version", "status"),
        )
