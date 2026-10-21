"""Agent node config

MemoryBear workflow Agent node - autonomous reasoning + tool calling (ReAct / Function Calling).

Reuses the LLM node's configuration pattern (memory / thinking / error_handle) and
drives the tool-calling loop internally through the LangChainAgent execution engine.
"""

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ...nodes.base_config import BaseNodeConfig, VariableDefinition
from ....utils.workflow_node_types import HttpErrorHandle
from ...nodes.llm.config import (
    LLMExtraHeadersConfig,
    LLMFrequencyPenaltyConfig,
    LLMPresencePenaltyConfig,
    LLMRepetitionPenaltyConfig,
    LLMResponseFormatConfig,
    LLMSeedConfig,
    LLMStopConfig,
    LLMThinkingConfig,
    LLMTopKConfig,
    LLMTopPConfig,
    MemoryWindowSetting,
)
from ...variable.base_variable import VariableType
from ....schemas.app_schema import KnowledgeRetrievalConfig


class LongTermMemoryConfig(BaseModel):
    """Long-term memory tool config for the workflow Agent.

    Kept separate from ``memory`` (the conversation history window); semantically
    aligned with the long-term memory switch of Agent applications.
    """

    enable: bool = Field(
        default=False,
        description="是否启用长期记忆检索工具",
    )


class ToolSelector(BaseModel):
    """Selector for tools available to the Agent

    Points to one tool in the tool library (builtin / custom / MCP / workflow) and may
    select a sub-operation of that tool (for multi-operation tools such as
    datetime_tool / json_tool / MCP).
    """

    tool_id: str = Field(
        ...,
        description="工具 ID（工具库中的 ToolConfig.id）"
    )

    tool_type: str | None = Field(
        default=None,
        description="工具类型：builtin / custom / mcp / workflow（仅用于展示，运行时以实际工具实例为准）"
    )

    operation: str | None = Field(
        default=None,
        description="子操作名（对多操作工具，如 datetime_tool 的 now / MCP 的工具名）"
    )

    enabled: bool = Field(
        default=True,
        description="是否启用该工具"
    )


class AgentErrorHandleConfig(BaseModel):
    """Agent error handling config (identical to LLMErrorHandleConfig)."""

    method: HttpErrorHandle = Field(
        default=HttpErrorHandle.NONE,
        description="异常处理策略：'none' 抛出异常, 'default' 返回默认值, 'branch' 走异常分支",
    )

    output: str = Field(
        default="",
        description="Agent 异常时返回的默认输出文本（method=default 时生效）",
    )


class AgentModelCompletionParamsConfig(BaseModel):
    temperature: float | None = Field(default=0.7, ge=0.0, le=2.0, description="Temperature")
    max_tokens: int | None = Field(default=2000, ge=1, le=32000, description="Max output tokens")
    top_p: LLMTopPConfig = Field(default_factory=LLMTopPConfig, description="Top-p sampling config")
    top_k: LLMTopKConfig = Field(default_factory=LLMTopKConfig, description="Top-k sampling config")
    seed: LLMSeedConfig = Field(default_factory=LLMSeedConfig, description="Random seed config")
    repetition_penalty: LLMRepetitionPenaltyConfig = Field(default_factory=LLMRepetitionPenaltyConfig, description="Repetition penalty config")
    frequency_penalty: LLMFrequencyPenaltyConfig = Field(default_factory=LLMFrequencyPenaltyConfig, description="Frequency penalty config")
    presence_penalty: LLMPresencePenaltyConfig = Field(default_factory=LLMPresencePenaltyConfig, description="Presence penalty config")
    search: bool = Field(default=False, description="Enable model search")
    thinking: LLMThinkingConfig = Field(default_factory=LLMThinkingConfig, description="Thinking config")
    response_format: LLMResponseFormatConfig = Field(default_factory=LLMResponseFormatConfig, description="Response format config")
    extra_headers: LLMExtraHeadersConfig = Field(default_factory=LLMExtraHeadersConfig, description="Extra request headers config")
    stop: LLMStopConfig = Field(default_factory=LLMStopConfig, description="Stop sequence config")
    json_output: bool = Field(default=False, description="Force JSON output")
    structured_output: bool = Field(
        default=False,
        description="Whether to expose parsed JSON as structured_output and request JSON Schema output",
    )

    @field_validator("response_format", mode="before")
    @classmethod
    def coerce_response_format(cls, v):
        if isinstance(v, str):
            return LLMResponseFormatConfig(enable=True, value=v)
        return v


class AgentModelConfig(BaseModel):
    model_id: uuid.UUID | None = Field(default=None, description="Model config ID")
    provider: str | None = Field(default=None, description="Model provider")
    model: str | None = Field(default=None, description="Provider model name")
    model_type: str | None = Field(default="llm", description="Model type")
    completion_params: AgentModelCompletionParamsConfig = Field(
        default_factory=AgentModelCompletionParamsConfig,
        description="Model completion parameters",
    )


class AgentReferenceConfig(BaseModel):
    """Reference to a published Agent application."""

    app_id: uuid.UUID = Field(..., description="被引用的 Agent 应用 ID")
    release_policy: Literal["current", "pinned"] = Field(
        default="pinned",
        description="版本策略：current 跟随当前发布版本，pinned 固定版本",
    )
    release_id: uuid.UUID | None = Field(default=None, description="固定的 AppRelease ID")

    @model_validator(mode="after")
    def validate_release(self):
        if self.release_policy == "pinned" and self.release_id is None:
            raise ValueError("固定版本模式必须提供 release_id")
        return self


class AgentNodeConfig(BaseNodeConfig):
    """Agent node config

    The Agent node autonomously loops through thinking, tool selection and tool calls
    until it reaches a final answer.
    """

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_config(cls, value):
        """Migrate legacy Agent node config."""
        if not isinstance(value, dict):
            return value

        migrated = dict(value)
        # Referenced Agents inherit the parent workflow's full text turns; drop the legacy field.
        migrated.pop("history_mode", None)

        # Nested config wins; root-level legacy keys still get stripped for extra="forbid".
        knowledge_config = {
            key: migrated.pop(key)
            for key in (
                "knowledge_bases",
                "merge_strategy",
                "reranker_id",
                "reranker_top_k",
                "rerank_mode",
                "rerank_weights",
                "use_graph",
            )
            if key in migrated
        }
        if migrated.get("knowledge_retrieval") is None and "knowledge_bases" in knowledge_config:
            migrated["knowledge_retrieval"] = knowledge_config
        return migrated

    # Node source mode. Defaults to inline so existing workflows need no migration.
    mode: Literal["inline", "reference"] = Field(
        default="inline",
        description="Agent 配置来源：inline 节点内嵌配置，reference 引用已发布 Agent 应用",
    )
    reference: AgentReferenceConfig | None = Field(
        default=None,
        description="已发布 Agent 应用引用；mode=reference 时必填",
    )

    # Model selection (reuses the LLM node's model selection pattern)
    model: AgentModelConfig = Field(
        default_factory=AgentModelConfig,
        description="Model selector and completion parameters"
    )

    # System prompt
    system_prompt: str = Field(
        default="",
        description="Agent 系统提示词，支持模板变量，如：{{ sys.message }}"
    )

    # User message (the Agent's input)
    message: str = Field(
        default="{{ sys.message }}",
        description="发送给 Agent 的消息，支持模板变量"
    )

    context: str = Field(
        default="",
        description="注入 Agent 输入的工作流上下文变量，如 '{{sys.message}}'"
    )

    variable_mapping: dict[str, Any] | list[dict[str, Any]] = Field(
        default_factory=dict,
        description="引用 Agent 的应用变量映射；完整变量选择器保留原始值类型",
    )
    files: str | None = Field(
        default=None,
        description="引用 Agent 的文件或文件数组变量选择器，如 '{{sys.files}}'",
    )

    # Tool selection
    tools: list[ToolSelector] = Field(
        default_factory=list,
        description="Agent 可用工具列表（从工具库选择）"
    )

    # Agent builtin context tools
    knowledge_retrieval: KnowledgeRetrievalConfig | None = Field(
        default=None,
        description="关联的知识库检索工具配置；知识库为空时不启用",
    )
    long_term_memory: LongTermMemoryConfig = Field(
        default_factory=LongTermMemoryConfig,
        description="长期记忆检索工具配置；与对话历史窗口 memory 独立",
    )

    # Agent behavior parameters
    max_iterations: int = Field(
        default=10,
        ge=1,
        le=500,
        description="最大推理迭代次数"
    )

    # Agent strategy
    strategy: str = Field(
        default="react",
        description="Agent 执行策略，如 'react' / 'function_calling'"
    )

    memory: MemoryWindowSetting = Field(
        default_factory=MemoryWindowSetting,
        description="对话上下文窗口"
    )

    # Error handling
    error_handle: AgentErrorHandleConfig = Field(
        default_factory=AgentErrorHandleConfig,
        description="异常处理配置",
    )

    # Output variable definitions
    output_variables: list[VariableDefinition] = Field(
        default_factory=lambda: [
            VariableDefinition(
                name="output",
                type=VariableType.STRING,
                description="Agent 回复内容"
            ),
            VariableDefinition(
                name="reasoning_content",
                type=VariableType.STRING,
                description="推理内容（深度思考模式下的思考过程）"
            ),
            VariableDefinition(
                name="token_usage",
                type=VariableType.OBJECT,
                description="Token 使用情况"
            ),
            VariableDefinition(
                name="files",
                type=VariableType.ARRAY_FILE,
                description="Agent 生成的文件"
            ),
            VariableDefinition(
                name="json",
                type=VariableType.ARRAY_OBJECT,
                description="Agent 生成的 JSON 对象"
            ),
            VariableDefinition(
                name="param_warnings",
                type=VariableType.ARRAY_STRING,
                description="Model parameter warnings"
            ),
            VariableDefinition(
                name="citations",
                type=VariableType.ARRAY_OBJECT,
                description="引用 Agent 的知识库引用来源",
            ),
            VariableDefinition(
                name="reference_meta",
                type=VariableType.OBJECT,
                description="实际执行的 Agent 应用与发布版本信息",
            )
        ],
        description="输出变量定义（自动生成，通常不需要修改）"
    )

    @model_validator(mode="after")
    def validate_reference_mode(self):
        if self.mode == "reference" and self.reference is None:
            raise ValueError("引用模式必须提供 reference 配置")
        return self

    class Config:
        extra = "forbid"
        json_schema_extra = {
            "example": {
                "model": {
                    "model_id": "uuid-here",
                    "provider": "dashscope",
                    "model": "qwen3.6-plus",
                    "model_type": "llm",
                    "completion_params": {
                        "temperature": 0.7,
                        "max_tokens": 2000,
                    },
                },
                "system_prompt": "你是一个专业的研究助手，善于使用工具检索信息后再回答。",
                "message": "{{ sys.message }}",
                "context": "{{node_id.result}}",
                "tools": [
                    {"tool_id": "uuid-tool-1", "tool_type": "builtin", "operation": None, "enabled": True}
                ],
                "max_iterations": 10,
                "strategy": "react",
                "description": "自主推理 + 工具调用的 Agent 节点"
            }
        }
