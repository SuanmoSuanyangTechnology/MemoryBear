# -*- coding: utf-8 -*-
"""偏好类型模型

本模块定义偏好引擎（Coding Agent 偏好）的偏好类型表。

Classes:
    OntologyPreferenceScene: 偏好域表模型（workspace 级）
    OntologyPreferenceClass: 偏好类别 / 适用任务表模型（随偏好域级联）
"""

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.utils.datetime_utils import utcnow_naive
from app.db import Base


class OntologyPreferenceScene(Base):
    """偏好域表 - 一个工作空间下的一种偏好类型（本期只有 coding）。"""

    __tablename__ = "ontology_preference_scene"
    __table_args__ = (
        UniqueConstraint("workspace_id", "domain", name="uq_preference_scene_workspace_domain"),
        Index("ix_ontology_preference_scene_workspace", "workspace_id"),
    )

    preference_scene_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, comment="偏好域ID")
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "workspaces.id",
            ondelete="CASCADE",
            name="fk_ontology_preference_scene_workspace_id",
        ),
        nullable=False,
        comment="所属工作空间ID",
    )
    domain = Column(String(50), nullable=False, comment="偏好域 key，本期只有 coding")
    display_name_zh = Column(String(100), nullable=False, comment="中文展示名")
    display_name_en = Column(String(100), nullable=False, comment="英文展示名")
    note_zh = Column(Text, nullable=True, comment="中文提示语")
    note_en = Column(Text, nullable=True, comment="英文提示语")
    allowed_modes = Column(
        JSONB,
        nullable=False,
        default=lambda: ["positive", "negative", "relative"],
        server_default=text("'[\"positive\",\"negative\",\"relative\"]'::jsonb"),
        comment="引擎允许的偏好模式",
    )
    is_system_default = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="是否系统种子",
    )
    created_at = Column(DateTime, default=utcnow_naive, nullable=False, comment="创建时间")
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False, comment="更新时间")

    classes = relationship(
        "OntologyPreferenceClass",
        back_populates="scene",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self):
        return f"<OntologyPreferenceScene(id={self.preference_scene_id}, domain={self.domain})>"


class OntologyPreferenceClass(Base):
    """偏好类别 / 适用任务表 - 引擎 prompt 语义与页面展示文案的唯一事实源。"""

    __tablename__ = "ontology_preference_class"
    __table_args__ = (
        UniqueConstraint(
            "preference_scene_id", "item_type", "item_key",
            name="uq_preference_class_scene_type_key",
        ),
        Index(
            "ix_preference_class_scene_type_order",
            "preference_scene_id",
            "item_type",
            "sort_order",
        ),
    )

    preference_class_id = Column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, comment="类别/任务ID"
    )
    preference_scene_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "ontology_preference_scene.preference_scene_id",
            ondelete="CASCADE",
            name="fk_ontology_preference_class_scene_id",
        ),
        nullable=False,
        index=True,
        comment="所属偏好域ID",
    )
    item_type = Column(String(20), nullable=False, comment="条目类型：subject / situation")
    item_key = Column(String(50), nullable=False, comment="机器名，与本体 JSON 的 key 一致")
    label_zh = Column(String(100), nullable=False, comment="中文展示名")
    label_en = Column(String(100), nullable=False, comment="英文展示名")
    display_description_zh = Column(Text, nullable=True, comment="中文页面说明；仅 subject 使用")
    display_description_en = Column(Text, nullable=True, comment="英文页面说明；仅 subject 使用")
    semantic_description = Column(Text, nullable=True, comment="引擎 prompt 语义描述")
    semantic_includes = Column(Text, nullable=True, comment="引擎 prompt 应收范围；仅 subject")
    semantic_boundary = Column(Text, nullable=True, comment="引擎 prompt 语义边界")
    sort_order = Column(Integer, nullable=False, comment="同类型内顺序，从 1 开始")
    is_system_default = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="是否系统种子",
    )
    created_at = Column(DateTime, default=utcnow_naive, nullable=False, comment="创建时间")
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False, comment="更新时间")

    scene = relationship("OntologyPreferenceScene", back_populates="classes")

    def __repr__(self):
        return (
            f"<OntologyPreferenceClass(id={self.preference_class_id}, "
            f"type={self.item_type}, key={self.item_key})>"
        )
