from pydantic import BaseModel, Field, field_serializer, model_validator, ConfigDict, field_validator, StringConstraints
from typing import Optional, List, Dict, Any, Annotated
import datetime
import uuid

from ..utils.datetime_utils import to_timestamp_ms
from ..models.models_model import ModelFeature, ModelProvider, ModelType, LoadBalanceStrategy, Modality

# Name canonicalization: write entries always trim; empty or whitespace-only names return 422.
# Request schemas only; response schemas keep plain `str`, so legacy names
# (including leading/trailing whitespace) are unaffected on the read side.
ModelName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class RejectLegacyModelFields:
    """Guard against legacy fields (capability/is_omni) and retired types (type='chat').

    With pydantic's default `extra="ignore"`, merely removing the fields would let
    legacy values be swallowed silently while users believe the submission took effect.
    Request schemas mix in this guard: explicitly submitting a legacy field or
    `type='chat'` returns 422 with a hint to migrate to contract v2.
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


def _unknown_enum_values(enum_cls, values: Optional[List[str]]) -> List[str]:
    unknown: List[str] = []
    for value in values or ():
        try:
            enum_cls(value)
        except ValueError:
            unknown.append(value)
    return unknown


class RejectUnknownModelColumns:
    """Value-domain guard for the three contract columns: write entries accept only
    contract enum members, unknown values return 422.

    `List[str]` accepts any string as-is, and unknown values are silently dropped or
    fallen back only at the profile layer after being persisted; frontend disablement
    is auxiliary at best, the server-side trust boundary must reject them.
    Response schemas do not mix in this guard (the read side tolerates legacy unknown values).
    """

    @field_validator("input_modalities", "output_modalities")
    @classmethod
    def _reject_unknown_modalities(cls, values: Optional[List[str]]) -> Optional[List[str]]:
        unknown = _unknown_enum_values(Modality, values)
        if unknown:
            raise ValueError(
                f"未知模态 {', '.join(map(str, unknown))}，"
                f"可选值：{', '.join(item.value for item in Modality)}"
            )
        return values

    @field_validator("features")
    @classmethod
    def _reject_unknown_features(cls, values: Optional[List[str]]) -> Optional[List[str]]:
        unknown = _unknown_enum_values(ModelFeature, values)
        if unknown:
            raise ValueError(
                f"未知能力特征 {', '.join(map(str, unknown))}，"
                f"可选值：{', '.join(item.value for item in ModelFeature)}"
            )
        return values


# ModelConfig Schemas
class ModelConfigBase(BaseModel):
    """Base model-config schema"""
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
    """Model-domain credential registration (provider/model_name are read from config server-side)"""
    api_key: str = Field(..., description="API密钥", max_length=500)
    api_base: Optional[str] = Field(None, description="API基础URL", max_length=500)
    remark: Optional[str] = Field(None, description="备注", max_length=255)
    priority: int = Field(0, description="优先级（大者优先）")


class ModelConfigCreate(ModelConfigBase, RejectLegacyModelFields, RejectUnknownModelColumns):
    """Custom model creation schema (embedded credential: creation registers the
    named channel, atomically in a single endpoint)

    Custom models are not added through the model marketplace and provider-level
    channels are not guaranteed usable, so the credential is required and is verified
    live at creation time; failed verification rejects creation (zero persistence).
    """
    name: ModelName = Field(..., description="模型显示名称（写入前 trim）")
    credential: ApiKeyRegister = Field(..., description="模型凭据（必填，创建时活体验证）")


class CompositeMemberSpec(BaseModel):
    """Composite member declaration; (provider, model_name) locates the member config."""
    provider: str = Field(..., min_length=1, max_length=64, description="成员模型供应商")
    model_name: str = Field(..., min_length=1, max_length=255, description="成员模型名称")


class CompositeModelCreate(BaseModel, RejectLegacyModelFields):
    """Composite model creation schema"""
    name: ModelName = Field(..., description="组合模型名称（别名，真实调用名在成员声明）")
    type: Optional[ModelType] = Field(None, description="模型类型")
    logo: Optional[str] = Field(None, description="模型logo图片URL", max_length=255)
    description: Optional[str] = Field(None, description="模型描述")
    config: Optional[Dict[str, Any]] = Field({}, description="模型配置参数")
    is_active: bool = Field(True, description="是否激活")
    is_public: bool = Field(False, description="是否公开")
    members: Optional[List[CompositeMemberSpec]] = Field(None, description="组合成员列表（(provider, model_name) 声明）")
    load_balance_strategy: Optional[str] = Field(default=LoadBalanceStrategy.NONE.value, description="负载均衡策略")


class ModelConfigUpdate(BaseModel, RejectLegacyModelFields, RejectUnknownModelColumns):
    """Model config update schema"""
    name: Optional[ModelName] = Field(None, description="模型显示名称")
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
    """Model config schema

    `is_available` = enabled and not deprecated and channel candidates non-empty
    (computed for list/detail; None = not probed in this response);
    `members` = composite member summary (declaration order; empty for non-composite).
    Credential lists go through `GET /{model_id}/apikeys`.
    """
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime.datetime
    updated_at: datetime.datetime
    is_deprecated: bool = False
    is_available: Optional[bool] = None
    members: List[CompositeMemberSpec] = []
    # Always emitted in responses (derived by the profile; see model_profile_view.wire_model_config)
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
    """Composite config JSON members[] → declaration summary (dirty entries skipped, matching runtime parse_members)."""
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
    """Credential item (masked; shared by model-domain candidates and the Provider-domain list; id = channel id)"""
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
    """Registers a public credential in the Provider domain (a provider-level [] channel
    covering every unnamed model of that provider)

    Public channels always use the provider's public base URL and do not accept
    api_base; local providers have no public endpoint and cannot be registered.
    For custom endpoints, register per model in the model domain (or edit a named channel).
    """
    provider: ModelProvider = Field(..., description="API Key提供商")
    api_base: Optional[str] = Field(
        None,
        description="不接受：公共渠道使用供应商公共基地址（传非空值将 400）",
        max_length=500,
    )


class ProviderApiKeyUpdate(BaseModel):
    """Channel attribute update (shared by Provider-domain public channels and
    model-domain named channels)

    Omitted = unchanged; explicit null for api_base/remark = clear; explicit null for
    priority/is_active = treated as omitted; non-empty api_key = re-encrypt the
    credential. model_names cannot be changed.
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
    """Model config query schema"""
    type: Optional[List[ModelType]] = Field(None, description="模型类型筛选（支持多个）")
    provider: Optional[ModelProvider] = Field(None, description="提供商筛选（按模型配置的 provider）")
    is_active: Optional[bool] = Field(None, description="激活状态筛选")
    is_public: Optional[bool] = Field(None, description="公开状态筛选")
    is_available: Optional[bool] = Field(
        None, description="可用性筛选（已启用且未弃用且渠道候选非空；服务端全量探测后过滤）"
    )
    search: Optional[str] = Field(None, description="搜索关键词", max_length=255)


# Query and response schemas
class ModelConfigQueryNew(BaseModel):
    """Model config query schema"""
    type: Optional[List[ModelType]] = Field(None, description="模型类型筛选（支持多个）")
    provider: Optional[ModelProvider] = Field(None, description="提供商筛选（按模型配置的 provider）")
    is_active: Optional[bool] = Field(None, description="激活状态筛选")
    is_public: Optional[bool] = Field(None, description="公开状态筛选")
    is_available: Optional[bool] = Field(None, description="可用性筛选（已启用且未弃用且渠道候选非空）")
    is_composite: Optional[bool] = Field(None, description="组合模型筛选")
    search: Optional[str] = Field(None, description="搜索关键词", max_length=255)


class ModelMarketplace(BaseModel):
    """Model marketplace response schema"""
    llm_models: List[ModelConfig] = []
    embedding_models: List[ModelConfig] = []
    rerank_models: List[ModelConfig] = []
    total_count: int
    active_count: int


# Model validation schemas
class ModelValidateRequest(BaseModel):
    """Model validation request"""
    model_name: str = Field(..., description="模型实际名称")
    provider: ModelProvider = Field(..., description="API Key提供商")
    api_key: str = Field(..., description="API密钥")
    api_base: Optional[str] = Field(None, description="API基础URL")
    model_type: Optional[ModelType] = Field(ModelType.LLM, description="模型类型")
    test_message: Optional[str] = Field("Hello", description="测试消息")


class ModelValidateResponse(BaseModel):
    """Model validation response"""
    valid: bool = Field(..., description="是否有效")
    message: str = Field(..., description="验证消息")
    response: Optional[str] = Field(None, description="模型响应内容")
    elapsed_time: Optional[float] = Field(None, description="响应时间（秒）")
    error: Optional[str] = Field(None, description="错误信息")
    usage: Optional[Dict[str, Any]] = Field(None, description="Token使用情况")


# Rebuild forward references
ModelConfig.model_rebuild()


# ModelBase Schemas
class ModelBaseCreate(BaseModel, RejectLegacyModelFields, RejectUnknownModelColumns):
    """Model base creation schema"""
    name: ModelName = Field(..., description="模型唯一标识")
    type: ModelType = Field(..., description="模型类型")
    provider: ModelProvider = Field(..., description="提供商")
    logo: Optional[str] = Field(None, description="模型logo图片URL", max_length=255)
    description: Optional[str] = Field(None, description="模型描述")
    is_official: bool = Field(True, description="是否供应商官方模型")
    tags: List[str] = Field(default_factory=list, description="模型标签")
    input_modalities: Optional[List[str]] = Field(None, description="输入模态（缺省按 type/provider 定基）")
    output_modalities: Optional[List[str]] = Field(None, description="输出模态（缺省按 type/provider 定基）")
    features: Optional[List[str]] = Field(None, description="能力特征（缺省为空）")


class ModelBaseUpdate(BaseModel, RejectLegacyModelFields, RejectUnknownModelColumns):
    """Model base update schema"""
    name: Optional[ModelName] = Field(None, description="模型唯一标识")
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
    """Model base schema"""
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
    # Always emitted in responses (derived by the profile; see model_profile_view.wire_model_base)
    input_modalities: List[str] = []
    output_modalities: List[str] = []
    features: List[str] = []

    @field_validator("type", mode="before")
    @classmethod
    def canonicalize_legacy_type(cls, value):
        """`type` is a plain str (not an enum); legacy strings are normalized on read: asr case-insensitive, chat → llm."""
        if isinstance(value, str):
            if value.lower() == "asr":
                return ModelType.ASR.value
            if value.lower() == "chat":
                return ModelType.LLM.value
        return value


class ModelBaseQuery(BaseModel):
    """Model base query schema"""
    type: Optional[ModelType] = Field(None, description="模型类型")
    provider: Optional[ModelProvider] = Field(None, description="提供商")
    is_official: Optional[bool] = Field(None, description="是否官方模型")
    is_deprecated: Optional[bool] = Field(None, description="是否弃用")
    search: Optional[str] = Field(None, description="搜索关键词", max_length=255)


class ModelInfo(BaseModel):
    """Model info schema (runtime shell; capabilities live in the three contract-v2 columns)"""
    model_name: str = Field(..., description="模型名称")
    provider: str = Field(..., description="模型提供商")
    api_key: Optional[str] = Field(
        None, description="API密钥；运行期非解密视图为 None（凭据不出模型服务）"
    )
    api_base: Optional[str] = Field(None, description="API基础URL；空=使用提供商默认地址")
    model_type: ModelType = Field(..., description="模型类型")
    input_modalities: List[str] = Field(default_factory=list, description="输入模态（契约 v2）")
    output_modalities: List[str] = Field(default_factory=list, description="输出模态（契约 v2）")
    features: List[str] = Field(default_factory=list, description="能力特征（契约 v2）")
    tenant_id: Optional[str] = Field(None, description="用量归属：租户ID")
    model_config_id: Optional[str] = Field(None, description="用量归属：模型配置ID")
    channel_id: Optional[str] = Field(None, description="用量归属：渠道ID")
    # Channel failover plan travels along with the instance (read by from_api_key);
    # exclude prevents serialization leakage, and a lost copy degrades to a single candidate
    failover_plan: Any = Field(default=None, exclude=True, repr=False)
