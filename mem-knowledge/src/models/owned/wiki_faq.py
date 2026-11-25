"""Wiki 来源快照、当前 FAQ 和首次生成任务；不保存 FAQ 内容历史。"""

import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import declared_attr

from ...db import KnowledgeBase
from .wiki import SCOPE, WikiScope, scope_fk


class WikiPageVersion(WikiScope, KnowledgeBase):
    __tablename__ = "wiki_page_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    wiki_page_id = Column(UUID(as_uuid=True), nullable=False)
    page_version = Column(BigInteger, nullable=False)
    title = Column(Text, nullable=False)
    summary = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    page_type = Column(String(16), nullable=False)
    aliases = Column(JSONB, nullable=False)
    published_sources = Column(JSONB, nullable=False)
    content_hash = Column(String(64), nullable=False)
    published_at = Column(DateTime, nullable=False)

    @declared_attr
    def __table_args__(cls):
        return (
            UniqueConstraint(*SCOPE, "id", name="uq_wiki_page_version_scope"),
            UniqueConstraint(
                *SCOPE, "wiki_page_id", "page_version", name="uq_wiki_page_version_number"
            ),
            scope_fk("wiki_pages", "wiki_page_id", name="fk_wiki_page_version_page"),
            CheckConstraint("page_version > 0", name="ck_wiki_page_version_positive"),
        )


class WikiFAQGenerationJob(WikiScope, KnowledgeBase):
    __tablename__ = "wiki_faq_generation_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    wiki_page_id = Column(UUID(as_uuid=True), nullable=False)
    trigger_kind = Column(String(24), nullable=False, default="first_publish")
    source_snapshot_id = Column(UUID(as_uuid=True), nullable=False)
    config_snapshot = Column(JSONB, nullable=False)
    config_hash = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="pending")
    publish_confirmed = Column(Boolean, nullable=False, default=False)
    task_id = Column(UUID(as_uuid=True), nullable=False)
    execution_token = Column(UUID(as_uuid=True))
    lease_expires_at = Column(DateTime)
    failure_attempt = Column(Integer, nullable=False, default=0)
    dispatch_attempt = Column(Integer, nullable=False, default=0)
    result_count = Column(Integer)
    error_code = Column(String(80))
    error_stage = Column(String(80))
    row_version = Column(BigInteger, nullable=False, default=1)

    @declared_attr
    def __table_args__(cls):
        return (
            UniqueConstraint(*SCOPE, "id", name="uq_wiki_faq_job_scope"),
            UniqueConstraint(
                *SCOPE, "wiki_page_id", "trigger_kind", name="uq_wiki_faq_first_publish"
            ),
            scope_fk("wiki_pages", "wiki_page_id", name="fk_wiki_faq_job_page"),
            scope_fk("wiki_page_versions", "source_snapshot_id", name="fk_wiki_faq_job_snapshot"),
            CheckConstraint("trigger_kind = 'first_publish'", name="ck_wiki_faq_job_trigger"),
            CheckConstraint(
                "status IN ('pending','running','retry_wait','succeeded','failed','cancelled')",
                name="ck_wiki_faq_job_status",
            ),
            CheckConstraint(
                "failure_attempt >= 0 AND dispatch_attempt >= 0 AND row_version > 0",
                name="ck_wiki_faq_job_counters",
            ),
            Index("ix_wiki_faq_job_dispatch", "kb_id", "status", "publish_confirmed"),
        )


class WikiFAQ(WikiScope, KnowledgeBase):
    __tablename__ = "wiki_faqs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    wiki_page_id = Column(UUID(as_uuid=True))
    origin = Column(String(16), nullable=False)
    generation_job_id = Column(UUID(as_uuid=True))
    generated_wiki_snapshot_id = Column(UUID(as_uuid=True))
    standard_question = Column(Text, nullable=False)
    similar_questions = Column(JSONB, nullable=False, default=list)
    negative_questions = Column(JSONB, nullable=False, default=list)
    answers = Column(JSONB, nullable=False)
    answer_strategy = Column(String(16), nullable=False, default="all")
    content_revision = Column(BigInteger, nullable=False, default=1)
    review_state = Column(String(16), nullable=False)
    approved_wiki_snapshot_id = Column(UUID(as_uuid=True))
    is_enabled = Column(Boolean, nullable=False, default=False)
    row_version = Column(BigInteger, nullable=False, default=1)
    reviewed_by = Column(UUID(as_uuid=True))
    reviewed_at = Column(DateTime)
    last_review_request_id = Column(UUID(as_uuid=True))
    created_by = Column(UUID(as_uuid=True))
    updated_by = Column(UUID(as_uuid=True))
    deleted_at = Column(DateTime)

    @declared_attr
    def __table_args__(cls):
        return (
            UniqueConstraint(*SCOPE, "id", name="uq_wiki_faq_scope"),
            scope_fk("wiki_pages", "wiki_page_id", name="fk_wiki_faq_page"),
            scope_fk("wiki_faq_generation_jobs", "generation_job_id", name="fk_wiki_faq_job"),
            scope_fk(
                "wiki_page_versions",
                "generated_wiki_snapshot_id",
                name="fk_wiki_faq_generated_snapshot",
            ),
            scope_fk(
                "wiki_page_versions",
                "approved_wiki_snapshot_id",
                name="fk_wiki_faq_approved_snapshot",
            ),
            CheckConstraint("origin IN ('auto','manual')", name="ck_wiki_faq_origin"),
            CheckConstraint("review_state IN ('pending','approved')", name="ck_wiki_faq_review"),
            CheckConstraint(
                "review_state != 'pending' OR NOT is_enabled", name="ck_wiki_faq_pending_disabled"
            ),
            CheckConstraint(
                "content_revision > 0 AND row_version > 0", name="ck_wiki_faq_revision"
            ),
            CheckConstraint(
                "jsonb_typeof(answers) = 'array' AND jsonb_array_length(answers) > 0",
                name="ck_wiki_faq_answers",
            ),
            CheckConstraint("answer_strategy IN ('all','random')", name="ck_wiki_faq_strategy"),
            Index("ix_wiki_faq_list", "kb_id", "deleted_at", "updated_at", "id"),
            Index("ix_wiki_faq_page", "wiki_page_id"),
        )
