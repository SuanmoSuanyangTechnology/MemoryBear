from pydantic import BaseModel, Field, field_serializer, model_validator, ConfigDict, field_validator
from typing import Optional, List, Dict, Any
import datetime
import uuid

from app.core.utils.datetime_utils import to_timestamp_ms
from app.models.models_model import ModelProvider, ModelType, LoadBalanceStrategy


class RejectLegacyModelFields:
    """旧字段（capability/is_omni）与下线类型（type='chat'）守卫（2e）。

    pydantic 默认 `extra="ignore"`：仅删字段/成员会让旧值被静默吞掉，用户以为提交生效。
    请求类混入本守卫，显式提交旧字段或 `type='chat'` 即 422，提示迁移到契约 v2。
    """

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_model_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            legacy = [key for key in ("capability", "is_omni") if key in data]
            if legacy:
                raise ValueError(
                    f"字段 {', '.join(sorted(legacy))} 已下线，"
                    "请改用 input_modalities / output_modalities / features"
                )
            raw_type = data.get("type")
            if isinstance(raw_type, str) and raw_type.lower() == "chat":
                raise ValueError("type='chat' 已下线，请改用 type='llm'")
        return data


# ModelConfig Schemas
class ModelConfigBase(BaseModel):
    """模型配置基础Schema"""
    name: str = Field(..., description="模型显示名称", max_length=255)
    type: ModelType = Field(..., description="模型类型")
    logo: Optional[str] = Field(None, description="模型logo图片URL", max_length=255)
    description: Optional[str] = Field(None, description="模型描述")
    provider: str = Field(..., description="供应商")
    config: Optional[Dict[str, Any]] = Field({}, description="模型配置参数")
    is_active: bool = Field(True, description="是否激活")
    is_public: bool = Field(False, description="是否公开")
    load_balance_strategy: Optional[str] = Field(LoadBalanceStrategy.NONE.value, description="负载均衡策略")
    input_modalities: Optional[List[str]] = Field(None, description="输入模态（如['text','image']；缺省按 type/provider 定基）")
    output_modalities: Optional[List[str]] = Field(None, description="输出模态（如['text','audio']；缺省按 type/provider 定基）")
    features: Optional[List[str]] = Field(None, description="能力特征（如['thinking']；缺省为空）")
    model_id: Optional[uuid.UUID] = Field(None, description="基础模型ID")


class ApiKeyRegister(BaseModel):
    """模型域登记凭据（provider/model_name 由服务端按 config 读）"""
    api_key: str = Field(..., description="API密钥", max_length=500)
    api_base: Optional[str] = Field(None, description="API基础URL", max_length=500)
    remark: Optional[str] = Field(None, description="备注", max_length=255)
    priority: int = Field(0, description="优先级（大者优先）")


class ModelConfigCreate(ModelConfigBase, RejectLegacyModelFields):
    """创建自定义模型Schema（内嵌 credential：创建即登记点名渠道，单接口原子完成）

    自定义模型不经模型广场添加，provider 级渠道不保证可用，因此凭据必填并
    在创建时做活体验证；验证失败拒绝创建（零落库）。
    """
    credential: ApiKeyRegister = Field(..., description="模型凭据（必填，创建时活体验证）")


class CompositeMemberSpec(BaseModel):
    """组合成员声明：以 (provider, model_name) 定位租户内成员 config。"""
    provider: str = Field(..., min_length=1, max_length=64, description="成员模型供应商")
    model_name: str = Field(..., min_length=1, max_length=255, description="成员模型名称")


class CompositeModelCreate(BaseModel, RejectLegacyModelFields):
    """创建组合模型Schema"""
    name: str = Field(..., description="组合模型名称（别名，真实调用名在成员声明）", max_length=255)
    type: Optional[ModelType] = Field(None, description="模型类型")
    logo: Optional[str] = Field(None, description="模型logo图片URL", max_length=255)
    description: Optional[str] = Field(None, description="模型描述")
    config: Optional[Dict[str, Any]] = Field({}, description="模型配置参数")
    is_active: bool = Field(True, description="是否激活")
    is_public: bool = Field(False, description="是否公开")
    members: Optional[List[CompositeMemberSpec]] = Field(None, description="组合成员列表（(provider, model_name) 声明）")
    load_balance_strategy: Optional[str] = Field(default=LoadBalanceStrategy.NONE.value, description="负载均衡策略")


class ModelConfigUpdate(BaseModel, RejectLegacyModelFields):
    """更新模型配置Schema"""
    name: Optional[str] = Field(None, description="模型显示名称", max_length=255)
    type: Optional[ModelType] = Field(None, description="模型类型")
    provider: Optional[str] = Field(None, description="供应商")
    logo: Optional[str] = Field(None, description="模型logo图片URL", max_length=255)
    description: Optional[str] = Field(None, description="模型描述")
    config: Optional[Dict[str, Any]] = Field(None, description="模型配置参数")
    is_active: Optional[bool] = Field(None, description="是否激活")
    is_public: Optional[bool] = Field(None, description="是否公开")
    input_modalities: Optional[List[str]] = Field(None, description="输入模态（缺省保持现值；不得为空列表）")
    output_modalities: Optional[List[str]] = Field(None, description="输出模态（缺省保持现值；不得为空列表）")
    features: Optional[List[str]] = Field(None, description="能力特征（缺省保持现值）")


class ModelConfig(ModelConfigBase):
    """模型配置Schema

    `is_available` = 渠道候选探测结果（列表/详情计算；None = 本响应未探测）；
    `members` = 组合成员摘要（声明序；非组合为空）。凭据列表走 `GET /{model_id}/apikeys`。
    """
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime.datetime
    updated_at: datetime.datetime
    is_deprecated: bool = False
    is_available: Optional[bool] = None
    members: List[CompositeMemberSpec] = []
    # 响应侧恒输出（由 profile 派生，见 model_profile_view.wire_model_config）
    input_modalities: List[str] = []
    output_modalities: List[str] = []
    features: List[str] = []

    @classmethod
    def model_validate(cls, obj, **kwargs):
        instance = super().model_validate(obj, **kwargs)
        if hasattr(obj, "model_base") and obj.model_base is not None:
            instance.is_deprecated = bool(obj.model_base.is_deprecated)
        if getattr(obj, "provider", None) == ModelProvider.COMPOSITE:
            instance.members = _parse_member_specs(getattr(obj, "config", None))
        return instance

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime | None):
        return to_timestamp_ms(dt)

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)


def _parse_member_specs(config: Dict[str, Any] | None) -> List[CompositeMemberSpec]:
    """组合 config JSON members[] → 声明摘要（脏项跳过，与运行期 parse_members 同口径）。"""
    raw = (config or {}).get("members")
    if not isinstance(raw, list):
        return []
    specs: List[CompositeMemberSpec] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        provider = item.get("provider")
        model_name = item.get("model_name")
        if not isinstance(provider, str) or not provider:
            continue
        if not isinstance(model_name, str) or not model_name:
            continue
        specs.append(CompositeMemberSpec(provider=provider, model_name=model_name))
    return specs


class ApiKeyItem(BaseModel):
    """凭据条目（脱敏；模型域候选与 Provider 域列表共用，id = 渠道 id）"""
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: uuid.UUID
    provider: str
    credential_masked: str
    is_provider_level: bool = False
    model_names: List[str] = Field(default_factory=list, description="点名覆盖集（空 = 供应商公共）")
    api_base: Optional[str] = None
    is_active: bool = True
    priority: int = 0
    source: str = "manual"
    remark: Optional[str] = None
    created_at_ms: Optional[int] = None
    updated_at_ms: Optional[int] = None


class ProviderApiKeyCreate(ApiKeyRegister):
    """Provider 域登记公共凭据（provider 级 [] 渠道，覆盖该供应商全部未点名模型）

    公共渠道固定使用供应商公共基地址，不接受 api_base；本地提供商无公共端点
    不可登记。自定义端点请改在模型域按模型登记（或编辑点名渠道）。
    """
    provider: ModelProvider = Field(..., description="API Key提供商")
    api_base: Optional[str] = Field(
        None,
        description="不接受：公共渠道使用供应商公共基地址（传非空值将 400）",
        max_length=500,
    )


class ProviderApiKeyUpdate(BaseModel):
    """渠道属性更新（Provider 域公共渠道与模型域点名渠道共用）

    省略 = 不改；api_base/remark 显式 null = 清空；priority/is_active 显式 null 视为省略；
    api_key 非空 = 重填凭据（重加密）。model_names 不可改。
    """
    priority: Optional[int] = Field(None, description="优先级（大者优先）")
    remark: Optional[str] = Field(None, description="备注", max_length=255)
    api_base: Optional[str] = Field(
        None,
        description="API基础URL（仅点名渠道；公共渠道传非空值将 400；null 清空）",
        max_length=500,
    )
    is_active: Optional[bool] = Field(None, description="渠道启停")
    api_key: Optional[str] = Field(None, description="重填凭据", max_length=500)


class ModelConfigQuery(BaseModel):
    """模型配置查询Schema"""
    type: Optional[List[ModelType]] = Field(None, description="模型类型筛选（支持多个）")
    provider: Optional[ModelProvider] = Field(None, description="提供商筛选（按模型配置的 provider）")
    is_active: Optional[bool] = Field(None, description="激活状态筛选")
    is_public: Optional[bool] = Field(None, description="公开状态筛选")
    is_available: Optional[bool] = Field(
        None, description="可用性筛选（未弃用且渠道候选非空；置位时服务端全量探测后内存分页）"
    )
    search: Optional[str] = Field(None, description="搜索关键词", max_length=255)
    page: int = Field(1, description="页码", ge=1)
    pagesize: int = Field(10, description="每页数量", ge=1, le=100)


# 查询和响应Schemas
class ModelConfigQueryNew(BaseModel):
    """模型配置查询Schema"""
    type: Optional[List[ModelType]] = Field(None, description="模型类型筛选（支持多个）")
    provider: Optional[ModelProvider] = Field(None, description="提供商筛选（按模型配置的 provider）")
    is_active: Optional[bool] = Field(None, description="激活状态筛选")
    is_public: Optional[bool] = Field(None, description="公开状态筛选")
    is_composite: Optional[bool] = Field(None, description="组合模型筛选")
    search: Optional[str] = Field(None, description="搜索关键词", max_length=255)


class ModelMarketplace(BaseModel):
    """模型广场响应Schema"""
    llm_models: List[ModelConfig] = []
    embedding_models: List[ModelConfig] = []
    rerank_models: List[ModelConfig] = []
    total_count: int
    active_count: int


# 验证模型配置Schema
class ModelValidateRequest(BaseModel):
    """验证模型配置请求"""
    model_name: str = Field(..., description="模型实际名称")
    provider: ModelProvider = Field(..., description="API Key提供商")
    api_key: str = Field(..., description="API密钥")
    api_base: Optional[str] = Field(None, description="API基础URL")
    model_type: Optional[ModelType] = Field(ModelType.LLM, description="模型类型")
    test_message: Optional[str] = Field("Hello", description="测试消息")


class ModelValidateResponse(BaseModel):
    """验证模型配置响应"""
    valid: bool = Field(..., description="是否有效")
    message: str = Field(..., description="验证消息")
    response: Optional[str] = Field(None, description="模型响应内容")
    elapsed_time: Optional[float] = Field(None, description="响应时间（秒）")
    error: Optional[str] = Field(None, description="错误信息")
    usage: Optional[Dict[str, Any]] = Field(None, description="Token使用情况")


# 更新前向引用
ModelConfig.model_rebuild()


# ModelBase Schemas
class ModelBaseCreate(BaseModel, RejectLegacyModelFields):
    """创建基础模型Schema"""
    name: str = Field(..., description="模型唯一标识", max_length=255)
    type: ModelType = Field(..., description="模型类型")
    provider: ModelProvider = Field(..., description="提供商")
    logo: Optional[str] = Field(None, description="模型logo图片URL", max_length=255)
    description: Optional[str] = Field(None, description="模型描述")
    is_official: bool = Field(True, description="是否供应商官方模型")
    tags: List[str] = Field(default_factory=list, description="模型标签")
    input_modalities: Optional[List[str]] = Field(None, description="输入模态（缺省按 type/provider 定基）")
    output_modalities: Optional[List[str]] = Field(None, description="输出模态（缺省按 type/provider 定基）")
    features: Optional[List[str]] = Field(None, description="能力特征（缺省为空）")


class ModelBaseUpdate(BaseModel, RejectLegacyModelFields):
    """更新基础模型Schema"""
    name: Optional[str] = Field(None, description="模型唯一标识", max_length=255)
    type: Optional[ModelType] = Field(None, description="模型类型")
    provider: Optional[ModelProvider] = Field(None, description="提供商")
    logo: Optional[str] = Field(None, description="模型logo图片URL", max_length=255)
    description: Optional[str] = Field(None, description="模型描述")
    is_deprecated: Optional[bool] = Field(None, description="是否弃用")
    is_official: Optional[bool] = Field(None, description="是否供应商官方模型")
    tags: Optional[List[str]] = Field(None, description="模型标签")
    input_modalities: Optional[List[str]] = Field(None, description="输入模态（缺省保持现值；不得为空列表）")
    output_modalities: Optional[List[str]] = Field(None, description="输出模态（缺省保持现值；不得为空列表）")
    features: Optional[List[str]] = Field(None, description="能力特征（缺省保持现值）")


class ModelBase(BaseModel):
    """基础模型Schema"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    type: str
    provider: str
    logo: Optional[str]
    description: Optional[str]
    is_deprecated: bool
    is_official: bool
    tags: List[str]
    add_count: int
    # 响应侧恒输出（profile 派生，见 model_profile_view.wire_model_base）
    input_modalities: List[str] = []
    output_modalities: List[str] = []
    features: List[str] = []

    @field_validator("type", mode="before")
    @classmethod
    def canonicalize_legacy_type(cls, value):
        """`type` 为裸 str（非枚举），存量字符串读侧归一：asr 大小写、chat → llm。"""
        if isinstance(value, str):
            if value.lower() == "asr":
                return ModelType.ASR.value
            if value.lower() == "chat":
                return ModelType.LLM.value
        return value


class ModelBaseQuery(BaseModel):
    """基础模型查询Schema"""
    type: Optional[ModelType] = Field(None, description="模型类型")
    provider: Optional[ModelProvider] = Field(None, description="提供商")
    is_official: Optional[bool] = Field(None, description="是否官方模型")
    is_deprecated: Optional[bool] = Field(None, description="是否弃用")
    search: Optional[str] = Field(None, description="搜索关键词", max_length=255)


class ModelInfo(BaseModel):
    """模型信息Schema（运行期壳；能力载体为契约 v2 三列）"""
    model_name: str = Field(..., description="模型名称")
    provider: str = Field(..., description="模型提供商")
    api_key: str = Field(..., description="API密钥")
    api_base: Optional[str] = Field(None, description="API基础URL；空=使用提供商默认地址")
    model_type: ModelType = Field(..., description="模型类型")
    input_modalities: List[str] = Field(default_factory=list, description="输入模态（契约 v2）")
    output_modalities: List[str] = Field(default_factory=list, description="输出模态（契约 v2）")
    features: List[str] = Field(default_factory=list, description="能力特征（契约 v2）")
    tenant_id: Optional[str] = Field(None, description="用量归属：租户ID")
    model_config_id: Optional[str] = Field(None, description="用量归属：模型配置ID")
    channel_id: Optional[str] = Field(None, description="用量归属：渠道ID")
    # 渠道换线计划随行透传（from_api_key 读取）；exclude 防序列化泄漏，拷贝丢失语义=退化单候选
    failover_plan: Any = Field(default=None, exclude=True, repr=False)
