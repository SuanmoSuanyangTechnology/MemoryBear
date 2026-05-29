"""LLM 节点配置"""

from typing import Any
import uuid

from pydantic import BaseModel, Field, field_validator

from app.core.workflow.nodes.base_config import BaseNodeConfig, VariableDefinition
from app.core.workflow.nodes.enums import HttpErrorHandle
from app.core.workflow.variable.base_variable import VariableType
from app.models.models_model import ModelCapability, ModelProvider


class MessageConfig(BaseModel):
    """消息配置"""

    role: str = Field(
        default='user',
        description="消息角色：system, user, assistant"
    )

    content: str = Field(
        default="",
        description="消息内容，支持模板变量，如：{{ sys.message }}"
    )

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        """验证角色"""
        allowed_roles = ["system", "user", "human", "assistant", "ai"]
        if v.lower() not in allowed_roles:
            raise ValueError(f"角色必须是以下之一: {', '.join(allowed_roles)}")
        return v.lower()


class MemoryWindowSetting(BaseModel):
    enable: bool = Field(
        default=False,
        description="启用记忆"
    )

    enable_window: bool = Field(
        default=False,
        description="启用记忆窗口"
    )

    window_size: int = Field(
        default=20,
        description="记忆窗口大小"
    )


class LLMErrorHandleConfig(BaseModel):
    """LLM 异常处理配置"""

    method: HttpErrorHandle = Field(
        default=HttpErrorHandle.NONE,
        description="异常处理策略：'none' 抛出异常, 'default' 返回默认值, 'branch' 走异常分支",
    )

    output: str = Field(
        default="",
        description="LLM 异常时返回的默认输出文本（method=default 时生效）",
    )


class LLMRetryConfig(BaseModel):
    """LLM 节点失败重试配置"""

    enable: bool = Field(
        default=False,
        description="是否启用失败重试"
    )

    max_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        description="最大重试次数"
    )

    retry_interval: int = Field(
        default=100,
        ge=10,
        le=60000,
        description="重试间隔（毫秒）"
    )


class LLMTopPConfig(BaseModel):
    enable: bool = Field(default=False, description="是否启用 Top-p 参数")
    value: float | None = Field(default=None, ge=0.0, le=1.0, description="Top-p 采样参数")


class LLMTopKConfig(BaseModel):
    enable: bool = Field(default=False, description="是否启用取样数量")
    value: int | None = Field(default=None, ge=0, le=100, description="取样数量（Top-k 采样）")


class LLMSeedConfig(BaseModel):
    enable: bool = Field(default=False, description="是否启用随机种子")
    value: int | None = Field(default=None, ge=0, le=18446744073709551615, description="随机种子")


class LLMRepetitionPenaltyConfig(BaseModel):
    enable: bool = Field(default=False, description="是否启用重复惩罚")
    value: float | None = Field(default=None, ge=0.0, le=2.0, description="重复惩罚")


class LLMFrequencyPenaltyConfig(BaseModel):
    enable: bool = Field(default=False, description="是否启用频率惩罚")
    value: float | None = Field(default=None, ge=-2.0, le=2.0, description="频率惩罚")


class LLMPresencePenaltyConfig(BaseModel):
    enable: bool = Field(default=False, description="是否启用存在惩罚")
    value: float | None = Field(default=None, ge=-2.0, le=2.0, description="存在惩罚")


class LLMThinkingBudgetConfig(BaseModel):
    enable: bool = Field(default=False, description="是否启用思考长度限制")
    value: int | None = Field(default=None, ge=128, le=65536, description="思考长度限制")


class LLMThinkingConfig(BaseModel):
    enable: bool = Field(default=False, description="是否启用思考模式")
    budget: LLMThinkingBudgetConfig = Field(default_factory=LLMThinkingBudgetConfig, description="思考长度限制配置")


class LLMResponseFormatConfig(BaseModel):
    enable: bool = Field(default=False, description="是否启用回复格式")
    value: str | None = Field(default=None, description="回复格式（如：json_object, text）")


class LLMExtraHeadersConfig(BaseModel):
    enable: bool = Field(default=False, description="是否启用额外请求头")
    value: str | None = Field(default=None, description="额外请求头，JSON 字符串格式")


class LLMStopConfig(BaseModel):
    enable: bool = Field(default=False, description="是否启用停止序列")
    value: list[str] | None = Field(default=None, description="停止序列（最多4个）")





class LLMNodeConfig(BaseNodeConfig):
    """LLM 节点配置
    
    支持两种配置方式：
    1. 简单模式：使用 prompt 字段
    2. 消息模式：使用 messages 字段（推荐）
    """

    model_id: uuid.UUID = Field(
        ...,
        description="模型配置 ID"
    )

    context: Any = Field(
        default="",
        description="上下文"
    )

    memory: MemoryWindowSetting = Field(
        default_factory=MemoryWindowSetting,
        description="对话上下文窗口"
    )

    vision: bool = Field(
        default=False,
        description="是否启用视觉模型"
    )

    vision_input: str = Field(
        default=None,
        description="视觉输入"
    )

    # 简单模式
    prompt: str | None = Field(
        default=None,
        description="提示词模板（简单模式），支持变量引用"
    )

    # 消息模式（推荐）
    messages: list[MessageConfig] | None = Field(
        default=None,
        description="消息列表（消息模式），支持多轮对话"
    )

    # 模型参数（始终生效）
    temperature: float | None = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="温度参数，控制输出的随机性"
    )

    max_tokens: int | None = Field(
        default=1000,
        ge=1,
        le=32000,
        description="最大生成 token 数"
    )

    # 模型参数（需开关控制）
    top_p: LLMTopPConfig = Field(default_factory=LLMTopPConfig, description="Top-p 采样参数配置")
    top_k: LLMTopKConfig = Field(default_factory=LLMTopKConfig, description="取样数量配置")
    seed: LLMSeedConfig = Field(default_factory=LLMSeedConfig, description="随机种子配置")
    repetition_penalty: LLMRepetitionPenaltyConfig = Field(default_factory=LLMRepetitionPenaltyConfig, description="重复惩罚配置")
    frequency_penalty: LLMFrequencyPenaltyConfig = Field(default_factory=LLMFrequencyPenaltyConfig, description="频率惩罚配置")
    presence_penalty: LLMPresencePenaltyConfig = Field(default_factory=LLMPresencePenaltyConfig, description="存在惩罚配置")

    search: bool = Field(default=False, description="是否启用联网搜索")
    thinking: LLMThinkingConfig = Field(default_factory=LLMThinkingConfig, description="思考模式配置")
    response_format: LLMResponseFormatConfig = Field(default_factory=LLMResponseFormatConfig, description="回复格式配置")
    extra_headers: LLMExtraHeadersConfig = Field(default_factory=LLMExtraHeadersConfig, description="额外请求头配置")
    stop: LLMStopConfig = Field(default_factory=LLMStopConfig, description="停止序列配置")

    json_output: bool = Field(
        default=False,
        description="是否以 JSON 格式输出"
    )

    enable_reasoning_content_extraction: bool = Field(
        default=False,
        description="是否启用推理标签分离（从 think 标签提取内容到 reasoning_content 字段）"
    )

    retry: LLMRetryConfig = Field(
        default_factory=LLMRetryConfig,
        description="失败重试配置"
    )

    # 输出变量定义
    output_variables: list[VariableDefinition] = Field(
        default_factory=lambda: [
            VariableDefinition(
                name="output",
                type=VariableType.STRING,
                description="LLM 生成的文本输出"
            ),
            VariableDefinition(
                name="reasoning_content",
                type=VariableType.STRING,
                description="推理内容（启用推理标签分离后，从 think 标签提取的内容）"
            ),
            VariableDefinition(
                name="token_usage",
                type=VariableType.OBJECT,
                description="Token 使用情况"
            )
        ],
        description="输出变量定义（自动生成，通常不需要修改）"
    )

    error_handle: LLMErrorHandleConfig = Field(
        default_factory=LLMErrorHandleConfig,
        description="LLM 异常处理配置",
    )

    @field_validator("response_format", mode="before")
    @classmethod
    def coerce_response_format(cls, v):
        if isinstance(v, str):
            return LLMResponseFormatConfig(enable=True, value=v)
        return v

    @field_validator("messages", "prompt")
    @classmethod
    def validate_input_mode(cls, v):
        """验证输入模式：prompt 和 messages 至少有一个"""
        # 这个验证在 model_validator 中更合适
        return v

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "model_id": "uuid-here",
                    "prompt": "请回答：{{ sys.message }}",
                    "temperature": 0.7,
                    "max_tokens": 1000
                },
                {
                    "model_id": "uuid-here",
                    "messages": [
                        {
                            "role": "system",
                            "content": "你是一个专业的 AI 助手"
                        },
                        {
                            "role": "user",
                            "content": "{{ sys.message }}"
                        }
                    ],
                    "temperature": 0.7,
                    "max_tokens": 1000
                }
            ]
        }


_PARAM_CAPABILITY_REQUIREMENTS: dict[str, list[ModelCapability]] = {
    "thinking": [ModelCapability.THINKING, ModelCapability.THINKING_ONLY],
    "thinking_budget": [ModelCapability.THINKING],
    "json_output": [ModelCapability.JSON_OUTPUT],
    "response_format_json": [ModelCapability.JSON_OUTPUT],
}

_PARAM_CAPABILITY_WARNINGS: dict[str, str] = {
    "thinking": "模型不具备思考能力，思考模式参数不会生效",
    "thinking_budget": "thinking_only 类型模型不支持思考长度限制，该参数不会生效",
    "thinking_budget_no_thinking": "模型不具备思考能力，思考长度限制参数不会生效",
    "json_output": "模型不支持 JSON 输出能力，JSON 输出参数不会生效",
    "response_format_json": "模型不支持 JSON 输出能力，回复格式(JSON)参数不会生效",
}

_OPENAI_COMPATIBLE_PROVIDERS = frozenset({
    ModelProvider.OPENAI, ModelProvider.XINFERENCE, ModelProvider.GPUSTACK, ModelProvider.VOLCANO,
})

_PARAM_PROVIDER_SUPPORT: dict[str, frozenset[ModelProvider]] = {
    "top_k": frozenset({ModelProvider.OLLAMA, ModelProvider.DASHSCOPE, ModelProvider.BEDROCK}),
    "repetition_penalty": frozenset({ModelProvider.OLLAMA, ModelProvider.DASHSCOPE}),
    "seed": frozenset({
        ModelProvider.OPENAI, ModelProvider.XINFERENCE, ModelProvider.GPUSTACK,
        ModelProvider.OLLAMA, ModelProvider.VOLCANO, ModelProvider.DASHSCOPE,
        ModelProvider.BEDROCK,
    }),
    "frequency_penalty": frozenset({
        ModelProvider.OPENAI, ModelProvider.XINFERENCE, ModelProvider.GPUSTACK,
        ModelProvider.VOLCANO, ModelProvider.DASHSCOPE,
    }),
    "presence_penalty": frozenset({
        ModelProvider.OPENAI, ModelProvider.XINFERENCE, ModelProvider.GPUSTACK,
        ModelProvider.VOLCANO, ModelProvider.DASHSCOPE,
    }),
    "enable_search": frozenset({ModelProvider.DASHSCOPE}),
}

_PARAM_PROVIDER_WARNINGS: dict[str, str] = {
    "top_k": "当前提供商不支持取样数量(top_k)参数，该参数已自动剥离",
    "repetition_penalty": "当前提供商不支持重复惩罚参数，该参数已自动剥离",
    "seed": "当前提供商不支持随机种子参数，该参数已自动剥离",
    "frequency_penalty": "当前提供商不支持频率惩罚参数，该参数已自动剥离",
    "presence_penalty": "当前提供商不支持存在惩罚参数，该参数已自动剥离",
    "enable_search": "当前提供商不支持联网搜索参数，该参数已自动剥离",
}

# Providers whose LLM chat class accepts OpenAI-style multimodal content
# format: [{"type": "text", "text": "..."}], [{"type": "image_url", ...}] etc.
# DashScope non-Omni (ChatTongyi) uses its own format and rejects OpenAI-style lists.
_MULTIMODAL_COMPATIBLE_PROVIDERS = frozenset({
    ModelProvider.OPENAI, ModelProvider.XINFERENCE, ModelProvider.GPUSTACK,
    ModelProvider.VOLCANO,
    ModelProvider.OLLAMA,
    ModelProvider.BEDROCK,
})


def strip_unsupported_llm_params(
        extra_params: dict[str, Any],
        provider: str,
        is_omni: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """Strip provider-unsupported parameters from extra_params.

    Parameters listed in _PARAM_PROVIDER_SUPPORT are only kept when the
    provider (or, for DashScope, the Omni variant) is in the support set.
    Other parameters (top_p, frequency_penalty, presence_penalty, etc.)
    are kept by default — they'll be routed to model_kwargs or top-level
    by RedBearModelFactory.get_model_params based on the provider.

    Note: temperature, max_tokens, seed, stop, top_k, repetition_penalty,
    enable_search are extracted from extra_params before reaching
    RedBearModelFactory and then re-routed per provider. They are not
    affected by this stripping step.

    Returns:
        (stripped_params, warnings): filtered params and warning messages
        for each stripped parameter.
    """
    warnings: list[str] = []
    provider_lower = provider.lower() if provider else ""

    try:
        provider_enum = ModelProvider(provider_lower)
    except ValueError:
        return extra_params, warnings

    # DashScope Omni is OpenAI-compatible; non-Omni (ChatTongyi) is not.
    # frequency_penalty / presence_penalty are only safe for Omni.
    # Other params (top_k, seed, enable_search, repetition_penalty) are
    # supported by ChatTongyi via model_kwargs routing in RedBearModelFactory.
    effective_provider = provider_enum
    if provider_enum == ModelProvider.DASHSCOPE and not is_omni:
        # Map DashScope non-Omni to a virtual "dashscope_native" so that
        # OpenAI-only params (frequency_penalty, presence_penalty) are
        # stripped while DashScope-native params (top_k, seed,
        # enable_search, repetition_penalty via model_kwargs) remain.
        _DASHSCOPE_NATIVE_SUPPORT: dict[str, bool] = {
            "top_k": True,
            "repetition_penalty": True,
            "seed": True,
            "frequency_penalty": False,
            "presence_penalty": False,
            "enable_search": True,
        }
        for param_key, supported in _DASHSCOPE_NATIVE_SUPPORT.items():
            if param_key in extra_params and not supported:
                warnings.append(_PARAM_PROVIDER_WARNINGS.get(param_key, f"参数 {param_key} 已自动剥离"))
                extra_params.pop(param_key, None)
        return extra_params, warnings

    # General case: check _PARAM_PROVIDER_SUPPORT
    for param_key, supported_providers in _PARAM_PROVIDER_SUPPORT.items():
        if param_key in extra_params and provider_enum not in supported_providers:
            warnings.append(_PARAM_PROVIDER_WARNINGS.get(param_key, f"参数 {param_key} 已自动剥离"))
            extra_params.pop(param_key, None)

    return extra_params, warnings


def validate_llm_param_constraints(
    config: LLMNodeConfig,
    capability: list[str],
    provider: str,
    is_omni: bool = False,
) -> list[str]:
    """校验 LLM 节点参数设置是否受模型能力或提供商支持限制。

    当用户启用了模型不具备的能力参数或提供商不支持的参数时，
    返回对应的警告消息列表。

    Args:
        config: LLM 节点配置（含各参数的 enable/value 开关）
        capability: 模型能力列表（如 ['thinking', 'json_output']）
        provider: 模型提供商（如 'openai', 'dashscope'）
        is_omni: 是否为 Omni 模型（影响 DashScope 参数路由）

    Returns:
        警告消息列表，无问题时返回空列表
    """
    warnings: list[str] = []
    provider_lower = provider.lower() if provider else ""
    capability_set = set(capability) if capability else set()

    try:
        provider_enum = ModelProvider(provider_lower)
    except ValueError:
        provider_enum = None

    # --- 模型能力限制校验 ---
    if config.thinking.enable:
        required = _PARAM_CAPABILITY_REQUIREMENTS["thinking"]
        if not any(c in capability_set for c in required):
            warnings.append(_PARAM_CAPABILITY_WARNINGS["thinking"])

    if config.thinking.budget.enable:
        required_budget = _PARAM_CAPABILITY_REQUIREMENTS["thinking_budget"]
        if ModelCapability.THINKING_ONLY in capability_set:
            warnings.append(_PARAM_CAPABILITY_WARNINGS["thinking_budget"])
        elif not any(c in capability_set for c in required_budget):
            warnings.append(_PARAM_CAPABILITY_WARNINGS["thinking_budget_no_thinking"])

    if config.json_output:
        required = _PARAM_CAPABILITY_REQUIREMENTS["json_output"]
        if not any(c in capability_set for c in required):
            warnings.append(_PARAM_CAPABILITY_WARNINGS["json_output"])

    if config.response_format.enable and config.response_format.value == "json_object":
        required = _PARAM_CAPABILITY_REQUIREMENTS["response_format_json"]
        if not any(c in capability_set for c in required):
            warnings.append(_PARAM_CAPABILITY_WARNINGS["response_format_json"])

    # --- 提供商支持限制校验 ---
    if provider_enum is None:
        return warnings

    if config.top_k.enable:
        supported = _PARAM_PROVIDER_SUPPORT["top_k"]
        if provider_enum not in supported:
            warnings.append(_PARAM_PROVIDER_WARNINGS["top_k"])

    if config.repetition_penalty.enable:
        supported = _PARAM_PROVIDER_SUPPORT["repetition_penalty"]
        if provider_enum not in supported:
            warnings.append(_PARAM_PROVIDER_WARNINGS["repetition_penalty"])

    if config.frequency_penalty.enable:
        supported = _PARAM_PROVIDER_SUPPORT["frequency_penalty"]
        if provider_enum not in supported:
            warnings.append(_PARAM_PROVIDER_WARNINGS["frequency_penalty"])

    if config.presence_penalty.enable:
        supported = _PARAM_PROVIDER_SUPPORT["presence_penalty"]
        if provider_enum not in supported:
            warnings.append(_PARAM_PROVIDER_WARNINGS["presence_penalty"])

    if config.seed.enable:
        supported = _PARAM_PROVIDER_SUPPORT["seed"]
        if provider_enum not in supported:
            warnings.append(_PARAM_PROVIDER_WARNINGS["seed"])

    if config.search:
        supported = _PARAM_PROVIDER_SUPPORT["enable_search"]
        if provider_enum not in supported:
            warnings.append(_PARAM_PROVIDER_WARNINGS["enable_search"])

    return warnings


