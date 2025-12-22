import datetime
from sqlalchemy import Column, String, Boolean, DateTime, Integer, Float
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base


class DataConfig(Base):
    """数据配置表 - 用于存储记忆系统的配置参数"""
    __tablename__ = "data_config"

    # 主键
    config_id = Column(Integer, primary_key=True, autoincrement=True, comment="配置ID")

    # 基本信息
    config_name = Column(String, nullable=False, comment="配置名称")
    config_desc = Column(String, nullable=True, comment="配置描述")

    # 组织信息
    workspace_id = Column(UUID(as_uuid=True), nullable=True, comment="工作空间ID")
    group_id = Column(String, nullable=True, comment="组ID")
    user_id = Column(String, nullable=True, comment="用户ID")
    apply_id = Column(String, nullable=True, comment="应用ID")

    # 模型选择（从workspace继承）
    llm_id = Column(String, nullable=True, comment="LLM模型配置ID")
    embedding_id = Column(String, nullable=True, comment="嵌入模型配置ID")
    rerank_id = Column(String, nullable=True, comment="重排序模型配置ID")
    llm = Column(String, nullable=True, comment="LLM模型配置ID")

    # 记忆萃取引擎配置
    enable_llm_dedup_blockwise = Column(Boolean, default=True, comment="启用LLM决策去重")
    enable_llm_disambiguation = Column(Boolean, default=True, comment="启用LLM决策消歧")
    deep_retrieval = Column(Boolean, default=True, comment="深度检索开关")

    # 阈值配置 (0-1 之间的浮点数)
    t_type_strict = Column(Float, default=0.8, comment="类型严格阈值")
    t_name_strict = Column(Float, default=0.8, comment="名称严格阈值")
    t_overall = Column(Float, default=0.8, comment="综合阈值")

    # 状态配置
    state = Column(Boolean, default=False, comment="配置使用状态")

    # 分块策略
    chunker_strategy = Column(String, default="RecursiveChunker", comment="分块策略")

    # 剪枝配置
    pruning_enabled = Column(Boolean, default=False, comment="是否启动智能语义剪枝")
    pruning_scene = Column(String, nullable=True, comment="智能剪枝场景：education/online_service/outbound")
    pruning_threshold = Column(Float, nullable=True, comment="智能语义剪枝阈值（0-0.9）")

    # 自我反思配置
    enable_self_reflexion = Column(Boolean, default=False, comment="是否启用自我反思")
    iteration_period = Column(String, default="3", comment="反思迭代周期")
    reflexion_range = Column(String, default="retrieval", comment="反思范围：部分/全部")
    baseline = Column(String, default="time", comment="基线：时间/事实/时间和事实")
    reflection_model_id = Column(String, nullable=True, comment="反思模型ID")
    memory_verify = Column(Boolean, default=True, comment="记忆验证")
    quality_assessment = Column(Boolean, default=True, comment="质量评估")

    # 遗忘引擎配置
    statement_granularity = Column(Integer, default=2, comment="陈述提取颗粒度，挡位 1/2/3")
    include_dialogue_context = Column(Boolean, default=False, comment="是否包含对话上下文")
    max_context = Column(Integer, default=1000, comment="对话语境中包含字符的最大数量")
    lambda_time = Column("lambda_time", Float, default=0.5, comment="最低保持度，0-1 小数")
    lambda_mem = Column("lambda_mem", Float, default=0.5, comment="遗忘率，0-1 小数")
    offset = Column("offset", Float, default=0.0, comment="偏移度，0-1 小数")
    
    # 情绪引擎配置
    emotion_enabled = Column(Boolean, default=True, comment="是否启用情绪提取")
    emotion_model_id = Column(String, nullable=True, comment="情绪分析专用模型ID")
    emotion_extract_keywords = Column(Boolean, default=True, comment="是否提取情绪关键词")
    emotion_min_intensity = Column(Float, default=0.1, comment="最小情绪强度阈值")
    emotion_enable_subject = Column(Boolean, default=True, comment="是否启用主体分类")
    
    # 时间戳
    created_at = Column(DateTime, default=datetime.datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now, comment="更新时间")

    def __repr__(self):
        return f"<DataConfig(config_id={self.config_id}, config_name={self.config_name})>"
