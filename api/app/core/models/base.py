from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, TypeVar

from langchain_aws import ChatBedrock
from langchain_community.chat_models import ChatTongyi
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseLLM
from langchain_ollama import OllamaLLM
from langchain_openai import ChatOpenAI, OpenAI
from pydantic import BaseModel, Field, model_validator

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.models.models_model import ModelProvider, ModelType
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

    @model_validator(mode="after")
    def _resolve_capabilities(self) -> "RedBearModelConfig":
        from app.core.logging_config import get_business_logger
        logger = get_business_logger()
        if self.deep_thinking and "thinking" not in self.capability:
            logger.warning(
                f"模型 {self.model_name} 不支持深度思考（capability 中无 'thinking'），已自动关闭 deep_thinking"
            )
            self.deep_thinking = False
            self.thinking_budget_tokens = None
        if self.json_output and "json_output" not in self.capability:
            logger.warning(
                f"模型 {self.model_name} 不支持 JSON 输出（capability 中无 'json_output'），已自动关闭 json_output"
            )
            self.json_output = False
        return self


class RedBearModelFactory:
    """模型工厂类"""

    @classmethod
    def get_model_params(cls, config: RedBearModelConfig) -> Dict[str, Any]:
        """根据提供商获取模型参数"""
        provider = config.provider.lower()

        # 打印供应商信息用于调试
        from app.core.logging_config import get_business_logger
        logger = get_business_logger()
        logger.debug(f"获取模型参数 - Provider: {provider}, Model: {config.model_name}, is_omni: {config.is_omni}, deep_thinking: {config.deep_thinking}")

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
                **config.extra_params
            }
            # 流式模式下启用 stream_usage 以获取 token 统计
            is_streaming = bool(config.extra_params.get("streaming"))
            if is_streaming:
                params["stream_usage"] = True
            # 支持 thinking 的模型始终传 enable_thinking，关闭时显式传 False 避免模型默认开启思考
            if "thinking" in config.capability:
                extra_body = params.setdefault("extra_body", {})
                if config.deep_thinking:
                    extra_body["enable_thinking"] = False
                    if is_streaming:
                        extra_body["enable_thinking"] = True
                    if config.thinking_budget_tokens:
                        extra_body["thinking_budget"] = config.thinking_budget_tokens
            # JSON 输出模式
            if config.json_output:
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
            params: Dict[str, Any] = {
                "model": config.model_name,
                "base_url": config.base_url,
                "api_key": config.api_key,
                "timeout": timeout_config,
                "max_retries": config.max_retries,
                **config.extra_params
            }
            # 流式模式下启用 stream_usage 以获取 token 统计
            is_streaming = bool(config.extra_params.get("streaming"))
            if is_streaming:
                params["stream_usage"] = True
            # 支持 thinking 的模型始终传 enable_thinking，关闭时显式传 False 避免模型默认开启思考
            if "thinking" in config.capability:
                # VOLCANO 深度思考仅流式支持
                if provider == ModelProvider.VOLCANO:
                    thinking_config: Dict[str, Any] = {"type": "enabled" if config.deep_thinking else "disabled"}
                    if config.deep_thinking and config.thinking_budget_tokens:
                        thinking_config["budget_tokens"] = config.thinking_budget_tokens
                    params["extra_body"] = {"thinking": thinking_config}
                else:
                    extra_body = params.setdefault("extra_body", {})
                    if config.deep_thinking:
                        extra_body["enable_thinking"] = False
                        if is_streaming:
                            extra_body["enable_thinking"] = True
                        if config.thinking_budget_tokens:
                            extra_body["thinking_budget"] = config.thinking_budget_tokens
            # JSON 输出模式
            if config.json_output:
                model_kwargs = params.setdefault("model_kwargs", {})
                # VOLCANO 模型不支持 response_format，JSON 输出由 system prompt 注入实现
                if provider != ModelProvider.VOLCANO:
                    model_kwargs["response_format"] = {"type": "json_object"}
            return params
        elif provider == ModelProvider.DASHSCOPE:
            params = {
                "model": config.model_name,
                "dashscope_api_key": config.api_key,
                "max_retries": config.max_retries,
                **config.extra_params
            }
            # 支持 thinking 的模型始终传 enable_thinking，关闭时显式传 False 避免模型默认开启思考
            if "thinking" in config.capability:
                is_streaming = bool(config.extra_params.get("streaming"))
                model_kwargs = params.setdefault("model_kwargs", {})
                if config.deep_thinking:
                    model_kwargs["enable_thinking"] = False
                    if is_streaming:
                        model_kwargs["enable_thinking"] = True
                        model_kwargs["incremental_output"] = True
                    if config.thinking_budget_tokens:
                        model_kwargs["thinking_budget"] = config.thinking_budget_tokens
            if config.json_output:
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
                **config.extra_params
            }

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
            if config.json_output:
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
