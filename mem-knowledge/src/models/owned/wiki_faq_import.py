"""FAQ 导入任务与持久进度；载荷不注册为知识库文件。"""

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from ...db import KnowledgeBase
from .wiki import WikiScope


class WikiFAQImportJob(WikiScope, KnowledgeBase):
    __tablename__ = "wiki_faq_import_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True)
    actor_id = Column(UUID(as_uuid=True), nullable=False)
    mode = Column(String(16), nullable=False)
    status = Column(String(24), nullable=False)
    stage = Column(String(16), nullable=False)
    payload_key = Column(Text, nullable=False)
    total_count = Column(BigInteger, nullable=False)
    processed_count = Column(BigInteger, nullable=False)
    candidates = Column(JSONB, nullable=False)
    failed_entries = Column(JSONB, nullable=False)
    cleanup_ids = Column(JSONB, nullable=False)
    summary = Column(JSONB)
    failure = Column(JSONB)
    model_key = Column(String(64))
    execution_token = Column(UUID(as_uuid=True))
    lease_expires_at = Column(DateTime)

    __table_args__ = (
        CheckConstraint("mode IN ('append','replace')", name="ck_faq_import_mode"),
        CheckConstraint(
            "status IN ('pending','running','succeeded','partially_succeeded','failed')",
            name="ck_faq_import_status",
        ),
        CheckConstraint(
            "stage IN ('queued','validating','indexing','writing','finished')",
            name="ck_faq_import_stage",
        ),
        CheckConstraint(
            "total_count > 0 AND processed_count >= 0 AND processed_count <= total_count",
            name="ck_faq_import_counts",
        ),
    )
