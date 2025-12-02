"""LLM 客户端适配器 - 支持多种 LLM 提供商"""
import os
import json
from typing import Optional, Dict, Any
from abc import ABC, abstractmethod
from app.core.logging_config import get_business_logger

logger = get_business_logger()


class BaseLLMClient(ABC):
    """LLM 客户端基类"""
    
    @abstractmethod
    async def chat(self, prompt: str, **kwargs) -> str:
        """发送聊天请求
        
        Args:
            prompt: 提示词
            **kwargs: 其他参数
            
        Returns:
            LLM 响应文本
        """
        pass


class OpenAIClient(BaseLLMClient):
    """OpenAI 客户端"""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-3.5-turbo",
        base_url: Optional[str] = None
    ):
        """初始化 OpenAI 客户端
        
        Args:
            api_key: API 密钥
            model: 模型名称
            base_url: API 基础 URL（可选，用于兼容其他服务）
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model
        self.base_url = base_url
        
        if not self.api_key:
            raise ValueError("OpenAI API key 未配置")
        
        try:
            from openai import AsyncOpenAI
            self.client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url
            )
        except ImportError:
            raise ImportError("请安装 openai 库: pip install openai")
    
    async def chat(self, prompt: str, **kwargs) -> str:
        """发送聊天请求
        
        Args:
            prompt: 提示词
            **kwargs: 其他参数（temperature, max_tokens 等）
            
        Returns:
            LLM 响应文本
        """
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=kwargs.get("temperature", 0.3),
                max_tokens=kwargs.get("max_tokens", 500)
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"OpenAI API 调用失败: {str(e)}")
            raise


class AzureOpenAIClient(BaseLLMClient):
    """Azure OpenAI 客户端"""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        deployment_name: Optional[str] = None,
        api_version: str = "2024-02-15-preview"
    ):
        """初始化 Azure OpenAI 客户端
        
        Args:
            api_key: API 密钥
            endpoint: Azure 端点
            deployment_name: 部署名称
            api_version: API 版本
        """
        self.api_key = api_key or os.getenv("AZURE_OPENAI_API_KEY")
        self.endpoint = endpoint or os.getenv("AZURE_OPENAI_ENDPOINT")
        self.deployment_name = deployment_name or os.getenv("AZURE_OPENAI_DEPLOYMENT")
        self.api_version = api_version
        
        if not all([self.api_key, self.endpoint, self.deployment_name]):
            raise ValueError("Azure OpenAI 配置不完整")
        
        try:
            from openai import AsyncAzureOpenAI
            self.client = AsyncAzureOpenAI(
                api_key=self.api_key,
                azure_endpoint=self.endpoint,
                api_version=self.api_version
            )
        except ImportError:
            raise ImportError("请安装 openai 库: pip install openai")
    
    async def chat(self, prompt: str, **kwargs) -> str:
        """发送聊天请求"""
        try:
            response = await self.client.chat.completions.create(
                model=self.deployment_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=kwargs.get("temperature", 0.3),
                max_tokens=kwargs.get("max_tokens", 500)
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"Azure OpenAI API 调用失败: {str(e)}")
            raise


class AnthropicClient(BaseLLMClient):
    """Anthropic Claude 客户端"""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-3-sonnet-20240229"
    ):
        """初始化 Anthropic 客户端
        
        Args:
            api_key: API 密钥
            model: 模型名称
        """
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.model = model
        
        if not self.api_key:
            raise ValueError("Anthropic API key 未配置")
        
        try:
            from anthropic import AsyncAnthropic
            self.client = AsyncAnthropic(api_key=self.api_key)
        except ImportError:
            raise ImportError("请安装 anthropic 库: pip install anthropic")
    
    async def chat(self, prompt: str, **kwargs) -> str:
        """发送聊天请求"""
        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=kwargs.get("max_tokens", 500),
                temperature=kwargs.get("temperature", 0.3),
                messages=[{"role": "user", "content": prompt}]
            )
            
            return response.content[0].text
            
        except Exception as e:
            logger.error(f"Anthropic API 调用失败: {str(e)}")
            raise


class LocalLLMClient(BaseLLMClient):
    """本地 LLM 客户端（通过 HTTP API）"""
    
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        model: str = "local-model"
    ):
        """初始化本地 LLM 客户端
        
        Args:
            base_url: API 基础 URL
            model: 模型名称
        """
        self.base_url = base_url
        self.model = model
        
        try:
            import httpx
            self.client = httpx.AsyncClient(timeout=30.0)
        except ImportError:
            raise ImportError("请安装 httpx 库: pip install httpx")
    
    async def chat(self, prompt: str, **kwargs) -> str:
        """发送聊天请求"""
        try:
            response = await self.client.post(
                f"{self.base_url}/v1/chat/completions",
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": kwargs.get("temperature", 0.3),
                    "max_tokens": kwargs.get("max_tokens", 500)
                }
            )
            
            response.raise_for_status()
            data = response.json()
            
            return data["choices"][0]["message"]["content"]
            
        except Exception as e:
            logger.error(f"本地 LLM API 调用失败: {str(e)}")
            raise


class MockLLMClient(BaseLLMClient):
    """模拟 LLM 客户端（用于测试）"""
    
    def __init__(self):
        """初始化模拟客户端"""
        self.call_count = 0
    
    async def chat(self, prompt: str, **kwargs) -> str:
        """发送聊天请求（返回模拟结果）"""
        self.call_count += 1
        
        logger.info(f"模拟 LLM 调用 (第 {self.call_count} 次)")
        
        # 简单的规则匹配
        prompt_lower = prompt.lower()
        
        if "数学" in prompt_lower or "方程" in prompt_lower or "计算" in prompt_lower:
            return json.dumps({
                "agent_id": "math-agent",
                "confidence": 0.9,
                "reason": "消息包含数学相关内容"
            }, ensure_ascii=False)
        
        elif "化学" in prompt_lower or "反应" in prompt_lower or "元素" in prompt_lower:
            return json.dumps({
                "agent_id": "chemistry-agent",
                "confidence": 0.85,
                "reason": "消息包含化学相关内容"
            }, ensure_ascii=False)
        
        elif "物理" in prompt_lower or "力" in prompt_lower or "速度" in prompt_lower:
            return json.dumps({
                "agent_id": "physics-agent",
                "confidence": 0.88,
                "reason": "消息包含物理相关内容"
            }, ensure_ascii=False)
        
        elif "语文" in prompt_lower or "古诗" in prompt_lower or "作文" in prompt_lower:
            return json.dumps({
                "agent_id": "chinese-agent",
                "confidence": 0.87,
                "reason": "消息包含语文相关内容"
            }, ensure_ascii=False)
        
        elif "英语" in prompt_lower or "单词" in prompt_lower or "语法" in prompt_lower:
            return json.dumps({
                "agent_id": "english-agent",
                "confidence": 0.86,
                "reason": "消息包含英语相关内容"
            }, ensure_ascii=False)
        
        else:
            return json.dumps({
                "agent_id": "math-agent",
                "confidence": 0.5,
                "reason": "无法明确判断，使用默认 Agent"
            }, ensure_ascii=False)


class LLMClientFactory:
    """LLM 客户端工厂"""
    
    @staticmethod
    def create(
        provider: str = "mock",
        **kwargs
    ) -> BaseLLMClient:
        """创建 LLM 客户端
        
        Args:
            provider: 提供商名称 (openai, azure, anthropic, local, mock)
            **kwargs: 客户端配置参数
            
        Returns:
            LLM 客户端实例
        """
        provider = provider.lower()
        
        if provider == "openai":
            return OpenAIClient(**kwargs)
        
        elif provider == "azure":
            return AzureOpenAIClient(**kwargs)
        
        elif provider == "anthropic":
            return AnthropicClient(**kwargs)
        
        elif provider == "local":
            return LocalLLMClient(**kwargs)
        
        elif provider == "mock":
            return MockLLMClient()
        
        else:
            raise ValueError(f"不支持的 LLM 提供商: {provider}")
    
    @staticmethod
    def create_from_env() -> BaseLLMClient:
        """从环境变量创建 LLM 客户端
        
        环境变量：
        - LLM_PROVIDER: 提供商名称
        - OPENAI_API_KEY: OpenAI API 密钥
        - AZURE_OPENAI_API_KEY: Azure OpenAI API 密钥
        - ANTHROPIC_API_KEY: Anthropic API 密钥
        
        Returns:
            LLM 客户端实例
        """
        provider = os.getenv("LLM_PROVIDER", "mock")
        
        logger.info(f"从环境变量创建 LLM 客户端: {provider}")
        
        return LLMClientFactory.create(provider)
