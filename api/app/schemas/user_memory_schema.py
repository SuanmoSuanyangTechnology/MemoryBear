"""
用户记忆相关的请求和响应模型
包含用户摘要、记忆洞察、节点统计、图数据和用户档案等接口的 Schema
"""
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field


# ==================== 记忆洞察报告 ====================

class MemoryInsightReportData(BaseModel):
    """记忆洞察报告数据"""
    memory_insight: Optional[str] = Field(None, description="总体概述")
    behavior_pattern: Optional[str] = Field(None, description="行为模式")
    key_findings: Optional[List[str]] = Field(None, description="关键发现")
    growth_trajectory: Optional[str] = Field(None, description="成长轨迹")
    updated_at: Optional[int] = Field(None, description="更新时间戳（毫秒）")
    is_cached: bool = Field(..., description="是否有缓存数据")
    message: Optional[str] = Field(None, description="附加消息")


# ==================== 用户摘要 ====================

class UserSummaryData(BaseModel):
    """用户摘要数据"""
    user_summary: Optional[str] = Field(None, description="用户摘要")
    personality: Optional[str] = Field(None, description="性格特征")
    core_values: Optional[str] = Field(None, description="核心价值观")
    one_sentence: Optional[str] = Field(None, description="一句话总结")
    updated_at: Optional[int] = Field(None, description="更新时间戳（毫秒）")
    is_cached: bool = Field(..., description="是否有缓存数据")
    message: Optional[str] = Field(None, description="附加消息")


# ==================== 缓存生成 ====================

class GenerateCacheErrorItem(BaseModel):
    """缓存生成错误项"""
    type: Optional[str] = Field(None, description="错误类型 (insight/summary)")
    error: Optional[str] = Field(None, description="错误信息")


class SingleUserCacheResultData(BaseModel):
    """单用户缓存生成结果"""
    end_user_id: str = Field(..., description="终端用户ID")
    insight_success: bool = Field(..., description="洞察生成是否成功")
    summary_success: bool = Field(..., description="摘要生成是否成功")
    errors: List[GenerateCacheErrorItem] = Field(default_factory=list, description="错误列表")


class WorkspaceCacheErrorItem(BaseModel):
    """工作空间缓存生成错误项"""
    end_user_id: Optional[str] = Field(None, description="终端用户ID")
    insight_error: Optional[str] = Field(None, description="洞察生成错误")
    summary_error: Optional[str] = Field(None, description="摘要生成错误")
    error: Optional[str] = Field(None, description="通用错误信息")


class WorkspaceCacheResultData(BaseModel):
    """工作空间批量缓存生成结果"""
    total_users: int = Field(..., description="总用户数")
    successful: int = Field(..., description="成功数")
    failed: int = Field(..., description="失败数")
    errors: List[WorkspaceCacheErrorItem] = Field(default_factory=list, description="错误列表")


# ==================== 节点统计 ====================

class MemoryTypeStatItem(BaseModel):
    """记忆类型统计项"""
    type: str = Field(..., description="记忆类型枚举值")
    count: int = Field(..., description="该类型的数量")
    percentage: float = Field(..., description="该类型在所有记忆中的占比")


# ==================== 图数据 ====================

class GraphNodeData(BaseModel):
    """图节点数据"""
    id: str = Field(..., description="节点ID")
    label: str = Field(..., description="节点类型标签")
    properties: Dict[str, Any] = Field(default_factory=dict, description="节点属性")
    caption: Optional[str] = Field(None, description="节点显示名称")


class GraphEdgeData(BaseModel):
    """图边数据"""
    id: str = Field(..., description="边ID")
    source: str = Field(..., description="源节点ID")
    target: str = Field(..., description="目标节点ID")
    type: Optional[str] = Field(None, description="关系类型")
    properties: Dict[str, Any] = Field(default_factory=dict, description="边属性")
    caption: Optional[str] = Field(None, description="边显示名称")


class GraphStatistics(BaseModel):
    """图统计信息"""
    total_nodes: int = Field(0, description="节点总数")
    total_edges: int = Field(0, description="边总数")
    node_types: Dict[str, int] = Field(default_factory=dict, description="各节点类型数量")
    edge_types: Dict[str, int] = Field(default_factory=dict, description="各边类型数量")


class GraphData(BaseModel):
    """图数据响应"""
    nodes: List[GraphNodeData] = Field(..., description="节点列表")
    edges: List[GraphEdgeData] = Field(..., description="边列表")
    statistics: GraphStatistics = Field(..., description="统计信息")
    message: Optional[str] = Field(None, description="附加消息")


# ==================== 关系演变 ====================

class RelationshipEvolutionData(BaseModel):
    """关系演变数据"""
    emotion: Any = Field(None, description="情绪数据")
    interaction: Any = Field(None, description="交互频率数据")
