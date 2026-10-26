"""Multi-agent schema definitions

Subset of the host multi_agent_schema consumed by the definition plane (app
create path and the multi-agent config read/update endpoints).
"""
from __future__ import annotations

import datetime
import uuid
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

# The structured column is typed by the models-package copy of ModelParameters;
# pydantic v2 only validates an instance of the exact same class, so both the
# request and the response schema use that copy (its fields and constraints are
# kept identical to the app_schema counterpart).
from ..models.model_parameters import ModelParameters
from ..utils.datetime_utils import to_timestamp_ms
from .app_schema import (
    AppFeatures,
    FileInput,
    KnowledgeRetrievalConfig,
    MemoryConfig,
    SkillConfig,
    ToolConfig,
    VariableDefinition,
)

# ==================== Sub-agent config ====================

class SubAgentConfig(BaseModel):
    """Sub-agent config

    Release policy (aligned with the workflow Agent node's AgentReferenceConfig):
    - current: follow the sub-agent app's current release; pinned to a concrete
      release only when the cluster is published.
    - pinned: fixed to the published release given by release_id.

    The input agent_id is the sub-agent's app ID; on write the backend swaps it
    for a valid release ID (current=the current release at save time, kept as an
    anchor only; pinned=release_id). Legacy JSON without release_policy is
    treated as pinned by the resolution layer; behavior is unchanged.
    """
    agent_id: uuid.UUID = Field(..., description="Agent ID")
    name: str = Field(..., description="Agent 名称")
    role: Optional[str] = Field(None, description="角色描述")
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
            # With follow-latest, release_id is meaningless; clear it to avoid ambiguity
            self.release_id = None
        return self


class RoutingRule(BaseModel):
    """Routing rule"""
    condition: str = Field(..., description="条件表达式")
    target_agent_id: uuid.UUID = Field(..., description="目标 Agent ID")
    priority: int = Field(default=1, ge=1, le=100, description="优先级")


class ExecutionConfig(BaseModel):
    """Execution config"""
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

    # routing_mode was removed: no consumer ever read it (routing always goes
    # through MasterAgentRouter), and its llm_router enum value plus the matching
    # LLMRouter implementation were removed with the dead-code cleanup. Stale
    # routing_mode keys in legacy execution_config JSON are dropped by Pydantic
    # extra="ignore" and do not break deserialization.
    enable_rule_fast_path: bool = Field(
        default=False,
        description="已废弃：关键词规则快路径不再生效，保留字段仅为兼容存量配置。"
                    "确定性编排请使用 orchestration_mode 的声明式模式（pipeline/fanout/router）。"
    )

    # Result merge mode config
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
        description="已废弃，不再生效：所有 Agent 的单工具调用上限统一由环境变量 AGENT_TOOL_CALL_LIMIT 控制（默认 5）"
    )

    # Sub-agent execution mode config
    sub_agent_execution_mode: str = Field(
        default="parallel",
        pattern="^(parallel|sequential)$",
        description="子 Agent 执行模式：parallel（并行执行，快速）| sequential（串行执行，节省资源）"
    )


# ==================== Multi-agent config ====================

class SupervisorConfig(BaseModel):
    """Supervisor config (all modes; under supervisor_loop it also carries the
    supervisor's own capability surface).

    Mirrors the capability surface of a single-Agent app; empty/default keeps the
    existing "bare supervisor" behavior (hardcoded prompt + SubAgentTool only);
    existing configs need no migration.

    Cluster variables live under this model's variables key (no separate
    MultiAgentConfig.variables). The supervisor prompt shares one variable bag
    with sub-agents (no separate "supervisor-private variables", avoiding two
    competing variable definitions); default/empty = runtime falls back to the
    union of sub-agent variables.
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
        """Fault-tolerant skill_ids normalization (the frontend compresses the
        same way; this is the backend fallback).

        Rules match capabilityContract.compressSkills / the Agent runtime:
        - when all_skills=true, skill_ids is meaningless → store [] (the editor
          may leave null elements)
        - object elements yield their id; null / non-string elements are dropped

        Dirty entity fields under knowledge retrieval / tools / variables are
        dropped by their models' extra=ignore; only the list[str] skill_ids
        hard-errors on objects/None, hence the unified normalization here.
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
    """Create multi-agent config"""
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
    """Update multi-agent config"""
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
    """Multi-agent config output"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    app_id: uuid.UUID
    master_agent_id: uuid.UUID | None
    master_agent_name: Optional[str]
    default_model_config_id: uuid.UUID | None = Field(description="默认模型配置ID")
    model_parameters: ModelParameters | None = Field(
        default_factory=ModelParameters,
        description="模型参数配置（temperature、max_tokens 等）"
    )
    orchestration_mode: str
    sub_agents: List[dict[str, Any]]
    routing_rules: Optional[List[dict[str, Any]]]
    execution_config: dict[str, Any]
    supervisor_config: Optional[dict[str, Any]] = Field(
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


# ==================== Multi-agent run ====================

class MultiAgentRunRequest(BaseModel):
    """Multi-agent run request"""
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


class HandoffToolInfo(BaseModel):
    """Handoff tool info"""
    name: str = Field(..., description="工具名称")
    target_agent_id: str = Field(..., description="目标 Agent ID")
    target_agent_name: str = Field(..., description="目标 Agent 名称")
    description: str = Field(..., description="工具描述")


class HandoffRoutingTestResponse(BaseModel):
    """Handoff routing test response"""
    message: str = Field(..., description="测试消息")
    initial_agent_id: str = Field(..., description="初始 Agent ID")
    initial_agent_name: str = Field(..., description="初始 Agent 名称")
    available_handoff_tools: List[HandoffToolInfo] = Field(
        default_factory=list,
        description="可用的 handoff 工具"
    )
    handoff_suggestion: Optional[dict[str, Any]] = Field(
        None,
        description="自动切换建议"
    )
    total_agents: int = Field(..., description="总 Agent 数量")


__all__ = [
    "ExecutionConfig",
    "HandoffRoutingTestResponse",
    "HandoffToolInfo",
    "MultiAgentConfigCreate",
    "MultiAgentConfigSchema",
    "MultiAgentConfigUpdate",
    "RoutingRule",
    "SubAgentConfig",
    "SupervisorConfig",
]
