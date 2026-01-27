# -*- coding: utf-8 -*-
"""本体提取结果模型

本模块定义本体提取系统的提取结果数据模型。

Classes:
    OntologyExtractionResult: 本体提取结果表模型
"""

import datetime
import uuid
from sqlalchemy import Column, String, DateTime, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from app.db import Base


class OntologyExtractionResult(Base):
    """本体提取结果表 - 用于存储本体提取的执行结果"""
    __tablename__ = "ontology_extraction_result"

    # 主键
    ontology_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True, comment="结果ID")

    # 提取输入
    scenario = Column(Text, nullable=False, comment="场景描述文本")
    domain = Column(String(200), nullable=True, comment="领域")

    # 提取输出
    namespace = Column(String(500), nullable=True, comment="本体命名空间URI")
    classes_json = Column(JSONB, nullable=False, comment="提取的本体类数据(JSON格式)")
    extracted_count = Column(Integer, nullable=False, comment="提取的类数量")

    # 用户信息
    # user_id = Column(UUID(as_uuid=True), nullable=True, comment="用户ID")

    # 时间戳
    created_at = Column(DateTime, default=datetime.datetime.now, nullable=False, comment="创建时间")

    def __repr__(self):
        return f"<OntologyExtractionResult(id={self.ontology_id}, domain={self.domain}, extracted_count={self.extracted_count})>"
