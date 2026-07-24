"""Agent 节点配置

记忆熊工作流 Agent 节点——具备自主推理 + 工具调用能力（ReAct / Function Calling）。

复用 LLM 节点的配置模式（memory / thinking / error_handle），
内部通过 LangChainAgent 执行引擎驱动工具调用循环。
"""

import uuid

from pydantic import BaseModel, Field, field_validator

from app.core.workflow.nodes.base_config import BaseNodeConfig, VariableDefinition
from app.core.workflow.nodes.enums import HttpErrorHandle
from app.core.workflow.nodes.llm.config import (
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
from app.core.workflow.variable.base_variable import VariableType


class ToolSelector(BaseModel):
    """Agent 可用工具选择器

    指向工具库中的一个工具（内置 / 自定义 / MCP / 工作流），
    可选择该工具的某个子操作（对多操作工具，如 datetime_tool / json_tool / MCP）。
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
    """Agent 异常处理配置（与 LLMErrorHandleConfig 一致）"""

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


class AgentNodeConfig(BaseNodeConfig):
    """Agent 节点配置

    Agent 节点内部自主循环思考、选择并调用工具，直到得出最终答案。
    """

    # 模型选择（复用 LLM 节点的 model 选择模式）
    model: AgentModelConfig = Field(
        default_factory=AgentModelConfig,
        description="Model selector and completion parameters"
    )

    # 系统提示词
    system_prompt: str = Field(
        default="",
        description="Agent 系统提示词，支持模板变量，如：{{ sys.message }}"
    )

    # 用户消息（Agent 的输入）
    message: str = Field(
        default="{{ sys.message }}",
        description="发送给 Agent 的消息，支持模板变量"
    )

    context: str = Field(
        default="",
        description="注入 Agent 输入的工作流上下文变量，如 '{{sys.message}}'"
    )

    # 工具选择
    tools: list[ToolSelector] = Field(
        default_factory=list,
        description="Agent 可用工具列表（从工具库选择）"
    )

    # Agent 行为参数
    max_iterations: int = Field(
        default=10,
        ge=1,
        le=500,
        description="最大推理迭代次数"
    )

    # Agent 策略
    strategy: str = Field(
        default="react",
        description="Agent 执行策略，如 'react' / 'function_calling'"
    )

    memory: MemoryWindowSetting = Field(
        default_factory=MemoryWindowSetting,
        description="对话上下文窗口"
    )

    # 异常处理
    error_handle: AgentErrorHandleConfig = Field(
        default_factory=AgentErrorHandleConfig,
        description="异常处理配置",
    )

    # 输出变量定义
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
            )
        ],
        description="输出变量定义（自动生成，通常不需要修改）"
    )

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
