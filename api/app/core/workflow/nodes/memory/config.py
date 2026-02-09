from uuid import UUID

from pydantic import BaseModel, field_validator, Field

from app.core.workflow.nodes.base_config import BaseNodeConfig


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


class MemoryReadNodeConfig(BaseNodeConfig):
    message: str = Field(
        ...
    )

    config_id: UUID | int = Field(
        ...
    )

    search_switch: str = Field(
        "0",
        description="Search mode: 0=verify, 1=direct, 2=context"
    )


class MemoryWriteNodeConfig(BaseNodeConfig):
    message: str = Field(
        ...
    )

    messages: list[MessageConfig] = Field(
        default_factory=list
    )

    config_id: UUID | int = Field(
        ...
    )
