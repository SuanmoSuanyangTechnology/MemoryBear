# -*- coding: utf-8 -*-
"""本体提取配置模型

本模块定义本体提取系统的配置参数数据模型。

Classes:
    OntologyExtractionConfig: 本体提取配置表模型
"""

import datetime
from sqlalchemy import Column, String, Boolean, DateTime, Integer, Float
from app.db import Base


class OntologyExtractionConfig(Base):
    """本体提取配置表 - 用于存储本体提取的执行参数"""
    __tablename__ = "ontology_extraction_config"

    # 主键
    id = Column(Integer, primary_key=True, autoincrement=True, comment="配置ID")

    # 配置名称
    config_name = Column(String(100), unique=True, nullable=False, comment="配置名称")

    # 提取参数
    max_classes = Column(Integer, default=15, nullable=False, comment="最大提取类数量")
    min_classes = Column(Integer, default=5, nullable=False, comment="最小提取类数量")
    max_description_length = Column(Integer, default=500, nullable=False, comment="描述最大字符数")

    # LLM参数
    llm_temperature = Column(Float, default=0.3, nullable=False, comment="LLM温度参数(0-1)")
    llm_max_tokens = Column(Integer, default=2000, nullable=False, comment="LLM最大token数")
    llm_timeout = Column(Float, default=None, nullable=True, comment="LLM调用超时时间(秒)")

    # 验证开关
    enable_owl_validation = Column(Boolean, default=True, nullable=False, comment="是否启用OWL验证")

    # 时间戳
    created_at = Column(DateTime, default=datetime.datetime.now, nullable=False, comment="创建时间")
    updated_at = Column(
        DateTime,
        default=datetime.datetime.now,
        onupdate=datetime.datetime.now,
        nullable=False,
        comment="更新时间"
    )

    def __repr__(self):
        return f"<OntologyExtractionConfig(id={self.id}, config_name={self.config_name})>"
