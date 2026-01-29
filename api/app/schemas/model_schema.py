from pydantic import BaseModel, Field, field_serializer, field_validator, ConfigDict
from typing import Optional, List, Dict, Any
import datetime
import uuid

from app.models.models_model import ModelProvider, ModelType, LoadBalanceStrategy
from app.core.logging_config import get_business_logger

schema_logger = get_business_logger()


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


class ApiKeyCreateNested(BaseModel):
    """用于在创建模型时内嵌创建API Key的Schema"""
    model_name: str = Field(..., description="模型实际名称", max_length=255)
    description: Optional[str] = Field(None, description="备注")
    provider: ModelProvider = Field(..., description="API Key提供商")
    api_key: str = Field(..., description="API密钥", max_length=500)
    api_base: Optional[str] = Field(None, description="API基础URL", max_length=500)
    config: Optional[Dict[str, Any]] = Field({}, description="API Key特定配置")
    priority: str = Field("1", description="优先级", max_length=10)


class ModelConfigCreate(ModelConfigBase):
    """创建模型配置Schema"""
    api_keys: Optional[List[ApiKeyCreateNested]] = Field(None, description="同时创建的API Key配置")
    skip_validation: Optional[bool] = Field(False, description="是否跳过配置验证")


class CompositeModelCreate(BaseModel):
    """创建组合模型Schema"""
    name: str = Field(..., description="组合模型名称", max_length=255)
    type: ModelType = Field(..., description="模型类型")
    logo: Optional[str] = Field(None, description="模型logo图片URL", max_length=255)
    description: Optional[str] = Field(None, description="模型描述")
    config: Optional[Dict[str, Any]] = Field({}, description="模型配置参数")
    is_active: bool = Field(True, description="是否激活")
    is_public: bool = Field(False, description="是否公开")
    api_key_ids: List[uuid.UUID] = Field(..., description="绑定的API Key ID列表")
    load_balance_strategy: Optional[str] = Field(default=LoadBalanceStrategy.NONE.value, description="负载均衡策略")


class ModelConfigUpdate(BaseModel):
    """更新模型配置Schema"""
    name: Optional[str] = Field(None, description="模型显示名称", max_length=255)
    type: Optional[ModelType] = Field(None, description="模型类型")
    description: Optional[str] = Field(None, description="模型描述")
    config: Optional[Dict[str, Any]] = Field(None, description="模型配置参数")
    is_active: Optional[bool] = Field(None, description="是否激活")
    is_public: Optional[bool] = Field(None, description="是否公开")


class ModelConfig(ModelConfigBase):
    """模型配置Schema"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime.datetime
    updated_at: datetime.datetime
    api_keys: List["ModelApiKey"] = []

    @field_validator("api_keys", mode="after")
    @classmethod
    def filter_active_api_keys(cls, api_keys: List["ModelApiKey"]) -> List["ModelApiKey"]:
        return [key for key in api_keys if key.is_active]

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime | None):
        return int(dt.timestamp() * 1000) if dt else None

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None


# ModelApiKey Schemas
class ModelApiKeyCreateByProvider(BaseModel):
    """基于供应商创建API Key Schema"""
    provider: ModelProvider = Field(..., description="API Key提供商")
    api_key: str = Field(..., description="API密钥", max_length=500)
    api_base: Optional[str] = Field(None, description="API基础URL", max_length=500)
    description: Optional[str] = Field(None, description="备注")
    config: Optional[Dict[str, Any]] = Field({}, description="API Key特定配置")
    is_active: bool = Field(True, description="是否激活")
    priority: str = Field("1", description="优先级", max_length=10)
    model_config_ids: Optional[List[uuid.UUID]] = Field(None, description="关联的模型配置ID列表")


class ModelApiKeyBase(BaseModel):
    """API Key基础Schema"""
    model_name: str = Field(..., description="模型实际名称", max_length=255)
    description: Optional[str] = Field(None, description="备注")
    provider: ModelProvider = Field(..., description="API Key提供商")
    api_key: str = Field(..., description="API密钥", max_length=500)
    api_base: Optional[str] = Field(None, description="API基础URL", max_length=500)
    config: Optional[Dict[str, Any]] = Field({}, description="API Key特定配置")
    is_active: bool = Field(True, description="是否激活")
    priority: str = Field("1", description="优先级", max_length=10)


class ModelApiKeyCreate(ModelApiKeyBase):
    """创建API Key Schema"""
    model_config_ids: Optional[List[uuid.UUID]] = Field(None, description="关联的模型配置ID列表")


class ModelApiKeyUpdate(BaseModel):
    """更新API Key Schema"""
    model_name: Optional[str] = Field(None, description="模型实际名称", max_length=255)
    provider: Optional[ModelProvider] = Field(None, description="API Key提供商")
    api_key: Optional[str] = Field(None, description="API密钥", max_length=500)
    api_base: Optional[str] = Field(None, description="API基础URL", max_length=500)
    config: Optional[Dict[str, Any]] = Field(None, description="API Key特定配置")
    is_active: Optional[bool] = Field(None, description="是否激活")
    priority: Optional[str] = Field(None, description="优先级", max_length=10)


class ModelApiKey(ModelApiKeyBase):
    """API Key Schema"""
    id: uuid.UUID
    usage_count: str
    last_used_at: Optional[datetime.datetime]
    created_at: datetime.datetime
    updated_at: datetime.datetime
    model_configs: Any = Field(default=None, exclude=True)
    model_config_ids: List[uuid.UUID] = Field(default_factory=list, description="关联的模型配置ID列表")

    def model_post_init(self, __context: Any) -> None:
        """实例化后强制提取 model_configs 的ID到 model_config_ids"""
        # 如果手动传入了 model_config_ids，不覆盖
        if self.model_config_ids and len(self.model_config_ids) > 0:
            return

        # 从 model_configs 提取ID（只提取与 model_name 相同的非组合模型）
        if self.model_configs is not None:
            try:
                # 情况1：ORM 对象列表（SQLAlchemy 关联）
                if hasattr(self.model_configs, '__iter__') and not isinstance(self.model_configs, dict):
                    self.model_config_ids = [
                        mc.id for mc in self.model_configs
                        if hasattr(mc, 'id') 
                        and not getattr(mc, 'is_composite', False)
                        and getattr(mc, 'name', None) == self.model_name
                    ]
                # 情况2：字典列表
                elif isinstance(self.model_configs, list):
                    self.model_config_ids = [
                        mc['id'] if isinstance(mc, dict) else mc.id
                        for mc in self.model_configs
                        if ((isinstance(mc, dict) 
                             and 'id' in mc 
                             and not mc.get('is_composite', False)
                             and mc.get('name') == self.model_name) or 
                            (hasattr(mc, 'id') 
                             and not getattr(mc, 'is_composite', False)
                             and getattr(mc, 'name', None) == self.model_name))
                    ]
            except Exception as e:
                schema_logger.warning(f"提取 model_config_ids 失败：{e}")
                self.model_config_ids = []

    model_config = ConfigDict(
        from_attributes=True,  # 支持从 ORM 解析
        arbitrary_types_allowed=True,  # 允许任意类型（ORM 对象）
        populate_by_name=True,  # 按属性名匹配字段
        validate_assignment=True  # 确保赋值触发校验
    )


    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None
    
    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None

    @field_serializer("last_used_at", when_used="json")
    def _serialize_last_used_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None


class ModelConfigQuery(BaseModel):
    """模型配置查询Schema"""
    type: Optional[List[ModelType]] = Field(None, description="模型类型筛选（支持多个）")
    provider: Optional[ModelProvider] = Field(None, description="提供商筛选(通过API Key)")
    is_active: Optional[bool] = Field(None, description="激活状态筛选")
    is_public: Optional[bool] = Field(None, description="公开状态筛选")
    search: Optional[str] = Field(None, description="搜索关键词", max_length=255)
    page: int = Field(1, description="页码", ge=1)
    pagesize: int = Field(10, description="每页数量", ge=1, le=100)


# 查询和响应Schemas
class ModelConfigQueryNew(BaseModel):
    """模型配置查询Schema"""
    type: Optional[List[ModelType]] = Field(None, description="模型类型筛选（支持多个）")
    provider: Optional[ModelProvider] = Field(None, description="提供商筛选(通过API Key)")
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


# 统计信息Schema
class ModelStats(BaseModel):
    """模型统计信息Schema"""
    total_models: int
    active_models: int
    llm_count: int
    embedding_count: int
    rerank_count: int
    provider_stats: Dict[str, int]


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
class ModelBaseCreate(BaseModel):
    """创建基础模型Schema"""
    name: str = Field(..., description="模型唯一标识", max_length=255)
    type: ModelType = Field(..., description="模型类型")
    provider: ModelProvider = Field(..., description="提供商")
    logo: Optional[str] = Field(None, description="模型logo图片URL", max_length=255)
    description: Optional[str] = Field(None, description="模型描述")
    is_official: bool = Field(True, description="是否供应商官方模型")
    tags: List[str] = Field(default_factory=list, description="模型标签")


class ModelBaseUpdate(BaseModel):
    """更新基础模型Schema"""
    name: Optional[str] = Field(None, description="模型唯一标识", max_length=255)
    type: Optional[ModelType] = Field(None, description="模型类型")
    provider: Optional[ModelProvider] = Field(None, description="提供商")
    logo: Optional[str] = Field(None, description="模型logo图片URL", max_length=255)
    description: Optional[str] = Field(None, description="模型描述")
    is_deprecated: Optional[bool] = Field(None, description="是否弃用")
    is_official: Optional[bool] = Field(None, description="是否供应商官方模型")
    tags: Optional[List[str]] = Field(None, description="模型标签")


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


class ModelBaseQuery(BaseModel):
    """基础模型查询Schema"""
    type: Optional[ModelType] = Field(None, description="模型类型")
    provider: Optional[ModelProvider] = Field(None, description="提供商")
    is_official: Optional[bool] = Field(None, description="是否官方模型")
    is_deprecated: Optional[bool] = Field(None, description="是否弃用")
    search: Optional[str] = Field(None, description="搜索关键词", max_length=255)
