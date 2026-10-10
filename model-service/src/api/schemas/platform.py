"""平台面请求 schema（/internal/v1/platform/*，M7-5）：premium 控制台 → 服务侧编排。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ...models.models_model import ModelType
from ...schemas.model_schema import RejectLegacyModelFields


class PlatformModelCreateRequest(BaseModel):
    model_ids: list[str] = Field(
        ..., min_length=1, description="上游模型 ID 列表（整批创建，任一校验失败整批拒绝）"
    )


class PlatformModelUpdateRequest(BaseModel, RejectLegacyModelFields):
    type: ModelType | None = Field(None, description="模型类型（缺省保持现值；chat 已下线）")
    input_modalities: list[str] | None = Field(
        None, description="输入模态（缺省保持现值；不得为空列表）"
    )
    output_modalities: list[str] | None = Field(
        None, description="输出模态（缺省保持现值；不得为空列表）"
    )
    features: list[str] | None = Field(None, description="能力特征（缺省保持现值）")


class PlatformModelStatusRequest(BaseModel):
    is_active: bool = Field(..., description="启用/停用")


class PlatformChannelProvisionRequest(BaseModel):
    tenant_name: str = Field(..., min_length=1, description="上游 key 名称（租户名）")
    quota_limit: float = Field(..., ge=0, description="网关额度（初始赠送金额）")


__all__ = [
    "PlatformChannelProvisionRequest",
    "PlatformModelCreateRequest",
    "PlatformModelStatusRequest",
    "PlatformModelUpdateRequest",
]
