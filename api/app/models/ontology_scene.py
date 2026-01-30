# -*- coding: utf-8 -*-
"""本体场景模型

本模块定义本体场景的数据模型。

Classes:
    OntologyScene: 本体场景表模型
"""

import datetime
import uuid
from sqlalchemy import Column, String, DateTime, Integer, Text, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.db import Base


class OntologyScene(Base):
    """本体场景表 - 用于存储本体场景下不同的类型信息"""
    __tablename__ = "ontology_scene"
    __table_args__ = (
        UniqueConstraint('workspace_id', 'scene_name', name='uq_workspace_scene_name'),
    )

    # 主键
    scene_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True, comment="场景ID")

    # 场景信息
    scene_name = Column(String(200), nullable=False, comment="场景名称")
    scene_description = Column(Text, nullable=True, comment="场景描述")

    # 外键：关联到工作空间
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True, comment="所属工作空间ID")

    # 时间戳
    created_at = Column(DateTime, default=datetime.datetime.now, nullable=False, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now, nullable=False, comment="更新时间")

    # 关系：一个场景可以有多个类型
    classes = relationship("OntologyClass", back_populates="scene", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<OntologyScene(id={self.scene_id}, name={self.scene_name})>"
