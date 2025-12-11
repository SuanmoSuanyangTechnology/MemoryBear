"""多 Agent 相关的 Schema 定义"""
import uuid
import datetime
from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field, ConfigDict, field_serializer


# ==================== 子 Agent 配置 ====================

class SubAgentConfig(BaseModel):
    """子 Agent 配置"""
    agent_id: uuid.UUID = Field(..., description="Agent ID")
    name: str = Field(..., description="Agent 名称")
    role: Optional[str] = Field(None, description="角色描述")
    priority: int = Field(default=1, ge=1, le=100, description="优先级（1-100）")
    capabilities: List[str] = Field(default_factory=list, description="能力列表")


class RoutingRule(BaseModel):
    """路由规则"""
    condition: str = Field(..., description="条件表达式")
    target_agent_id: uuid.UUID = Field(..., description="目标 Agent ID")
    priority: int = Field(default=1, ge=1, le=100, description="优先级")


class ExecutionConfig(BaseModel):
    """执行配置"""
    max_iterations: int = Field(default=5, ge=1, le=20, description="最大迭代次数")
    timeout: int = Field(default=60, ge=10, le=300, description="超时时间（秒）")
    parallel_limit: int = Field(default=3, ge=1, le=10, description="并行限制")
    retry_on_failure: bool = Field(default=True, description="失败时是否重试")
    max_retries: int = Field(default=3, ge=0, le=10, description="最大重试次数")
    
    # 新增：路由模式配置
    routing_mode: str = Field(
        default="master_agent",
        pattern="^(master_agent|llm_router|rule_only)$",
        description="路由模式：master_agent（Master Agent决策）| llm_router（旧LLM路由器）| rule_only（仅规则路由）"
    )
    enable_rule_fast_path: bool = Field(
        default=True,
        description="是否启用规则快速路径（性能优化，高置信度关键词直接返回）"
    )
    
    # 新增：结果整合模式配置
    result_merge_mode: str = Field(
        default="smart",
        pattern="^(smart|master)$",
        description="结果整合模式：smart（规则去重，快速）| master（Master Agent 智能整合，连贯）"
    )
    
    # 新增：子 Agent 执行模式配置
    sub_agent_execution_mode: str = Field(
        default="parallel",
        pattern="^(parallel|sequential)$",
        description="子 Agent 执行模式：parallel（并行执行，快速）| sequential（串行执行，节省资源）"
    )


# ==================== 多 Agent 配置 ====================

class MultiAgentConfigCreate(BaseModel):
    """创建多 Agent 配置"""
    master_agent_id: uuid.UUID = Field(..., description="主 Agent ID")
    master_agent_name: Optional[str] = Field(None, max_length=100, description="主 Agent 名称")
    orchestration_mode: str = Field(
        ...,
        pattern="^(sequential|parallel|conditional|loop)$",
        description="编排模式：sequential|parallel|conditional|loop"
    )
    sub_agents: List[SubAgentConfig] = Field(..., description="子 Agent 列表")
    routing_rules: Optional[List[RoutingRule]] = Field(None, description="路由规则")
    execution_config: ExecutionConfig = Field(default_factory=ExecutionConfig, description="执行配置")
    aggregation_strategy: str = Field(
        default="merge",
        pattern="^(merge|vote|priority|custom)$",
        description="结果整合策略：merge|vote|priority|custom"
    )


class MultiAgentConfigUpdate(BaseModel):
    """更新多 Agent 配置"""
    master_agent_id: Optional[uuid.UUID] = None
    master_agent_name: Optional[str] = Field(None, max_length=100, description="主 Agent 名称")
    orchestration_mode: Optional[str] = Field(
        None,
        pattern="^(sequential|parallel|conditional|loop)$"
    )
    sub_agents: Optional[List[SubAgentConfig]] = None
    routing_rules: Optional[List[RoutingRule]] = None
    execution_config: Optional[ExecutionConfig] = None
    aggregation_strategy: Optional[str] = Field(
        None,
        pattern="^(merge|vote|priority|custom)$"
    )
    is_active: Optional[bool] = None


class MultiAgentConfigSchema(BaseModel):
    """多 Agent 配置输出"""
    model_config = ConfigDict(from_attributes=True)
    
    id: uuid.UUID
    app_id: uuid.UUID
    master_agent_id: uuid.UUID
    master_agent_name: Optional[str]
    orchestration_mode: str
    sub_agents: List[Dict[str, Any]]
    routing_rules: Optional[List[Dict[str, Any]]]
    execution_config: Dict[str, Any]
    aggregation_strategy: str
    is_active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime
    
    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None
    
    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None


# ==================== 多 Agent 运行 ====================

class MultiAgentRunRequest(BaseModel):
    """多 Agent 运行请求"""
    message: str = Field(..., description="用户消息")
    conversation_id: Optional[uuid.UUID] = Field(None, description="会话 ID")
    user_id: Optional[str] = Field(None, description="用户 ID")
    variables: Optional[Dict[str, Any]] = Field(None, description="变量参数")
    use_llm_routing: bool = Field(default=True, description="是否启用 LLM 路由（默认启用）")
    stream: bool = Field(default=False, description="是否流式返回")
    web_search: bool = Field(default=False, description="是否启用网络搜索")
    memory: bool = Field(default=True, description="是否启用记忆功能")


class SubAgentResult(BaseModel):
    """子 Agent 执行结果"""
    agent_id: str
    agent_name: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    elapsed_time: Optional[float] = None


class MultiAgentRunResponse(BaseModel):
    """多 Agent 运行响应"""
    message: str = Field(..., description="最终结果")
    conversation_id: Optional[uuid.UUID] = Field(None, description="会话 ID")
    elapsed_time: float = Field(..., description="总耗时（秒）")
    mode: str = Field(..., description="执行模式")
    sub_results: Union[List[Dict[str, Any]], Dict[str, Any]] = Field(..., description="子 Agent 结果")
    usage: Optional[Dict[str, Any]] = Field(None, description="资源使用情况")


# ==================== 智能路由测试 ====================

class RoutingTestRequest(BaseModel):
    """路由测试请求"""
    message: str = Field(..., description="测试消息")
    conversation_id: Optional[uuid.UUID] = Field(None, description="会话 ID（可选）")
    routing_model_id: Optional[uuid.UUID] = Field(None, description="路由模型 ID（用于 LLM 路由）")
    use_llm: bool = Field(default=False, description="是否启用 LLM 路由")
    keyword_threshold: Optional[float] = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="关键词置信度阈值（0-1）"
    )
    force_new: bool = Field(default=False, description="是否强制重新路由")


class RoutingTestCase(BaseModel):
    """路由测试用例"""
    message: str = Field(..., description="测试消息")
    expected_agent_id: Optional[uuid.UUID] = Field(None, description="期望的 Agent ID")
    description: Optional[str] = Field(None, description="测试用例描述")


class BatchRoutingTestRequest(BaseModel):
    """批量路由测试请求"""
    test_cases: List[RoutingTestCase] = Field(..., description="测试用例列表")
    routing_model_id: Optional[uuid.UUID] = Field(None, description="路由模型 ID")
    use_llm: bool = Field(default=False, description="是否启用 LLM 路由")
    keyword_threshold: Optional[float] = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="关键词置信度阈值"
    )
