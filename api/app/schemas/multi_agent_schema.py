"""多 Agent 相关的 Schema 定义"""
import uuid
import datetime
from typing import Optional, List, Dict, Any, Union, Literal
from pydantic import BaseModel, Field, ConfigDict, field_serializer, model_validator

from app.core.utils.datetime_utils import to_timestamp_ms
from app.schemas.app_schema import (
    ModelParameters,
    MemoryConfig,
    KnowledgeRetrievalConfig,
    ToolConfig,
    SkillConfig,
    VariableDefinition,
    AppFeatures,
    FileInput,
)


# ==================== 子 Agent 配置 ====================

class SubAgentConfig(BaseModel):
    """子 Agent 配置

    版本策略（对齐工作流 Agent 节点的 AgentReferenceConfig）：
    - current：跟随子 Agent 应用的当前发布版本；集群发布时才钉死为具体版本。
    - pinned：固定到 release_id 指定的已发布版本。

    入参 agent_id 是子 Agent 的应用 ID；落库时后端把它换成有效的 release ID
    （current=保存时的当前发布版本，仅作锚点；pinned=release_id）。
    存量 JSON 没有 release_policy，由解析层按 pinned 处理，行为不变。
    """
    agent_id: uuid.UUID = Field(..., description="Agent ID")
    name: str = Field(..., description="Agent 名称")
    role: Optional[str] = Field(None, description="角色描述")
    priority: int = Field(default=1, ge=1, le=100, description="优先级（1-100）")
    capabilities: List[str] = Field(default_factory=list, description="能力列表")
    release_policy: Literal["current", "pinned"] = Field(
        default="current",
        description="版本策略：current 跟随最新发布版本，pinned 固定版本",
    )
    release_id: Optional[uuid.UUID] = Field(
        default=None,
        description="固定的 AppRelease ID，仅 release_policy=pinned 时必填",
    )

    @model_validator(mode="after")
    def _validate_release(self):
        if self.release_policy == "pinned" and self.release_id is None:
            raise ValueError("固定版本模式必须提供 release_id")
        if self.release_policy == "current":
            # 跟随最新时 release_id 无意义，统一清空避免歧义
            self.release_id = None
        return self


class RoutingRule(BaseModel):
    """路由规则"""
    condition: str = Field(..., description="条件表达式")
    target_agent_id: uuid.UUID = Field(..., description="目标 Agent ID")
    priority: int = Field(default=1, ge=1, le=100, description="优先级")


class ExecutionConfig(BaseModel):
    """执行配置"""
    max_iterations: int = Field(
        default=10,
        ge=1,
        le=20,
        description="主管循环轮次上限（一轮=主管一次决策里发出的那批子 Agent 调用，同轮并发多个只算 1 轮），"
                    "仅 orchestration_mode=supervisor_loop 生效；达到上限后新一轮的子 Agent 调用不再执行，"
                    "把限制写进工具返回值让主管基于已有结果收尾；"
                    "前端不提供配置入口，统一使用默认值 10"
    )
    timeout: int = Field(
        default=60,
        ge=10,
        le=300,
        description="子 Agent 非流式执行的总超时（秒）。流式路径不用它——流式受 "
                    "stream_idle_timeout 约束（长回答只要持续产出就不该被总时长杀掉）"
    )
    stream_idle_timeout: int = Field(
        default=300,
        ge=30,
        le=3600,
        description="子 Agent 流式执行的**事件间空闲**超时（秒）。"
                    "取值需大于最慢工具的合法静默时长（沙箱代码执行 / 联网搜索在 "
                    "tool_start→tool_end 之间完全无事件），设太小会误杀正常调用。"
                    "作用是兜住真死锁（前端滚轮永久冻屏）"
    )
    parallel_limit: int = Field(default=3, ge=1, le=10, description="并行限制")
    retry_on_failure: bool = Field(
        default=False,
        description="子 Agent 失败时是否重试。默认关：重试会重复消耗模型配额，"
                    "且流式路径只在\"尚未产出任何事件\"时才安全重试"
    )
    max_retries: int = Field(
        default=2,
        ge=0,
        le=10,
        description="最大重试次数（不含首次）。仅对瞬时失败生效——配置类错误"
                    "（BusinessException）、超时、取消都不重试，重跑必然同样失败"
    )

    # P0-5：routing_mode 已删除 —— 该字段从无消费点（路由始终走 MasterAgentRouter），
    # 其枚举值 llm_router 对应的 LLMRouter 实现也已随死代码清理移除。
    # 存量 execution_config JSON 里残留的 routing_mode 键由 Pydantic extra="ignore"
    # 自动丢弃，不会导致反序列化失败。
    enable_rule_fast_path: bool = Field(
        default=False,
        description="已废弃：关键词规则快路径不再生效，保留字段仅为兼容存量配置。"
                    "确定性编排请使用 orchestration_mode 的声明式模式（pipeline/fanout/router）。"
    )

    # 新增：结果整合模式配置
    result_merge_mode: str = Field(
        default="master",
        pattern="^(smart|master)$",
        description="结果整合模式：master（Master Agent 流式智能整合，默认；连贯去重）| smart（规则拼接，不调用模型，快速）"
    )
    merge_max_tokens: int = Field(
        default=8192,
        ge=256,
        le=32000,
        description="Master Agent 整合输出的最大 token 数（仅 result_merge_mode=master 生效）"
    )
    supervisor_max_tool_calls: int = Field(
        default=3,
        ge=1,
        le=10,
        description="主管循环单轮内最多分派子 Agent 次数（tool_call_limit），仅 orchestration_mode=supervisor_loop 生效"
    )

    # 新增：子 Agent 执行模式配置
    sub_agent_execution_mode: str = Field(
        default="parallel",
        pattern="^(parallel|sequential)$",
        description="子 Agent 执行模式：parallel（并行执行，快速）| sequential（串行执行，节省资源）"
    )


# ==================== 多 Agent 配置 ====================

class SupervisorConfig(BaseModel):
    """主管配置（全模式；supervisor_loop 模式下还含主管本体能力面）。

    结构对齐单 Agent 应用的能力面；留空/缺省 = 现状"裸主管"行为
    （硬编码 prompt + 仅 SubAgentTool），存量配置零迁移。

    集群变量定义统一放在本模型的 variables 键（不再单列 MultiAgentConfig.variables）。
    主管 prompt 与子 Agent 共用同一变量包，不另设"主管私有变量"
    （避免两套变量定义打架）；缺省/空 = 运行时退化为子 Agent 变量并集。
    """
    model_config = ConfigDict(extra="ignore")

    system_prompt: Optional[str] = Field(
        default=None,
        max_length=8000,
        description="主管自定义系统提示词（渲染集群变量 {{var}} 后，自动段之前拼接）。留空 = 仅自动段（名册+分派纪律）",
    )
    memory: Optional[MemoryConfig] = Field(
        default=None,
        description="主管记忆配置（{'enabled': bool}；集群会话历史不受此开关控制，始终保留）",
    )
    knowledge_retrieval: Optional[KnowledgeRetrievalConfig] = Field(
        default=None,
        description="主管知识库检索配置（与单 Agent 应用 KnowledgeRetrievalConfig 同构）",
    )
    tools: Optional[List[ToolConfig]] = Field(
        default=None,
        description="主管工具配置（与单 Agent 应用 ToolConfig 同构）",
    )
    skills: Optional[SkillConfig] = Field(
        default=None,
        description="主管技能配置（与单 Agent 应用 SkillConfig 同构）",
    )
    variables: Optional[List[VariableDefinition]] = Field(
        default=None,
        description=(
            "集群变量定义（对外契约，优先于子 Agent 变量并集）："
            "[{'name': str, 'display_name': str, 'type': str, 'required': bool, "
            "'default_value': '...'}]；None/空 = 退化为子 Agent 变量并集"
        ),
    )
    features: Optional[AppFeatures] = Field(
        default=None,
        description=(
            "对话功能特性（与单 Agent 应用 AppFeatures 同构）：file_upload / opening_statement / "
            "suggested_questions_after_answer / text_to_speech / citation / web_search / "
            "context_engine / emotion_reply。None = 全部关闭（存量集群行为不变，零迁移）"
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_skill_ids(cls, data: Any) -> Any:
        """容错归一化 skill_ids（前端已做同样压缩，此为后端兜底）。

        规则与 capabilityContract.compressSkills / Agent 运行时一致：
        - all_skills=true 时 skill_ids 无意义 → 落 []（编辑器可能残留 null 元素）
        - 对象元素取 id；丢弃 null / 非字符串元素

        知识库/工具/变量的实体脏字段由各自模型 extra=ignore 自动丢弃，
        只有 list[str] 的 skill_ids 遇到对象/None 会硬报错，故在此统一归一化。
        """
        if isinstance(data, dict):
            skills = data.get("skills")
            if isinstance(skills, dict) and isinstance(skills.get("skill_ids"), list):
                if skills.get("all_skills"):
                    skills["skill_ids"] = []
                else:
                    skills["skill_ids"] = [
                        vo.get("id") if isinstance(vo, dict) else vo
                        for vo in skills["skill_ids"]
                    ]
                    skills["skill_ids"] = [
                        vo for vo in skills["skill_ids"] if isinstance(vo, str)
                    ]
        return data


class MultiAgentConfigCreate(BaseModel):
    """创建多 Agent 配置"""
    master_agent_id: uuid.UUID = Field(..., description="主 Agent ID")
    master_agent_name: Optional[str] = Field(default=None, max_length=100, description="主 Agent 名称")
    orchestration_mode: str = Field(
        default="supervisor_loop",
        pattern="^(collaboration|supervisor|supervisor_loop)$",
        description="协作模式：supervisor_loop（主管 ReAct 循环，默认）| supervisor（主管三段式）| collaboration（协作）"
    )
    sub_agents: List[SubAgentConfig] = Field(..., description="子 Agent 列表")
    routing_rules: Optional[List[RoutingRule]] = Field(default=None, description="路由规则")
    execution_config: ExecutionConfig = Field(default_factory=ExecutionConfig, description="执行配置")
    supervisor_config: Optional[SupervisorConfig] = Field(
        default=None,
        description="主管配置（全模式；含集群变量 variables；None=裸主管现状）",
    )
    aggregation_strategy: str = Field(
        default="merge",
        pattern="^(merge|vote|priority|custom)$",
        description="结果整合策略：merge|vote|priority|custom"
    )


class MultiAgentConfigUpdate(BaseModel):
    """更新多 Agent 配置"""
    master_agent_id: Optional[uuid.UUID] = None
    master_agent_name: Optional[str] = Field(default=None, max_length=100, description="主 Agent 名称")
    default_model_config_id: Optional[uuid.UUID] = Field(None, description="默认模型配置ID")
    model_parameters: Optional[ModelParameters] = Field(
        None,
        description="模型参数配置（temperature、max_tokens 等）"
    )
    orchestration_mode: Optional[str] = Field(
        default="supervisor_loop",
        pattern="^(collaboration|supervisor|supervisor_loop)$",
        description="协作模式：supervisor_loop（主管 ReAct 循环，默认）| supervisor（主管三段式）| collaboration（协作）"
    )
    sub_agents: Optional[List[SubAgentConfig]] = None
    routing_rules: Optional[List[RoutingRule]] = None
    execution_config: Optional[ExecutionConfig] = None
    supervisor_config: Optional[SupervisorConfig] = Field(
        default=None,
        description="主管配置（全模式；含集群变量 variables；None=不更新该字段）",
    )
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
    master_agent_id: uuid.UUID | None
    master_agent_name: Optional[str]
    default_model_config_id : uuid.UUID | None = Field(description="默认模型配置ID")
    model_parameters: ModelParameters | None = Field(
        default_factory=ModelParameters,
        description="模型参数配置（temperature、max_tokens 等）"
    )
    orchestration_mode: str
    sub_agents: List[Dict[str, Any]]
    routing_rules: Optional[List[Dict[str, Any]]]
    execution_config: Dict[str, Any]
    supervisor_config: Optional[Dict[str, Any]] = Field(
        default=None,
        description="主管配置（全模式；含集群变量 variables；null=裸主管现状）",
    )
    aggregation_strategy: str
    is_active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)


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
    thinking: bool = Field(default=False, description="是否启用深度思考（需集群 model_parameters.deep_thinking 同时开启）")
    files: List[FileInput] = Field(
        default_factory=list,
        description="附件列表（需 supervisor_config.features.file_upload 开启；仅 supervisor_loop 模式给主管）",
    )


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


# P0-5：智能路由测试 schema（RoutingTestRequest/RoutingTestCase/BatchRoutingTestRequest）
# 已随引用它们的死端点（test-routing/test-master-agent/batch-test-routing）一并移除。


# ==================== Agent Handoffs ====================

class HandoffHistoryItem(BaseModel):
    """Handoff 历史记录项"""
    from_agent: str = Field(..., description="源 Agent ID")
    to_agent: str = Field(..., description="目标 Agent ID")
    reason: str = Field(..., description="切换原因")
    timestamp: Optional[str] = Field(None, description="切换时间")
    user_message: Optional[str] = Field(None, description="触发切换的用户消息")
    context_summary: Optional[str] = Field(None, description="上下文摘要")


class HandoffChatResponse(BaseModel):
    """Handoff 聊天响应"""
    message: str = Field(..., description="最终回复")
    conversation_id: str = Field(..., description="会话 ID")
    final_agent_id: str = Field(..., description="最终处理的 Agent ID")
    handoff_count: int = Field(..., description="切换次数")
    handoff_history: List[HandoffHistoryItem] = Field(
        default_factory=list,
        description="切换历史"
    )
    elapsed_time: float = Field(..., description="总耗时（秒）")
    usage: Optional[Dict[str, Any]] = Field(None, description="资源使用情况")
    error: Optional[str] = Field(None, description="错误信息")


class HandoffStateResponse(BaseModel):
    """Handoff 状态响应"""
    conversation_id: str = Field(..., description="会话 ID")
    current_agent_id: str = Field(..., description="当前活跃的 Agent ID")
    handoff_count: int = Field(..., description="总切换次数")
    handoff_history: List[HandoffHistoryItem] = Field(
        default_factory=list,
        description="切换历史"
    )
    created_at: str = Field(..., description="创建时间")
    updated_at: str = Field(..., description="更新时间")


class HandoffToolInfo(BaseModel):
    """Handoff 工具信息"""
    name: str = Field(..., description="工具名称")
    target_agent_id: str = Field(..., description="目标 Agent ID")
    target_agent_name: str = Field(..., description="目标 Agent 名称")
    description: str = Field(..., description="工具描述")


class HandoffRoutingTestResponse(BaseModel):
    """Handoff 路由测试响应"""
    message: str = Field(..., description="测试消息")
    initial_agent_id: str = Field(..., description="初始 Agent ID")
    initial_agent_name: str = Field(..., description="初始 Agent 名称")
    available_handoff_tools: List[HandoffToolInfo] = Field(
        default_factory=list,
        description="可用的 handoff 工具"
    )
    handoff_suggestion: Optional[Dict[str, Any]] = Field(
        None,
        description="自动切换建议"
    )
    total_agents: int = Field(..., description="总 Agent 数量")
