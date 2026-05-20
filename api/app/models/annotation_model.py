import datetime
import uuid
from enum import StrEnum
from sqlalchemy import Column, String, DateTime, ForeignKey, Float, Integer, Text, Index
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import relationship
from app.db import Base


class HitLogSource(StrEnum):
    CONSOLE = "console"
    EXTERNAL = "external"


class AppAnnotation(Base):
    """应用标注表 - 存储应用的QA标注对"""
    __tablename__ = "app_annotations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    app_id = Column(UUID(as_uuid=True), ForeignKey("apps.id", ondelete="CASCADE"), nullable=False, index=True, comment="应用ID")
    workspace_id = Column(UUID(as_uuid=True), nullable=False, index=True, comment="工作空间ID")
    created_by = Column(UUID(as_uuid=True), nullable=False, comment="创建者用户ID")

    # QA标注对
    question = Column(Text, nullable=False, comment="提问")
    answer = Column(Text, nullable=False, comment="答案")

    # Embedding向量（用于相似度匹配）
    embedding = Column(ARRAY(Float), nullable=True, comment="提问的Embedding向量")

    # 命中统计
    hit_count = Column(Integer, default=0, nullable=False, comment="命中次数")

    is_active = Column(Integer, default=1, nullable=False, comment="是否激活: 1=是, 0=否")
    created_at = Column(DateTime, default=datetime.datetime.now, nullable=False, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now, nullable=False, comment="更新时间")

    # 复合索引：加速按应用ID查询
    __table_args__ = (
        Index("idx_annotation_app_active", "app_id", "is_active"),
        Index("idx_annotation_workspace", "workspace_id", "is_active"),
    )

    def __repr__(self):
        return f"<AppAnnotation(id={self.id}, app_id={self.app_id}, question={self.question[:50]}...)>"


class AppAnnotationSetting(Base):
    """应用标注设置表 - 每个应用的标注配置"""
    __tablename__ = "app_annotation_settings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    app_id = Column(UUID(as_uuid=True), ForeignKey("apps.id", ondelete="CASCADE"), nullable=False, unique=True, index=True, comment="应用ID")
    workspace_id = Column(UUID(as_uuid=True), nullable=False, index=True, comment="工作空间ID")

    # 相似度阈值 (0-1, 默认0.85)
    similarity_threshold = Column(Float, default=0.85, nullable=False, comment="相似度阈值")
    # Embedding模型配置ID
    model_config_id = Column(UUID(as_uuid=True), ForeignKey("model_configs.id"), nullable=True, comment="Embedding模型配置ID")
    # 是否启用标注功能
    enabled = Column(Integer, default=0, nullable=False, comment="是否启用: 1=是, 0=否")

    created_at = Column(DateTime, default=datetime.datetime.now, nullable=False, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now, nullable=False, comment="更新时间")

    def __repr__(self):
        return f"<AppAnnotationSetting(app_id={self.app_id}, threshold={self.similarity_threshold}, enabled={self.enabled})>"


class AppAnnotationHitLog(Base):
    """标注命中历史表 - 记录每次标注命中的详情"""
    __tablename__ = "app_annotation_hit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    annotation_id = Column(UUID(as_uuid=True), ForeignKey("app_annotations.id", ondelete="CASCADE"), nullable=False, index=True, comment="标注ID")
    app_id = Column(UUID(as_uuid=True), ForeignKey("apps.id", ondelete="CASCADE"), nullable=False, index=True, comment="应用ID")
    source = Column(Text, nullable=False, comment="来源")
    query = Column(Text, nullable=False, comment="用户提问")
    matched_question = Column(Text, nullable=False, comment="匹配到的标注问题")
    answer = Column(Text, nullable=False, comment="返回的标注答案")
    similarity = Column(Float, nullable=False, comment="相似度分数")
    hit_at = Column(DateTime, default=datetime.datetime.now, nullable=False, comment="命中时间")

    __table_args__ = (
        Index("idx_hit_log_annotation", "annotation_id", "hit_at"),
        Index("idx_hit_log_app", "app_id", "hit_at"),
    )

    def __repr__(self):
        return f"<AppAnnotationHitLog(id={self.id}, annotation_id={self.annotation_id}, similarity={self.similarity})>"
