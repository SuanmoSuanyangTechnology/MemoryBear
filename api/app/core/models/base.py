from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from typing import Any, ClassVar, Dict, List, Optional, TypeVar

import httpx
from langchain_aws import ChatBedrock
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseLLM
from langchain_ollama import OllamaLLM
from langchain_openai import ChatOpenAI, OpenAI
from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

from redbear_model import FailoverPlan, legacy_capability_columns

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.model_provider_config import (
    get_default_provider_api_base,
    is_local_deployment_provider,
)
from app.models.models_model import ModelFeature, ModelProvider, ModelType
from app.core.models.compatible_chat import CompatibleChatOpenAI

T = TypeVar("T")
_OPENAI_SYNC_CLIENTS: dict[
    tuple[float | None, float | None, float | None, float | None],
    httpx.Client,
] = {}
_OPENAI_HTTP_CLIENTS_LOCK = threading.Lock()
_OPENAI_ASYNC_CLIENTS_LOCAL = threading.local()


def _get_shared_openai_clients(
        timeout_config: httpx.Timeout,
) -> tuple[httpx.Client, httpx.AsyncClient]:
    key = (
        getattr(timeout_config, "connect", None),
        getattr(timeout_config, "read", None),
        getattr(timeout_config, "write", None),
        getattr(timeout_config, "pool", None),
    )
    limits = httpx.Limits(
        max_connections=int(os.getenv("LLM_HTTP_MAX_CONNECTIONS", "300")),
        max_keepalive_connections=int(os.getenv("LLM_HTTP_MAX_KEEPALIVE", "50")),
    )

    with _OPENAI_HTTP_CLIENTS_LOCK:
        sync_client = _OPENAI_SYNC_CLIENTS.get(key)
        if sync_client is None:
            sync_client = httpx.Client(
                timeout=timeout_config, limits=limits, follow_redirects=True
            )
            _OPENAI_SYNC_CLIENTS[key] = sync_client

    async_clients = getattr(_OPENAI_ASYNC_CLIENTS_LOCAL, "clients", None)
    if async_clients is None:
        async_clients = {}
        _OPENAI_ASYNC_CLIENTS_LOCAL.clients = async_clients
    async_client = async_clients.get(key)
    if async_client is None:
        async_client = httpx.AsyncClient(
            timeout=timeout_config, limits=limits, follow_redirects=True
        )
        async_clients[key] = async_client

    return sync_client, async_client


def _shell_field(obj: Any, *names: str) -> Any:
    """从运行期 key 壳（属性对象或 dict 快照）读字段，按序取首个存在的值。"""
    for name in names:
        if isinstance(obj, Mapping):
            if name in obj:
                return obj[name]
        else:
            value = getattr(obj, name, None)
            if value is not None:
                return value
    return None


class RedBearModelConfig(BaseModel):
    """模型配置基类"""
    model_name: str
    provider: str
    api_key: str
    base_url: Optional[str] = None
    # 契约 v2 三列：能力载体（features 驱动 thinking/json_output 等开关；模态列随行供多模态判定）
    input_modalities: List[str] = Field(default_factory=list)
    output_modalities: List[str] = Field(default_factory=list)
    features: List[str] = Field(default_factory=list)
    deep_thinking: bool = False  # 是否启用深度思考模式
    thinking_budget_tokens: Optional[int] = None  # 深度思考 token 预算
    json_output: bool = False  # 是否强制 JSON 输出
    # 请求超时时间（秒）- 默认120秒以支持复杂的LLM调用，可通过环境变量 LLM_TIMEOUT 配置
    timeout: float = Field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT", "120.0")))
    # 最大重试次数 - 默认2次以避免过长等待，可通过环境变量 LLM_MAX_RETRIES 配置
    max_retries: int = Field(default_factory=lambda: int(os.getenv("LLM_MAX_RETRIES", "2")))
    concurrency: int = 5  # 并发限流
    extra_params: Dict[str, Any] = {}
    # 用量事件归属（spec §13.2）：中央构建器从解析结果填充，业务调用点不感知；
    # tenant/model_config 缺失时用量事件跳过，channel 缺失则记 NULL
    tenant_id: Optional[str] = None
    model_config_id: Optional[str] = None
    channel_id: Optional[str] = None

    # 请求内换渠道计划（spec §11.2）：私有属性，不进 model_fields/model_dump/repr；
    # 由 from_api_key 从运行期 key 壳透传（壳缺键 → None，兼容旧壳）
    _failover_plan: FailoverPlan | None = PrivateAttr(default=None)

    @property
    def failover_plan(self) -> FailoverPlan | None:
        return self._failover_plan

    def bind_failover_plan(self, plan: FailoverPlan | None) -> "RedBearModelConfig":
        """直构路径（LangChainAgent 等）挂载请求内换渠道计划，语义同 from_api_key 壳透传。"""
        self._failover_plan = plan
        return self

    @field_validator("tenant_id", "model_config_id", "channel_id", mode="before")
    @classmethod
    def _coerce_attribution_id(cls, value: Any) -> Any:
        if value is None or isinstance(value, str):
            return value
        return str(value)

    EXTRA_PARAMS_FIELD_MAP: ClassVar[dict] = {
        "deep_thinking": "deep_thinking",
        "thinking_budget_tokens": "thinking_budget_tokens",
        "json_output": "json_output",
        "streaming": None,
        "enable_search": None,
        "enable_thinking": None,
        "response_format": None,
    }

    @model_validator(mode="before")
    @classmethod
    def _lift_config_from_extra_params(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        extra_params = data.get("extra_params", {})
        if not extra_params:
            return data
        for param_key, field_name in cls.EXTRA_PARAMS_FIELD_MAP.items():
            if param_key in extra_params and field_name is not None:
                if field_name not in data:
                    data[field_name] = extra_params[param_key]
        return data

    @model_validator(mode="after")
    def _resolve_default_base_url(self) -> "RedBearModelConfig":
        """补齐云端默认地址，并拒绝本地提供商的空地址配置。"""
        if isinstance(self.base_url, str):
            self.base_url = self.base_url.strip()

        if not self.base_url:
            if is_local_deployment_provider(self.provider):
                raise ValueError(
                    f"本地部署提供商 {self.provider} 必须配置 API Base URL"
                )
            self.base_url = get_default_provider_api_base(self.provider)
        return self

    @model_validator(mode="after")
    def _resolve_features(self) -> "RedBearModelConfig":
        from app.core.logging_config import get_business_logger
        logger = get_business_logger()

        has_thinking = ModelFeature.THINKING in self.features
        has_thinking_only = ModelFeature.THINKING_ONLY in self.features
        supports_json_output = ModelFeature.JSON_OUTPUT in self.features

        if self.deep_thinking and not has_thinking and not has_thinking_only:
            logger.warning(
                f"模型 {self.model_name} 不支持深度思考（features 中无 'thinking'/'thinking_only'），已自动关闭 deep_thinking"
            )
            self.deep_thinking = False
            self.thinking_budget_tokens = None

        if not self.deep_thinking and self.thinking_budget_tokens is not None:
            logger.warning(
                f"模型 {self.model_name} 未启用深度思考，已自动清除 thinking_budget_tokens"
            )
            self.thinking_budget_tokens = None

        # thinking_only 模型始终处于思考状态，deep_thinking 标志强制为 True
        if has_thinking_only:
            self.deep_thinking = True
            # thinking_only 模型不支持 thinking_budget_tokens 参数，清除以防止误传
            self.thinking_budget_tokens = None

        # thinking_only 模型不支持 json_output，两者冲突会导致模型输出异常（如输出 "[1]"）
        if self.json_output and has_thinking_only:
            logger.warning(
                f"模型 {self.model_name} 为 thinking_only 类型，不支持 json_output，已自动关闭 json_output"
            )
            self.json_output = False

        if self.json_output and not supports_json_output:
            logger.warning(
                f"模型 {self.model_name} 不支持 JSON 输出（features 中无 'json_output'），已自动关闭 json_output"
            )
            self.json_output = False
        return self

    @classmethod
    def from_api_key(cls, api_key_obj: Any, **overrides: Any) -> "RedBearModelConfig":
        """运行期 key 壳 → 模型配置（spec §13.2：用量归属三字段随行）。

        壳可为解析壳/speedbear/legacy 的运行期 ModelApiKey、ModelInfo、snapshot
        或 dict 快照；`overrides` 为调用点特有参数（timeout/max_retries/
        extra_params 等），同名覆盖。tenant/model_config/channel 缺失即 None
        （用量事件侧跳过/NULL，语义与中央构建器一致）；failover_plan 同随行
        （旧壳/拷贝丢失 → None，退化为单候选）。

        能力取契约 v2 三列；旧壳（无三列的 dict 快照 / Celery 存量消息）按旧列
        `capability`/`is_omni` 换算兜底（deprecated，M10 删）。
        """
        raw_input = _shell_field(api_key_obj, "input_modalities")
        if raw_input is None:
            legacy_input, legacy_output, legacy_features = legacy_capability_columns(
                type=ModelType.LLM.value,
                provider=str(_shell_field(api_key_obj, "provider") or ""),
                capabilities=tuple(_shell_field(api_key_obj, "capability") or ()),
                is_omni=bool(_shell_field(api_key_obj, "is_omni")),
            )
            raw_input, raw_output, raw_features = legacy_input, legacy_output, legacy_features
        else:
            raw_output = _shell_field(api_key_obj, "output_modalities") or ()
            raw_features = _shell_field(api_key_obj, "features") or ()
        data: Dict[str, Any] = {
            "model_name": _shell_field(api_key_obj, "model_name"),
            "provider": _shell_field(api_key_obj, "provider"),
            "api_key": _shell_field(api_key_obj, "api_key"),
            "base_url": _shell_field(api_key_obj, "api_base", "base_url") or None,
            "input_modalities": [str(item) for item in raw_input],
            "output_modalities": [str(item) for item in raw_output],
            "features": [str(item) for item in raw_features],
            "tenant_id": _shell_field(api_key_obj, "tenant_id"),
            "model_config_id": _shell_field(api_key_obj, "model_config_id"),
            "channel_id": _shell_field(api_key_obj, "channel_id"),
        }
        missing = [
            name for name in ("model_name", "provider", "api_key") if data[name] is None
        ]
        if missing:
            raise ValueError(f"from_api_key: 壳缺少必需字段 {', '.join(missing)}")
        data["provider"] = str(data["provider"])
        data.update(overrides)
        config = cls(**data)
        config._failover_plan = _shell_field(api_key_obj, "failover_plan")
        return config


def _map_budget_to_reasoning_effort(budget_tokens: Optional[int]) -> Optional[str]:
    if budget_tokens is None:
        return None
    if budget_tokens <= 2048:
        return "low"
    elif budget_tokens <= 4096:
        return "medium"
    else:
        return "high"


def _json_response_format(config: RedBearModelConfig) -> dict[str, Any]:
    response_format = config.extra_params.get("response_format")
    if isinstance(response_format, dict):
        return response_format
    return {"type": "json_object"}


def _should_send_response_format(config: RedBearModelConfig) -> bool:
    return config.json_output or isinstance(config.extra_params.get("response_format"), dict)


class RedBearModelFactory:
    """模型工厂类"""

    _CONFIG_ONLY_KEYS = {
        "deep_thinking", "thinking_budget_tokens",
        "enable_search", "enable_thinking", "response_format", "json_output",
        "default_headers",
    }

    @staticmethod
    def _extract_provider_specific_params(extra_params: Dict[str, Any]) -> tuple[Dict[str, Any], dict[str, Any]]:
        """从 extra_params 中分离提供商特有参数和 RedBearModelConfig 专有字段，
        返回 (过滤后的 extra_params, provider_specific dict)

        provider_specific 包含需要按提供商路由的参数：
        - top_k: 仅 Ollama 支持顶层；DashScope 兼容模式经 extra_body 透传
        - repetition_penalty: 仅 Ollama/DashScope 支持，DashScope 兼容模式经 extra_body
        - seed: 仅部分提供商支持
        - enable_search: 仅 DashScope 支持，兼容模式经 extra_body 透传
        - stop: 仅 OpenAI 兼容提供商支持顶级传递
        - temperature: OpenAI 兼容提供商顶级传递
        - max_tokens: OpenAI 兼容提供商顶级传递

        config_only_keys 中的字段是 RedBearModelConfig 配置字段，
        不应该被展开到最终 LLM 类的构造参数中。
        """
        provider_specific_keys = ("top_k", "repetition_penalty", "seed", "enable_search", "stop", "temperature", "max_tokens")
        config_only_keys = RedBearModelFactory._CONFIG_ONLY_KEYS
        provider_specific = {}
        for key in provider_specific_keys:
            if key in extra_params:
                provider_specific[key] = extra_params[key]
        filtered = {k: v for k, v in extra_params.items() if k not in config_only_keys and k not in provider_specific_keys}
        return filtered, provider_specific

    @classmethod
    def get_model_params(cls, config: RedBearModelConfig) -> Dict[str, Any]:
        """根据提供商获取模型参数"""
        provider = config.provider.lower()

        # 打印供应商信息用于调试
        from app.core.logging_config import get_business_logger
        logger = get_business_logger()
        logger.debug(
            f"获取模型参数 - Provider: {provider}, Model: {config.model_name}, "
            f"features: {config.features}, deep_thinking: {config.deep_thinking}"
        )

        filtered_extra_params, provider_specific = cls._extract_provider_specific_params(config.extra_params)
        default_headers = config.extra_params.get("default_headers")
        if default_headers:
            logger.info(f"额外请求头已注入: {default_headers}")

        # dashscope 全量模型使用 OpenAI 兼容模式（Task 10：ChatTongyi 原生协议退役）
        if provider == ModelProvider.DASHSCOPE:
            if not config.base_url:
                config.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            # 连接超时跟随调用预算（上限 60s）：短预算场景（如配置验证 timeout=10）
            # 不应在连接阶段独占 60s 才失败
            timeout_config = httpx.Timeout(
                timeout=config.timeout,
                connect=min(config.timeout, 60.0),
                read=config.timeout,
                write=60.0,
                pool=10.0,
            )
            http_client, http_async_client = _get_shared_openai_clients(timeout_config)
            params: Dict[str, Any] = {
                "model": config.model_name,
                "base_url": config.base_url,
                "api_key": config.api_key,
                "timeout": timeout_config,
                "max_retries": config.max_retries,
                "http_client": http_client,
                "http_async_client": http_async_client,
                **filtered_extra_params
            }
            if default_headers:
                params["default_headers"] = default_headers
            # 流式模式下启用 stream_usage 以获取 token 统计
            is_streaming = bool(config.extra_params.get("streaming"))
            if is_streaming:
                params["stream_usage"] = True
            # thinking 参数处理：
            # - thinking_only（B类）：不能传 enable_thinking，不做任何处理
            # - thinking（A类）：混合思考，流式和非流式均可开关，非流式也支持 thinking_budget
            if ModelFeature.THINKING in config.features:
                extra_body = params.setdefault("extra_body", {})
                if config.deep_thinking:
                    extra_body["enable_thinking"] = True
                    if config.thinking_budget_tokens:
                        extra_body["thinking_budget"] = config.thinking_budget_tokens
                else:
                    extra_body["enable_thinking"] = False
            # DashScope OpenAI 兼容 API：标准参数（temperature/max_tokens/seed/stop）
            # 走顶层；DashScope 扩展参数（repetition_penalty/top_k/enable_search）经 extra_body
            for key in ("temperature", "max_tokens", "seed", "stop",
                        "repetition_penalty", "top_k", "enable_search"):
                if key in provider_specific and provider_specific[key] is not None:
                    if key in ("repetition_penalty", "top_k", "enable_search"):
                        extra_body = params.setdefault("extra_body", {})
                        extra_body[key] = provider_specific[key]
                    else:
                        params[key] = provider_specific[key]
            # JSON 输出模式
            # thinking（A类）模型启用深度思考时，response_format 与思考模式 API 冲突，跳过由调用方 prompt 注入兜底
            if _should_send_response_format(config):
                if not (ModelFeature.THINKING in config.features and config.deep_thinking):
                    model_kwargs = params.setdefault("model_kwargs", {})
                    model_kwargs["response_format"] = _json_response_format(config)
            return params

        if provider in [
            ModelProvider.OPENAI,
            ModelProvider.XINFERENCE,
            ModelProvider.GPUSTACK,
            ModelProvider.MINIMAX,
            ModelProvider.OLLAMA,
            ModelProvider.VOLCANO,
            ModelProvider.SPEEDBEAR,
        ]:
            # 使用 httpx.Timeout 对象来设置详细的超时配置
            # 这样可以分别控制连接超时和读取超时
            timeout_config = httpx.Timeout(
                timeout=config.timeout,  # 总超时时间
                connect=min(config.timeout, 60.0),  # 连接超时跟随调用预算（上限 60 秒）
                read=config.timeout,  # 读取超时：使用配置的超时时间
                write=60.0,  # 写入超时：60秒
                pool=10.0,  # 连接池超时：10秒
            )
            http_client, http_async_client = _get_shared_openai_clients(timeout_config)
            # OllamaLLM 有 top_k 原生字段，可直接传入顶层；
            # ChatOpenAI/CompatibleChatOpenAI 不支持 top_k，OpenAI API 也无此参数，不能放入 model_kwargs
            # 否则会透传到 AsyncCompletions.create() 导致 unexpected keyword argument 错误
            if provider == ModelProvider.OLLAMA:
                if "top_k" in provider_specific and provider_specific["top_k"] is not None:
                    filtered_extra_params["top_k"] = provider_specific["top_k"]
                if "repetition_penalty" in provider_specific and provider_specific["repetition_penalty"] is not None:
                    filtered_extra_params["repetition_penalty"] = provider_specific["repetition_penalty"]

            params: Dict[str, Any] = {
                "model": config.model_name,
                "base_url": config.base_url,
                "api_key": config.api_key,
                "timeout": timeout_config,
                "max_retries": config.max_retries,
                "http_client": http_client,
                "http_async_client": http_async_client,
                **filtered_extra_params
            }

            # OpenAI-compatible providers: temperature, max_tokens, seed, stop
            # are top-level params. Other provider-specific params (top_k,
            # repetition_penalty) are already handled above for Ollama.
            for key in ("temperature", "max_tokens", "seed", "stop"):
                if key in provider_specific and provider_specific[key] is not None:
                    params[key] = provider_specific[key]

            if default_headers and provider != ModelProvider.OLLAMA:
                params["default_headers"] = default_headers

            is_streaming = bool(config.extra_params.get("streaming"))
            if is_streaming:
                params["stream_usage"] = True
            # thinking 参数处理：
            # - thinking_only（B类）：不能传 enable_thinking，不做任何处理
            # - thinking（A类）：混合思考，流式和非流式均可开关
            if ModelFeature.THINKING in config.features:
                if provider == ModelProvider.VOLCANO:
                    extra_body = params.setdefault("extra_body", {})
                    if config.deep_thinking:
                        extra_body["thinking"] = {"type": "enabled"}
                        effort = _map_budget_to_reasoning_effort(config.thinking_budget_tokens)
                        if effort is not None:
                            params["reasoning_effort"] = effort
                    else:
                        extra_body["thinking"] = {"type": "disabled"}
                elif provider == ModelProvider.SPEEDBEAR:
                    if config.deep_thinking:
                        params["reasoning_effort"] = "minimal"
                        effort = _map_budget_to_reasoning_effort(config.thinking_budget_tokens)
                        if effort is not None:
                            params["reasoning_effort"] = effort
                    else:
                        params["reasoning_effort"] = "none"
                else:
                    extra_body = params.setdefault("extra_body", {})
                    if config.deep_thinking:
                        extra_body["enable_thinking"] = True
                        if config.thinking_budget_tokens:
                            extra_body["thinking_budget"] = config.thinking_budget_tokens
                    else:
                        extra_body["enable_thinking"] = False
            # JSON 输出模式
            if _should_send_response_format(config):
                model_kwargs = params.setdefault("model_kwargs", {})
                # thinking（A类）模型启用深度思考时,response_format 与思考模式 API 冲突，跳过由调用方 prompt 注入兜底
                if not (
                    ModelFeature.THINKING in config.features and config.deep_thinking
                ):
                    model_kwargs["response_format"] = _json_response_format(config)
            return params
        elif provider == ModelProvider.BEDROCK:
            # Bedrock 使用 AWS 凭证
            # api_key 格式: "access_key_id:secret_access_key" 或只是 access_key_id
            # region 从 base_url 或 extra_params 获取
            from botocore.config import Config as BotoConfig
            from app.core.models.bedrock_model_mapper import normalize_bedrock_model_id

            max_pool_connections = int(os.getenv("BEDROCK_MAX_POOL_CONNECTIONS", "50"))
            max_retries = int(os.getenv("BEDROCK_MAX_RETRIES", "2"))
            # Configure with increased connection pool
            boto_config = BotoConfig(
                max_pool_connections=max_pool_connections,
                retries={'max_attempts': max_retries, 'mode': 'adaptive'}
            )

            # 标准化模型 ID（自动转换简化名称为完整 Bedrock Model ID）
            model_id = normalize_bedrock_model_id(config.model_name)

            params = {
                "model_id": model_id,
                "config": boto_config,
                **filtered_extra_params
            }
            model_kwargs = params.setdefault("model_kwargs", {})
            # Bedrock 专用参数路由：
            # top_k, seed → model_kwargs 保持原名
            # stop → model_kwargs 映射为 stop_sequences
            # temperature, max_tokens → ChatBedrock 有顶级字段，直接传递
            for key in ("top_k", "seed"):
                if key in provider_specific and provider_specific[key] is not None:
                    model_kwargs[key] = provider_specific[key]
            if "stop" in provider_specific and provider_specific["stop"] is not None:
                model_kwargs["stop_sequences"] = provider_specific["stop"]
            for key in ("temperature", "max_tokens"):
                if key in provider_specific and provider_specific[key] is not None:
                    params[key] = provider_specific[key]

            # 解析 API key (格式: access_key_id:secret_access_key)
            if config.api_key:
                access_key_id, _, secret_access_key = config.api_key.partition(":")
                access_key_id = access_key_id.strip()
                secret_access_key = secret_access_key.strip()
                if not access_key_id or not secret_access_key:
                    raise BusinessException(
                        "Bedrock 凭据格式错误：API Key 应为 "
                        "access_key_id:secret_access_key（英文半角冒号分隔），"
                        "请检查是否只填了 Access Key ID、漏填了 secret，或误用了中文冒号",
                        BizCode.INVALID_PARAMETER,
                    )
                params["aws_access_key_id"] = access_key_id
                params["aws_secret_access_key"] = secret_access_key

            # 设置 region
            if config.base_url:
                params["region_name"] = config.base_url
            elif "region_name" not in params:
                params["region_name"] = "us-east-1"  # 默认区域

            # 深度思考模式：Claude 3.7 Sonnet 等支持思考的模型
            # 通过 additional_model_request_fields 传递 thinking 块，关闭时不传（Bedrock 无 disabled 选项）
            if config.deep_thinking:
                budget = config.thinking_budget_tokens or 1024
                params["additional_model_request_fields"] = {
                    "thinking": {"type": "enabled", "budget_tokens": budget}
                }
            # JSON 输出模式
            # thinking（A类）模型启用深度思考时，response_format 与思考模式 API 冲突，跳过由调用方 prompt 注入兜底
            if _should_send_response_format(config):
                if not (ModelFeature.THINKING in config.features and config.deep_thinking):
                    model_kwargs = params.setdefault("model_kwargs", {})
                    model_kwargs["response_format"] = _json_response_format(config)
            return params
        else:
            raise BusinessException(f"不支持的提供商: {provider}", code=BizCode.PROVIDER_NOT_SUPPORTED)

    @classmethod
    def get_rerank_model_params(cls, config: RedBearModelConfig) -> Dict[str, Any]:
        """根据提供商获取模型参数"""
        provider = config.provider.lower()
        if provider in [ModelProvider.XINFERENCE, ModelProvider.GPUSTACK, ModelProvider.SPEEDBEAR]:
            return {
                "model": config.model_name,
                "jina_api_key": config.api_key,
                **config.extra_params
            }
        elif provider == ModelProvider.DASHSCOPE:
            return {
                "model": config.model_name,
                "dashscope_api_key": config.api_key,
                **config.extra_params
            }
        else:
            raise BusinessException(f"不支持的提供商: {provider}", code=BizCode.PROVIDER_NOT_SUPPORTED)


def get_provider_llm_class(config: RedBearModelConfig, type: ModelType = ModelType.LLM) -> type[BaseLLM]:
    """根据模型提供商获取对应的模型类"""
    provider = config.provider.lower()

    # dashscope 全量模型与 volcano 模型使用 OpenAI 兼容协议（ChatTongyi 已退役）
    if provider in [
        ModelProvider.DASHSCOPE,
        ModelProvider.VOLCANO,
        ModelProvider.OPENAI,
        ModelProvider.XINFERENCE,
        ModelProvider.GPUSTACK,
        ModelProvider.MINIMAX,
        ModelProvider.SPEEDBEAR,
    ]:
        return CompatibleChatOpenAI
    elif provider == ModelProvider.OLLAMA:
        return OllamaLLM
    elif provider == ModelProvider.BEDROCK:
        return ChatBedrock
    else:
        raise BusinessException(f"不支持的模型提供商: {provider}", code=BizCode.PROVIDER_NOT_SUPPORTED)


def get_provider_embedding_class(provider: str) -> type[Embeddings]:
    """根据模型提供商获取对应的模型类"""
    provider = provider.lower()
    if provider in [
        ModelProvider.OPENAI,
        ModelProvider.XINFERENCE,
        ModelProvider.GPUSTACK,
        ModelProvider.SPEEDBEAR,
    ]:
        from langchain_openai import OpenAIEmbeddings
        return OpenAIEmbeddings
    elif provider == ModelProvider.DASHSCOPE:
        from langchain_community.embeddings import DashScopeEmbeddings
        return DashScopeEmbeddings
    elif provider == ModelProvider.OLLAMA:
        from langchain_ollama import OllamaEmbeddings
        return OllamaEmbeddings
    elif provider == ModelProvider.BEDROCK:
        from langchain_aws import BedrockEmbeddings
        return BedrockEmbeddings
    else:
        raise BusinessException(f"不支持的模型提供商: {provider}", code=BizCode.PROVIDER_NOT_SUPPORTED)


def get_provider_rerank_class(provider: str):
    """根据模型提供商获取对应的模型类"""
    provider = provider.lower()
    if provider in [ModelProvider.XINFERENCE, ModelProvider.GPUSTACK, ModelProvider.SPEEDBEAR]:
        from langchain_community.document_compressors import JinaRerank
        return JinaRerank
    elif provider == ModelProvider.DASHSCOPE:
        from langchain_community.document_compressors.dashscope_rerank import DashScopeRerank
        return DashScopeRerank
        # elif provider == ModelProvider.OLLAMA:
    #     from langchain_ollama import OllamaEmbeddings
    #     return OllamaEmbeddings
    else:
        raise BusinessException(f"不支持的模型提供商: {provider}", code=BizCode.PROVIDER_NOT_SUPPORTED)
