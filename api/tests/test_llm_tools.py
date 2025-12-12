"""
LLM 工具层单元测试

测试 LLM 客户端和 Embedder 客户端的功能，包括：
- LLM 客户端的调用逻辑
- Embedder 客户端的调用逻辑
- 重试机制和错误处理

使用 anyio 作为异步测试框架
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, Mock, patch, MagicMock
from typing import List, Dict, Any

from pydantic import BaseModel

from app.core.models.base import RedBearModelConfig
from app.core.memory.llm_tools.llm_client import LLMClient, LLMClientException
from app.core.memory.llm_tools.embedder_client import (
    EmbedderClient,
    EmbedderClientException
)
from app.core.memory.llm_tools.openai_client import OpenAIClient
from app.core.memory.llm_tools.openai_embedder import OpenAIEmbedderClient


# 测试用的 Pydantic 模型
class ResponseModel(BaseModel):
    """测试用的响应模型"""
    content: str
    confidence: float = 0.9


class MockLLMClient(LLMClient):
    """Mock LLM 客户端用于测试"""
    
    async def chat(self, messages: List[Dict[str, str]], **kwargs) -> Any:
        """Mock 聊天接口"""
        return "这是一个测试响应"
    
    async def response_structured(
        self,
        messages: List[Dict[str, str]],
        response_model: type[BaseModel],
        **kwargs
    ) -> BaseModel:
        """Mock 结构化输出接口"""
        return ResponseModel(content="测试内容", confidence=0.95)


class MockEmbedderClient(EmbedderClient):
    """Mock Embedder 客户端用于测试"""
    
    async def response(
        self,
        messages: List[str],
        **kwargs
    ) -> List[List[float]]:
        """Mock 嵌入向量生成"""
        return [[0.1, 0.2, 0.3] for _ in messages]


# ============================================================================
# LLM 客户端测试
# ============================================================================

class TestLLMClient:
    """LLM 客户端测试类"""
    
    @pytest.fixture
    def mock_config(self):
        """创建 Mock 配置"""
        return RedBearModelConfig(
            model_name="gpt-3.5-turbo",
            provider="openai",
            api_key="test-key",
            base_url="https://api.openai.com/v1",
            max_retries=3,
            timeout=30.0,
        )
    
    @pytest.fixture
    def mock_llm_client(self, mock_config):
        """创建 Mock LLM 客户端"""
        return MockLLMClient(mock_config)
    
    @pytest.mark.anyio
    async def test_llm_client_initialization(self, mock_config):
        """测试 LLM 客户端初始化"""
        client = MockLLMClient(mock_config)
        
        assert client.model_name == "gpt-3.5-turbo"
        assert client.provider == "openai"
        assert client.api_key == "test-key"
        assert client.max_retries == 3
        assert client.timeout == 30.0
    
    @pytest.mark.anyio
    async def test_llm_client_chat(self, mock_llm_client):
        """测试 LLM 聊天接口"""
        messages = [
            {"role": "user", "content": "你好"}
        ]
        
        response = await mock_llm_client.chat(messages)
        
        assert response == "这是一个测试响应"
    
    @pytest.mark.anyio
    async def test_llm_client_response_structured(self, mock_llm_client):
        """测试 LLM 结构化输出接口"""
        messages = [
            {"role": "user", "content": "生成一个测试响应"}
        ]
        
        response = await mock_llm_client.response_structured(
            messages,
            ResponseModel
        )
        
        assert isinstance(response, ResponseModel)
        assert response.content == "测试内容"
        assert response.confidence == 0.95
    
    @pytest.mark.anyio
    async def test_llm_client_chat_with_retry_success(self, mock_llm_client):
        """测试带重试的聊天接口（成功情况）"""
        messages = [
            {"role": "user", "content": "测试重试"}
        ]
        
        response = await mock_llm_client.chat_with_retry(messages)
        
        assert response == "这是一个测试响应"
    
    @pytest.mark.anyio
    async def test_llm_client_chat_with_retry_failure(self, mock_config):
        """测试带重试的聊天接口（失败情况）"""
        
        class FailingLLMClient(LLMClient):
            async def chat(self, messages: List[Dict[str, str]], **kwargs):
                raise Exception("模拟 LLM 调用失败")
            
            async def response_structured(
                self,
                messages: List[Dict[str, str]],
                response_model: type[BaseModel],
                **kwargs
            ):
                pass
        
        client = FailingLLMClient(mock_config)
        messages = [{"role": "user", "content": "测试"}]
        
        with pytest.raises(LLMClientException):
            await client.chat_with_retry(messages)
    
    @pytest.mark.anyio
    async def test_llm_client_response_structured_with_retry(
        self,
        mock_llm_client
    ):
        """测试带重试的结构化输出接口"""
        messages = [
            {"role": "user", "content": "生成结构化响应"}
        ]
        
        response = await mock_llm_client.response_structured_with_retry(
            messages,
            ResponseModel
        )
        
        assert isinstance(response, ResponseModel)
        assert response.content == "测试内容"


# ============================================================================
# Embedder 客户端测试
# ============================================================================

class TestEmbedderClient:
    """Embedder 客户端测试类"""
    
    @pytest.fixture
    def mock_config(self):
        """创建 Mock 配置"""
        return RedBearModelConfig(
            model_name="text-embedding-ada-002",
            provider="openai",
            api_key="test-key",
            base_url="https://api.openai.com/v1",
            max_retries=3,
            timeout=30.0,
        )
    
    @pytest.fixture
    def mock_embedder_client(self, mock_config):
        """创建 Mock Embedder 客户端"""
        return MockEmbedderClient(mock_config)
    
    @pytest.mark.anyio
    async def test_embedder_client_initialization(self, mock_config):
        """测试 Embedder 客户端初始化"""
        client = MockEmbedderClient(mock_config)
        
        assert client.model_name == "text-embedding-ada-002"
        assert client.provider == "openai"
        assert client.api_key == "test-key"
        assert client.max_retries == 3
    
    @pytest.mark.anyio
    async def test_embedder_client_response(self, mock_embedder_client):
        """测试 Embedder 响应接口"""
        texts = ["文本1", "文本2", "文本3"]
        
        embeddings = await mock_embedder_client.response(texts)
        
        assert len(embeddings) == 3
        assert all(len(emb) == 3 for emb in embeddings)
        assert all(isinstance(emb, list) for emb in embeddings)
    
    @pytest.mark.anyio
    async def test_embedder_client_response_with_retry(
        self,
        mock_embedder_client
    ):
        """测试带重试的嵌入向量生成"""
        texts = ["测试文本"]
        
        embeddings = await mock_embedder_client.response_with_retry(texts)
        
        assert len(embeddings) == 1
        assert len(embeddings[0]) == 3
    
    @pytest.mark.anyio
    async def test_embedder_client_embed_single(self, mock_embedder_client):
        """测试单个文本嵌入"""
        text = "单个测试文本"
        
        embedding = await mock_embedder_client.embed_single(text)
        
        assert isinstance(embedding, list)
        assert len(embedding) == 3
    
    @pytest.mark.anyio
    async def test_embedder_client_embed_batch(self, mock_embedder_client):
        """测试批量文本嵌入"""
        texts = [f"文本{i}" for i in range(10)]
        
        embeddings = await mock_embedder_client.embed_batch(
            texts,
            batch_size=3
        )
        
        assert len(embeddings) == 10
        assert all(len(emb) == 3 for emb in embeddings)
    
    @pytest.mark.anyio
    async def test_embedder_client_response_with_retry_failure(
        self,
        mock_config
    ):
        """测试带重试的嵌入向量生成（失败情况）"""
        
        class FailingEmbedderClient(EmbedderClient):
            async def response(self, messages: List[str], **kwargs):
                raise Exception("模拟嵌入向量生成失败")
        
        client = FailingEmbedderClient(mock_config)
        texts = ["测试文本"]
        
        with pytest.raises(EmbedderClientException):
            await client.response_with_retry(texts)
    
    @pytest.mark.anyio
    async def test_embedder_client_empty_input(self, mock_embedder_client):
        """测试空输入处理"""
        texts = []
        
        embeddings = await mock_embedder_client.response(texts)
        
        assert embeddings == []


# ============================================================================
# OpenAI 客户端集成测试（需要 Mock）
# ============================================================================

class TestOpenAIClient:
    """OpenAI 客户端测试类"""
    
    @pytest.fixture
    def mock_config(self):
        """创建 Mock 配置"""
        return RedBearModelConfig(
            model_name="gpt-3.5-turbo",
            provider="openai",
            api_key="test-key",
            base_url="https://api.openai.com/v1",
            max_retries=2,
            timeout=30.0,
        )
    
    @pytest.mark.anyio
    async def test_openai_client_initialization(self, mock_config):
        """测试 OpenAI 客户端初始化"""
        with patch('app.core.memory.llm_tools.openai_client.RedBearLLM'):
            client = OpenAIClient(mock_config)
            
            assert client.model_name == "gpt-3.5-turbo"
            assert client.provider == "openai"
    
    @pytest.mark.anyio
    async def test_openai_client_chat_mock(self, mock_config):
        """测试 OpenAI 聊天接口（Mock）"""
        with patch('app.core.memory.llm_tools.openai_client.RedBearLLM') as mock_llm:
            # 创建 Mock 链
            mock_chain = AsyncMock()
            mock_chain.ainvoke = AsyncMock(return_value="测试响应")
            
            # Mock ChatPromptTemplate
            with patch('app.core.memory.llm_tools.openai_client.ChatPromptTemplate') as mock_prompt:
                mock_prompt.from_template.return_value.__or__ = Mock(return_value=mock_chain)
                
                client = OpenAIClient(mock_config)
                messages = [{"role": "user", "content": "你好"}]
                
                response = await client.chat(messages)
                
                assert response == "测试响应"


class TestOpenAIEmbedderClient:
    """OpenAI Embedder 客户端测试类"""
    
    @pytest.fixture
    def mock_config(self):
        """创建 Mock 配置"""
        return RedBearModelConfig(
            model_name="text-embedding-ada-002",
            provider="openai",
            api_key="test-key",
            base_url="https://api.openai.com/v1",
            max_retries=2,
            timeout=30.0,
        )
    
    @pytest.mark.anyio
    async def test_openai_embedder_initialization(self, mock_config):
        """测试 OpenAI Embedder 客户端初始化"""
        with patch('app.core.memory.llm_tools.openai_embedder.RedBearEmbeddings'):
            client = OpenAIEmbedderClient(mock_config)
            
            assert client.model_name == "text-embedding-ada-002"
            assert client.provider == "openai"
    
    @pytest.mark.anyio
    async def test_openai_embedder_response_mock(self, mock_config):
        """测试 OpenAI 嵌入向量生成（Mock）"""
        with patch('app.core.memory.llm_tools.openai_embedder.RedBearEmbeddings') as mock_embeddings:
            # Mock aembed_documents 方法
            mock_model = Mock()
            mock_model.aembed_documents = AsyncMock(
                return_value=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
            )
            mock_embeddings.return_value = mock_model
            
            client = OpenAIEmbedderClient(mock_config)
            texts = ["文本1", "文本2"]
            
            embeddings = await client.response(texts)
            
            assert len(embeddings) == 2
            assert embeddings[0] == [0.1, 0.2, 0.3]
            assert embeddings[1] == [0.4, 0.5, 0.6]
    
    @pytest.mark.anyio
    async def test_openai_embedder_empty_input(self, mock_config):
        """测试空输入处理"""
        with patch('app.core.memory.llm_tools.openai_embedder.RedBearEmbeddings'):
            client = OpenAIEmbedderClient(mock_config)
            texts = []
            
            embeddings = await client.response(texts)
            
            assert embeddings == []


# ============================================================================
# 重试机制测试
# ============================================================================

class TestRetryMechanism:
    """重试机制测试类"""
    
    @pytest.fixture
    def mock_config(self):
        """创建 Mock 配置"""
        return RedBearModelConfig(
            model_name="test-model",
            provider="test",
            api_key="test-key",
            max_retries=3,
            timeout=30.0,
        )
    
    @pytest.mark.anyio
    async def test_retry_on_timeout(self, mock_config):
        """测试超时重试"""
        call_count = 0
        
        class TimeoutLLMClient(LLMClient):
            async def chat(self, messages: List[Dict[str, str]], **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count < 2:
                    raise asyncio.TimeoutError("超时")
                return "成功响应"
            
            async def response_structured(
                self,
                messages: List[Dict[str, str]],
                response_model: type[BaseModel],
                **kwargs
            ):
                pass
        
        client = TimeoutLLMClient(mock_config)
        messages = [{"role": "user", "content": "测试"}]
        
        response = await client.chat_with_retry(messages)
        
        assert response == "成功响应"
        assert call_count == 2  # 第一次失败，第二次成功
    
    @pytest.mark.anyio
    async def test_retry_exhausted(self, mock_config):
        """测试重试次数耗尽"""
        
        class AlwaysFailLLMClient(LLMClient):
            async def chat(self, messages: List[Dict[str, str]], **kwargs):
                raise Exception("持续失败")
            
            async def response_structured(
                self,
                messages: List[Dict[str, str]],
                response_model: type[BaseModel],
                **kwargs
            ):
                pass
        
        client = AlwaysFailLLMClient(mock_config)
        messages = [{"role": "user", "content": "测试"}]
        
        with pytest.raises(LLMClientException):
            await client.chat_with_retry(messages)


# ============================================================================
# 错误处理测试
# ============================================================================

class TestErrorHandling:
    """错误处理测试类"""
    
    @pytest.fixture
    def mock_config(self):
        """创建 Mock 配置"""
        return RedBearModelConfig(
            model_name="test-model",
            provider="test",
            api_key="test-key",
            max_retries=2,
            timeout=30.0,
        )
    
    @pytest.mark.anyio
    async def test_llm_client_exception(self, mock_config):
        """测试 LLM 客户端异常"""
        
        class ErrorLLMClient(LLMClient):
            async def chat(self, messages: List[Dict[str, str]], **kwargs):
                raise ValueError("无效的消息格式")
            
            async def response_structured(
                self,
                messages: List[Dict[str, str]],
                response_model: type[BaseModel],
                **kwargs
            ):
                pass
        
        client = ErrorLLMClient(mock_config)
        messages = [{"role": "user", "content": "测试"}]
        
        with pytest.raises(LLMClientException) as exc_info:
            await client.chat_with_retry(messages)
        
        assert "LLM 调用失败" in str(exc_info.value)
    
    @pytest.mark.anyio
    async def test_embedder_client_exception(self, mock_config):
        """测试 Embedder 客户端异常"""
        
        class ErrorEmbedderClient(EmbedderClient):
            async def response(self, messages: List[str], **kwargs):
                raise ValueError("无效的文本格式")
        
        client = ErrorEmbedderClient(mock_config)
        texts = ["测试文本"]
        
        with pytest.raises(EmbedderClientException) as exc_info:
            await client.response_with_retry(texts)
        
        assert "嵌入向量生成失败" in str(exc_info.value)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
