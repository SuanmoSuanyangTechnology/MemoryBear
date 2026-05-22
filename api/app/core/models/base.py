from __future__ import annotations

import os
from typing import Any, ClassVar, Dict, List, Optional, TypeVar

from langchain_aws import ChatBedrock
from langchain_community.chat_models import ChatTongyi
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseLLM
from langchain_ollama import OllamaLLM
from langchain_openai import ChatOpenAI, OpenAI
from pydantic import BaseModel, Field, model_validator

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.models.models_model import ModelProvider, ModelType, ModelCapability
from app.core.models.compatible_chat import CompatibleChatOpenAI

T = TypeVar("T")


class RedBearModelConfig(BaseModel):
    """模型配置基类"""
    model_name: str
    provider: str
    api_key: str
    base_url: Optional[str] = None
    capability: List[str] = Field(default_factory=list)  # 模型能力列表，驱动所有能力开关
    is_omni: bool = False  # 是否为 Omni 模型
    deep_thinking: bool = False  # 是否启用深度思考模式
    thinking_budget_tokens: Optional[int] = None  # 深度思考 token 预算
    json_output: bool = False  # 是否强制 JSON 输出
    # 请求超时时间（秒）- 默认120秒以支持复杂的LLM调用，可通过环境变量 LLM_TIMEOUT 配置
    timeout: float = Field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT", "120.0")))
    # 最大重试次数 - 默认2次以避免过长等待，可通过环境变量 LLM_MAX_RETRIES 配置
    max_retries: int = Field(default_factory=lambda: int(os.getenv("LLM_MAX_RETRIES", "2")))
    concurrency: int = 5  # 并发限流
    extra_params: Dict[str, Any] = {}

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
    def _resolve_capabilities(self) -> "RedBearModelConfig":
        from app.core.logging_config import get_business_logger
        logger = get_business_logger()

        has_thinking = ModelCapability.THINKING in self.capability
        has_thinking_only = ModelCapability.THINKING_ONLY in self.capability

        if self.deep_thinking and not has_thinking and not has_thinking_only:
            logger.warning(
                f"模型 {self.model_name} 不支持深度思考（capability 中无 'thinking'/'thinking_only'），已自动关闭 deep_thinking"
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

        if self.json_output and ModelCapability.JSON_OUTPUT not in self.capability:
            logger.warning(
                f"模型 {self.model_name} 不支持 JSON 输出（capability 中无 'json_output'），已自动关闭 json_output"
            )
            self.json_output = False
        return self


def _map_budget_to_reasoning_effort(budget_tokens: Optional[int]) -> Optional[str]:
    if budget_tokens is None:
        return None
    if budget_tokens <= 2048:
        return "low"
    elif budget_tokens <= 4096:
        return "medium"
    else:
        return "high"


class RedBearModelFactory:
    """模型工厂类"""

    _CONFIG_ONLY_KEYS = {
        "deep_thinking", "thinking_budget_tokens", "streaming",
        "enable_search", "enable_thinking", "response_format", "json_output",
        "default_headers",
    }

    @staticmethod
    def _extract_top_k_from_extra_params(extra_params: Dict[str, Any]) -> tuple[Dict[str, Any], Optional[int]]:
        """从 extra_params 中分离 top_k 和 RedBearModelConfig 专有字段，返回 (过滤后的 extra_params, top_k 值)

        deep_thinking, thinking_budget_tokens, streaming 等字段是 RedBearModelConfig 的配置字段，
        不应该被展开到最终 LLM 类的构造参数中，否则 OpenAI SDK 等会报 unknown keyword argument。
        """
        top_k_value = extra_params.get("top_k")
        config_only_keys = RedBearModelFactory._CONFIG_ONLY_KEYS
        filtered = {k: v for k, v in extra_params.items() if k not in config_only_keys and k != "top_k"}
        return filtered, top_k_value

    @classmethod
    def get_model_params(cls, config: RedBearModelConfig) -> Dict[str, Any]:
        """根据提供商获取模型参数"""
        provider = config.provider.lower()

        # 打印供应商信息用于调试
        from app.core.logging_config import get_business_logger
        logger = get_business_logger()
        logger.debug(f"获取模型参数 - Provider: {provider}, Model: {config.model_name}, is_omni: {config.is_omni}, deep_thinking: {config.deep_thinking}")

        filtered_extra_params, top_k_value = cls._extract_top_k_from_extra_params(config.extra_params)
        default_headers = config.extra_params.get("default_headers")
        if default_headers:
            logger.info(f"额外请求头已注入: {default_headers}")

        # dashscope 的 omni 模型使用 OpenAI 兼容模式
        if provider == ModelProvider.DASHSCOPE and config.is_omni:
            import httpx
            if not config.base_url:
                config.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            timeout_config = httpx.Timeout(
                timeout=config.timeout,
                connect=60.0,
                read=config.timeout,
                write=60.0,
                pool=10.0,
            )
            params: Dict[str, Any] = {
                "model": config.model_name,
                "base_url": config.base_url,
                "api_key": config.api_key,
                "timeout": timeout_config,
                "max_retries": config.max_retries,
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
            if ModelCapability.THINKING in config.capability:
                extra_body = params.setdefault("extra_body", {})
                if config.deep_thinking:
                    extra_body["enable_thinking"] = True
                    if config.thinking_budget_tokens:
                        extra_body["thinking_budget"] = config.thinking_budget_tokens
                else:
                    extra_body["enable_thinking"] = False
            # JSON 输出模式
            # thinking（A类）模型启用深度思考时，response_format 与思考模式 API 冲突，跳过由调用方 prompt 注入兜底
            if config.json_output:
                if not (ModelCapability.THINKING in config.capability and config.deep_thinking):
                    model_kwargs = params.setdefault("model_kwargs", {})
                    model_kwargs["response_format"] = {"type": "json_object"}
            return params

        if provider in [ModelProvider.OPENAI, ModelProvider.XINFERENCE, ModelProvider.GPUSTACK, ModelProvider.OLLAMA, ModelProvider.VOLCANO]:
            # 使用 httpx.Timeout 对象来设置详细的超时配置
            # 这样可以分别控制连接超时和读取超时
            import httpx
            timeout_config = httpx.Timeout(
                timeout=config.timeout,  # 总超时时间
                connect=60.0,  # 连接超时：60秒（足够建立 TCP 连接）
                read=config.timeout,  # 读取超时：使用配置的超时时间
                write=60.0,  # 写入超时：60秒
                pool=10.0,  # 连接池超时：10秒
            )
            # OllamaLLM 有 top_k 原生字段，可直接传入顶层；
            # ChatOpenAI/CompatibleChatOpenAI 不支持 top_k，OpenAI API 也无此参数，不能放入 model_kwargs
            # 否则会透传到 AsyncCompletions.create() 导致 unexpected keyword argument 错误
            if provider == ModelProvider.OLLAMA and top_k_value is not None:
                filtered_extra_params["top_k"] = top_k_value

            params: Dict[str, Any] = {
                "model": config.model_name,
                "base_url": config.base_url,
                "api_key": config.api_key,
                "timeout": timeout_config,
                "max_retries": config.max_retries,
                **filtered_extra_params
            }

            if default_headers and provider != ModelProvider.OLLAMA:
                params["default_headers"] = default_headers

            is_streaming = bool(config.extra_params.get("streaming"))
            if is_streaming:
                params["stream_usage"] = True
            # thinking 参数处理：
            # - thinking_only（B类）：不能传 enable_thinking，不做任何处理
            # - thinking（A类）：混合思考，流式和非流式均可开关
            if ModelCapability.THINKING in config.capability:
                if provider == ModelProvider.VOLCANO:
                    extra_body = params.setdefault("extra_body", {})
                    if config.deep_thinking:
                        extra_body["thinking"] = {"type": "enabled"}
                        effort = _map_budget_to_reasoning_effort(config.thinking_budget_tokens)
                        if effort is not None:
                            params["reasoning_effort"] = effort
                    else:
                        extra_body["thinking"] = {"type": "disabled"}
                else:
                    extra_body = params.setdefault("extra_body", {})
                    if config.deep_thinking:
                        extra_body["enable_thinking"] = True
                        if config.thinking_budget_tokens:
                            extra_body["thinking_budget"] = config.thinking_budget_tokens
                    else:
                        extra_body["enable_thinking"] = False
            # JSON 输出模式
            if config.json_output:
                model_kwargs = params.setdefault("model_kwargs", {})
                # VOLCANO 模型不支持 response_format，JSON 输出由 system prompt 注入实现
                # thinking（A类）模型启用深度思考时，response_format 与思考模式 API 冲突，跳过由调用方 prompt 注入兜底
                if provider != ModelProvider.VOLCANO and not (
                    ModelCapability.THINKING in config.capability and config.deep_thinking
                ):
                    model_kwargs["response_format"] = {"type": "json_object"}
            return params
        elif provider == ModelProvider.DASHSCOPE:
            params = {
                "model": config.model_name,
                "dashscope_api_key": config.api_key,
                "max_retries": config.max_retries,
                **filtered_extra_params
            }
            if top_k_value is not None:
                model_kwargs = params.setdefault("model_kwargs", {})
                model_kwargs["top_k"] = top_k_value
            # thinking 参数处理：
            # - thinking_only（B类）：不能传 enable_thinking，不做任何处理
            # - thinking（A类）：混合思考，流式和非流式均可开关，非流式也支持 thinking_budget
            if ModelCapability.THINKING in config.capability:
                is_streaming = bool(config.extra_params.get("streaming"))
                model_kwargs = params.setdefault("model_kwargs", {})
                if config.deep_thinking:
                    model_kwargs["enable_thinking"] = True
                    if config.thinking_budget_tokens:
                        model_kwargs["thinking_budget"] = config.thinking_budget_tokens
                    if is_streaming:
                        model_kwargs["incremental_output"] = True
                else:
                    model_kwargs["enable_thinking"] = False
            # JSON 输出模式
            # thinking（A类）模型启用深度思考时，response_format 与思考模式 API 冲突，跳过由调用方 prompt 注入兜底
            if config.json_output:
                if not (ModelCapability.THINKING in config.capability and config.deep_thinking):
                    model_kwargs = params.setdefault("model_kwargs", {})
                    model_kwargs["response_format"] = {"type": "json_object"}
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
            if top_k_value is not None:
                model_kwargs = params.setdefault("model_kwargs", {})
                model_kwargs["top_k"] = top_k_value

            # 解析 API key (格式: access_key_id:secret_access_key)
            if config.api_key and ":" in config.api_key:
                access_key_id, secret_access_key = config.api_key.split(":", 1)
                params["aws_access_key_id"] = access_key_id
                params["aws_secret_access_key"] = secret_access_key
            elif config.api_key:
                params["aws_access_key_id"] = config.api_key

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
            if config.json_output:
                if not (ModelCapability.THINKING in config.capability and config.deep_thinking):
                    model_kwargs = params.setdefault("model_kwargs", {})
                    model_kwargs["response_format"] = {"type": "json_object"}
            return params
        else:
            raise BusinessException(f"不支持的提供商: {provider}", code=BizCode.PROVIDER_NOT_SUPPORTED)

    @classmethod
    def get_rerank_model_params(cls, config: RedBearModelConfig) -> Dict[str, Any]:
        """根据提供商获取模型参数"""
        provider = config.provider.lower()
        if provider in [ModelProvider.XINFERENCE, ModelProvider.GPUSTACK]:
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

    # dashscope的omni模型 和 volcano模型使用
    if provider == ModelProvider.DASHSCOPE and config.is_omni:
        return CompatibleChatOpenAI
    if provider == ModelProvider.VOLCANO:
        return CompatibleChatOpenAI
    if provider in [ModelProvider.OPENAI, ModelProvider.XINFERENCE, ModelProvider.GPUSTACK]:
        return CompatibleChatOpenAI
        # if type == ModelType.LLM:
        #     return OpenAI
        # elif type == ModelType.CHAT:
        #     return CompatibleChatOpenAI
        # else:
        #     raise BusinessException(f"不支持的模型提供商及类型: {provider}-{type}", code=BizCode.PROVIDER_NOT_SUPPORTED)
    elif provider == ModelProvider.DASHSCOPE:
        return ChatTongyi
    elif provider == ModelProvider.OLLAMA:
        return OllamaLLM
    elif provider == ModelProvider.BEDROCK:
        return ChatBedrock
    else:
        raise BusinessException(f"不支持的模型提供商: {provider}", code=BizCode.PROVIDER_NOT_SUPPORTED)


def get_provider_embedding_class(provider: str) -> type[Embeddings]:
    """根据模型提供商获取对应的模型类"""
    provider = provider.lower()
    if provider in [ModelProvider.OPENAI, ModelProvider.XINFERENCE, ModelProvider.GPUSTACK]:
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
    if provider in [ModelProvider.XINFERENCE, ModelProvider.GPUSTACK]:
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
