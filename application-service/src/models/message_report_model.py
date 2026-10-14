"""
Message report model (with text-selection feedback and review)
"""
import uuid
import datetime
from enum import StrEnum

from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .base import ServiceBase as Base
from ..utils.datetime_utils import utcnow_naive


class MessageReportType(StrEnum):
    """Report type enum"""
    POLITICS = "politics"      # Politically sensitive
    PORN = "porn"              # Pornographic
    VIOLENCE = "violence"      # Violence
    FAKE = "fake"              # Misinformation
    AD = "ad"                  # Advertising
    QUALITY = "quality"        # Inaccurate content
    OTHER = "other"            # Other

    @classmethod
    def get_all_types(cls):
        """Get all report types"""
        return [report_type.value for report_type in cls]

    @classmethod
    def get_all_types_with_labels(cls):
        """Get all report types with their text labels"""
        return [
            {"value": cls.POLITICS.value, "label": "涉政"},
            {"value": cls.PORN.value, "label": "色情"},
            {"value": cls.VIOLENCE.value, "label": "暴力"},
            {"value": cls.FAKE.value, "label": "虚假信息"},
            {"value": cls.AD.value, "label": "广告"},
            {"value": cls.QUALITY.value, "label": "内容不准确"},
            {"value": cls.OTHER.value, "label": "其他"}
        ]


class MessageReport(Base):
    """Message report table (with text-selection feedback and review)

    Supported features:
    - Text-selection feedback: a user selects a span of text and submits feedback
    - Report: flag inappropriate content
    - Review workflow: the platform classifies report data, triages severity, and analyzes trends
    """
    __tablename__ = "message_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id = Column(UUID(as_uuid=True), ForeignKey("messages.id"), nullable=False, comment="消息ID")
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False, comment="会话ID")
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False, comment="工作空间ID")
    reported_by = Column(UUID(as_uuid=True), nullable=False, comment="举报人ID")

    # Report type
    report_type = Column(String(50), nullable=False, comment="举报类型: politics/porn/violence/fake/ad/quality/other")
    report_reason = Column(Text, comment="举报原因详细描述")

    # Selected-text positions (supports text-selection feedback)
    text_start_offset = Column(Integer, comment="选中文本起始位置")
    text_end_offset = Column(Integer, comment="选中文本结束位置")
    selected_text = Column(Text, comment="选中的违规文本片段")

    # Review status
    status = Column(String(20), server_default="pending", comment="状态: pending/reviewing/resolved/rejected")
    severity = Column(String(20), comment="严重程度: low/medium/high/critical")
    reviewer_id = Column(UUID(as_uuid=True), comment="审核人ID")
    review_note = Column(Text, comment="审核备注")
    reviewed_at = Column(DateTime, comment="审核时间")

    # Action taken
    action_taken = Column(String(50), comment="处理措施: warning/content_removed/user_banned/no_action")

    created_at = Column(DateTime, default=utcnow_naive, comment="创建时间")
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, comment="更新时间")

    # Relationships
    message = relationship("Message", back_populates="reports")
    conversation = relationship("Conversation")
