"""
消息举报模型（含选中反馈和审核）
"""
import uuid
import datetime
from enum import StrEnum

from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db import Base


class MessageReportType(StrEnum):
    """举报类型枚举"""
    POLITICS = "politics"      # 涉政
    PORN = "porn"              # 色情
    VIOLENCE = "violence"      # 暴力
    FAKE = "fake"              # 虚假信息
    AD = "ad"                  # 广告
    QUALITY = "quality"        # 内容不准确
    OTHER = "other"            # 其他

    @classmethod
    def get_all_types(cls):
        """获取所有举报类型"""
        return [report_type.value for report_type in cls]

    @classmethod
    def get_all_types_with_labels(cls):
        """获取所有举报类型及其文本描述"""
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
    """消息举报表（含选中反馈和审核）
    
    支持功能：
    - 选中反馈：用户选中某段文本提交反馈
    - 举报：标记不当内容
    - 审核流程：平台侧对举报数据进行分类、严重度分拣、趋势分析
    """
    __tablename__ = "message_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id = Column(UUID(as_uuid=True), ForeignKey("messages.id"), nullable=False, comment="消息ID")
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False, comment="会话ID")
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False, comment="工作空间ID")
    reported_by = Column(UUID(as_uuid=True), nullable=False, comment="举报人ID")

    # 举报类型
    report_type = Column(String(50), nullable=False, comment="举报类型: politics/porn/violence/fake/ad/quality/other")
    report_reason = Column(Text, comment="举报原因详细描述")

    # 选中文本定位（支持选中反馈）
    text_start_offset = Column(Integer, comment="选中文本起始位置")
    text_end_offset = Column(Integer, comment="选中文本结束位置")
    selected_text = Column(Text, comment="选中的违规文本片段")

    # 审核状态
    status = Column(String(20), server_default="pending", comment="状态: pending/reviewing/resolved/rejected")
    severity = Column(String(20), comment="严重程度: low/medium/high/critical")
    reviewer_id = Column(UUID(as_uuid=True), comment="审核人ID")
    review_note = Column(Text, comment="审核备注")
    reviewed_at = Column(DateTime, comment="审核时间")

    # 处理结果
    action_taken = Column(String(50), comment="处理措施: warning/content_removed/user_banned/no_action")

    created_at = Column(DateTime, default=datetime.datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now, comment="更新时间")

    # 关联关系
    message = relationship("Message", back_populates="reports")
    conversation = relationship("Conversation")
