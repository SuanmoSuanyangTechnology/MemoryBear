import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.workflow.nodes.base_config import BaseNodeConfig


class FormFieldConfig(BaseModel):
    """人工介入节点的表单字段。

    极简 schema：通过 ``default_value`` 或 ``variable_ref`` 的存在与否来区分类型
        - 文本字段（用户可编辑）：包含 ``default_value``
        - 变量展示字段（只读）：包含 ``variable_ref``
    """

    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., description="表单字段 ID，必须以字母或下划线开头")
    default_value: str | None = Field(default=None, description="默认值（文本字段时使用）")
    variable_ref: str | None = Field(
        default=None,
        description="变量引用，如 {{ sys.message }}（变量展示字段时使用）"
    )

    @field_validator("id")
    @classmethod
    def validate_field_id(cls, v: str) -> str:
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', v):
            raise ValueError(
                f"Field ID '{v}' 必须以字母或下划线开头，后跟字母、数字或下划线"
            )
        return v

    @model_validator(mode="after")
    def validate_field_kind(self) -> "FormFieldConfig":
        has_default = self.default_value is not None
        has_ref = self.variable_ref is not None
        if has_default and has_ref:
            raise ValueError(
                f"Field '{self.id}' 不能同时设置 default_value 和 variable_ref"
            )
        return self


class ActionConfig(BaseModel):
    id: str = Field(..., description="操作 ID，必须以字母或下划线开头")
    label: str = Field(..., description="按钮显示文本")
    variant: str = Field(default="", description="按钮样式，由前端定义和校验")

    @field_validator("id")
    @classmethod
    def validate_action_id(cls, v: str) -> str:
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', v):
            raise ValueError(
                f"Action ID '{v}' 必须以字母或下划线开头，后跟字母、数字或下划线"
            )
        return v


class TimeoutConfig(BaseModel):
    unit: str = Field(default="minutes", description="单位: seconds, minutes, hours 或 days")
    value: int = Field(default=1, ge=1, description="超时时间")


class WebappConfig(BaseModel):
    enabled: bool = Field(default=True, description="是否启用 WebApp 提交")


class EmailConfig(BaseModel):
    enabled: bool = Field(default=False, description="是否启用 Email 提交")
    # 预留 Email 相关配置字段
    # recipients: list[str] = Field(default_factory=list, description="收件人列表")
    # subject_template: str = Field(default="", description="邮件主题模板")


class DeliveryMethodConfig(BaseModel):
    webapp: WebappConfig = Field(default_factory=WebappConfig, description="WebApp 提交配置")
    email: EmailConfig = Field(default_factory=EmailConfig, description="Email 提交配置")


class HumanInterventionNodeConfig(BaseNodeConfig):
    delivery_method: DeliveryMethodConfig = Field(
        default_factory=DeliveryMethodConfig,
        description="提交方式配置，支持多个提交方式同时开启"
    )

    @field_validator("delivery_method", mode="before")
    @classmethod
    def coerce_delivery_method(cls, v):
        """兼容旧版配置：delivery_method 为字符串或非法类型时转换为 DeliveryMethodConfig"""
        if isinstance(v, str):
            # 旧格式: "webapp" / "email" — 转换为对应的嵌套配置
            if v == "email":
                return {"webapp": {"enabled": False}, "email": {"enabled": True}}
            # 默认视为 webapp
            return {"webapp": {"enabled": True}, "email": {"enabled": False}}
        # 兼容空列表等非 dict 类型（旧配置或前端未发送该字段）
        if not isinstance(v, dict):
            return {"webapp": {"enabled": True}, "email": {"enabled": False}}
        return v
    content: str = Field(
        default="",
        description="表单内容，支持 Markdown 和 {{变量}}"
    )
    form_fields: list[FormFieldConfig] = Field(
        default_factory=list,
        description="表单字段列表，支持 editable（用户填写）和 display（展示变量）两种模式"
    )
    actions: list[ActionConfig] = Field(
        default_factory=list,
        min_length=1,
        description="用户操作按钮列表，至少一个"
    )
    timeout: TimeoutConfig = Field(
        default_factory=TimeoutConfig,
        description="超时设置"
    )