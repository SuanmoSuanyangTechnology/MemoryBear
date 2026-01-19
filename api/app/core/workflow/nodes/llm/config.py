"""LLM 节点配置"""

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.core.workflow.nodes.base_config import BaseNodeConfig, VariableDefinition, VariableType


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


class LLMNodeConfig(BaseNodeConfig):
    """LLM 节点配置
    
    支持两种配置方式：
    1. 简单模式：使用 prompt 字段
    2. 消息模式：使用 messages 字段（推荐）
    """

    model_id: str = Field(
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

    # 模型参数
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

    top_p: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Top-p 采样参数"
    )

    frequency_penalty: float | None = Field(
        default=None,
        ge=-2.0,
        le=2.0,
        description="频率惩罚"
    )

    presence_penalty: float | None = Field(
        default=None,
        ge=-2.0,
        le=2.0,
        description="存在惩罚"
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
                name="token_usage",
                type=VariableType.OBJECT,
                description="Token 使用情况"
            )
        ],
        description="输出变量定义（自动生成，通常不需要修改）"
    )

    @field_validator("messages", "prompt")
    @classmethod
    def validate_input_mode(cls, v, info):
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
