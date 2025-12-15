from __future__ import annotations
import asyncio
import httpx
import time
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, TypeVar, Callable
from langchain_community.document_compressors import JinaRerank
from pydantic import BaseModel, Field
from langchain_core.runnables import RunnableSerializable
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseLLM, BaseLanguageModel
from langchain_core.outputs import LLMResult, Generation
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever

from app.models.models_model import ModelProvider, ModelType
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode

T = TypeVar("T")

class RedBearModelConfig(BaseModel):
    """模型配置基类"""
    model_name: str
    provider: str
    api_key: str
    base_url: Optional[str] = None
    # 请求超时时间（秒）- 默认120秒以支持复杂的LLM调用，可通过环境变量 LLM_TIMEOUT 配置
    timeout: float = Field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT", "120.0")))
    # 最大重试次数 - 默认2次以避免过长等待，可通过环境变量 LLM_MAX_RETRIES 配置
    max_retries: int = Field(default_factory=lambda: int(os.getenv("LLM_MAX_RETRIES", "2")))
    concurrency: int = 5         # 并发限流
    extra_params: Dict[str, Any] = {}

class RedBearModelFactory:
    """模型工厂类"""
    
    @classmethod
    def get_model_params(cls, config: RedBearModelConfig) -> Dict[str, Any]:
        """根据提供商获取模型参数"""
        provider = config.provider.lower()
        
        # 打印供应商信息用于调试
        from app.core.logging_config import get_business_logger
        logger = get_business_logger()
        logger.debug(f"获取模型参数 - Provider: {provider}, Model: {config.model_name}")

        if provider in [ModelProvider.OPENAI, ModelProvider.XINFERENCE, ModelProvider.GPUSTACK, ModelProvider.OLLAMA]:
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
            return {
                "model": config.model_name,
                "base_url": config.base_url,
                "api_key": config.api_key,
                "timeout": timeout_config,
                "max_retries": config.max_retries,
                **config.extra_params
                }
        elif provider == ModelProvider.DASHSCOPE:
            # DashScope (通义千问) 使用自己的参数格式
            # 注意: DashScopeEmbeddings 不支持 timeout 和 base_url 参数
            # 只支持: model, dashscope_api_key, max_retries, client
            return {
                "model": config.model_name,
                "dashscope_api_key": config.api_key,
                "max_retries": config.max_retries,
                **config.extra_params
            }
        elif provider == ModelProvider.BEDROCK:
            # Bedrock 使用 AWS 凭证
            # api_key 格式: "access_key_id:secret_access_key" 或只是 access_key_id
            # region 从 base_url 或 extra_params 获取
            params = {
                "model_id": config.model_name,
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
                # "base_url": config.base_url,
                "jina_api_key": config.api_key,
                **config.extra_params
                }
        else:
            raise BusinessException(f"不支持的提供商: {provider}", code=BizCode.PROVIDER_NOT_SUPPORTED)

def get_provider_llm_class(config:RedBearModelConfig, type: ModelType=ModelType.LLM) -> type[BaseLLM]:
    """根据模型提供商获取对应的模型类"""
    provider = config.provider.lower()
    if provider in [ModelProvider.OPENAI, ModelProvider.XINFERENCE, ModelProvider.GPUSTACK] : 
        if type == ModelType.LLM:
            from langchain_openai import OpenAI
            return OpenAI 
        elif type == ModelType.CHAT:
            from langchain_openai import ChatOpenAI
            return ChatOpenAI
    elif provider == ModelProvider.DASHSCOPE:
        from langchain_community.chat_models import ChatTongyi
        return ChatTongyi
    elif provider == ModelProvider.OLLAMA: 
        from langchain_ollama import OllamaLLM
        return OllamaLLM
    elif provider == ModelProvider.BEDROCK:
        from langchain_aws import ChatBedrock, ChatBedrockConverse

        return ChatBedrock
    else:
        raise BusinessException(f"不支持的模型提供商: {provider}", code=BizCode.PROVIDER_NOT_SUPPORTED)

def get_provider_embedding_class(provider: str) -> type[Embeddings]:
    """根据模型提供商获取对应的模型类"""
    provider = provider.lower()
    if provider in [ModelProvider.OPENAI, ModelProvider.XINFERENCE, ModelProvider.GPUSTACK] :
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
    if provider in [ModelProvider.XINFERENCE, ModelProvider.GPUSTACK] :
        from langchain_community.document_compressors import JinaRerank
        return JinaRerank        
    # elif provider == ModelProvider.OLLAMA:
    #     from langchain_ollama import OllamaEmbeddings
    #     return OllamaEmbeddings
    else:
        raise BusinessException(f"不支持的模型提供商: {provider}", code=BizCode.PROVIDER_NOT_SUPPORTED)