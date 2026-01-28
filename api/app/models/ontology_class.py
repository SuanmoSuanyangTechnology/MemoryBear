# -*- coding: utf-8 -*-
"""本体类型模型

本模块定义本体类型的数据模型。

Classes:
    OntologyClass: 本体类型表模型
"""

import datetime
import uuid
from sqlalchemy import Column, String, DateTime, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.db import Base


class OntologyClass(Base):
    """本体类型表 - 用于存储某个场景提取出来的本体类型信息"""
    __tablename__ = "ontology_class"

    # 主键
    class_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True, comment="类型ID")

    # 类型信息
    class_name = Column(String(200), nullable=False, comment="类型名称")
    class_description = Column(Text, nullable=True, comment="类型描述")

    # 外键：关联到本体场景
    scene_id = Column(UUID(as_uuid=True), ForeignKey("ontology_scene.scene_id", ondelete="CASCADE"), nullable=False, index=True, comment="所属场景ID")

    # 时间戳
    created_at = Column(DateTime, default=datetime.datetime.now, nullable=False, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now, nullable=False, comment="更新时间")

    # 关系：类型属于某个场景
    scene = relationship("OntologyScene", back_populates="classes")

    def __repr__(self):
        return f"<OntologyClass(id={self.class_id}, name={self.class_name}, scene_id={self.scene_id})>"
