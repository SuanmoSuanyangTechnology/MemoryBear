"""
优化的LLM服务类，用于压缩和统一LLM调用
"""

import asyncio
from typing import Any, Dict, List, Optional, Type, TypeVar, Union
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.logging_config import get_agent_logger
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.core.memory.llm_tools.openai_client import OpenAIClient

T = TypeVar('T', bound=BaseModel)

logger = get_agent_logger(__name__)


class OptimizedLLMService:
    """
    优化的LLM服务类，提供统一的LLM调用接口
    
    特性：
    1. 客户端复用 - 避免重复创建LLM客户端
    2. 批量处理 - 支持并发处理多个请求
    3. 错误处理 - 统一的错误处理和降级策略
    4. 性能优化 - 缓存和连接池优化
    """
    
    def __init__(self, db_session: Session):
        self.db_session = db_session
        self.client_factory = MemoryClientFactory(db_session)
        self._client_cache: Dict[str, OpenAIClient] = {}
        
    def _get_cached_client(self, llm_model_id: str) -> OpenAIClient:
        """获取缓存的LLM客户端，避免重复创建"""
        if llm_model_id not in self._client_cache:
            self._client_cache[llm_model_id] = self.client_factory.get_llm_client(llm_model_id)
        return self._client_cache[llm_model_id]
    
    async def structured_response(
        self,
        llm_model_id: str,
        system_prompt: str,
        response_model: Type[T],
        user_message: Optional[str] = None,
        fallback_value: Optional[Any] = None
    ) -> T:
        """
        统一的结构化响应接口
        
        Args:
            llm_model_id: LLM模型ID
            system_prompt: 系统提示词
            response_model: 响应模型类
            user_message: 用户消息（可选）
            fallback_value: 失败时的降级值
            
        Returns:
            结构化响应对象
        """
        try:
            llm_client = self._get_cached_client(llm_model_id)
            
            messages = [{"role": "system", "content": system_prompt}]
            if user_message:
                messages.append({"role": "user", "content": user_message})
            
            logger.debug(f"LLM调用: model={llm_model_id}, prompt_length={len(system_prompt)}")
            
            structured = await llm_client.response_structured(
                messages=messages,
                response_model=response_model
            )
            
            if structured is None:
                logger.warning(f"LLM返回None，使用降级值")
                return self._create_fallback_response(response_model, fallback_value)
            
            return structured
            
        except Exception as e:
            logger.error(f"结构化响应失败: {e}", exc_info=True)
            return self._create_fallback_response(response_model, fallback_value)
    
    async def batch_structured_response(
        self,
        llm_model_id: str,
        requests: List[Dict[str, Any]],
        response_model: Type[T],
        max_concurrent: int = 5
    ) -> List[T]:
        """
        批量处理结构化响应
        
        Args:
            llm_model_id: LLM模型ID
            requests: 请求列表，每个请求包含system_prompt等参数
            response_model: 响应模型类
            max_concurrent: 最大并发数
            
        Returns:
            结构化响应列表
        """
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def process_single_request(request: Dict[str, Any]) -> T:
            async with semaphore:
                return await self.structured_response(
                    llm_model_id=llm_model_id,
                    system_prompt=request.get('system_prompt', ''),
                    response_model=response_model,
                    user_message=request.get('user_message'),
                    fallback_value=request.get('fallback_value')
                )
        
        tasks = [process_single_request(req) for req in requests]
        return await asyncio.gather(*tasks)
    
    async def simple_response(
        self,
        llm_model_id: str,
        system_prompt: str,
        user_message: Optional[str] = None,
        fallback_message: str = "信息不足，无法回答"
    ) -> str:
        """
        简单的文本响应接口
        
        Args:
            llm_model_id: LLM模型ID
            system_prompt: 系统提示词
            user_message: 用户消息（可选）
            fallback_message: 失败时的降级消息
            
        Returns:
            响应文本
        """
        try:
            llm_client = self._get_cached_client(llm_model_id)
            
            messages = [{"role": "system", "content": system_prompt}]
            if user_message:
                messages.append({"role": "user", "content": user_message})
            
            response = await llm_client.response(messages=messages)
            
            if not response or not response.strip():
                return fallback_message
            
            return response.strip()
            
        except Exception as e:
            logger.error(f"简单响应失败: {e}", exc_info=True)
            return fallback_message
    
    def _create_fallback_response(self, response_model: Type[T], fallback_value: Optional[Any]) -> T:
        """创建降级响应"""
        try:
            if fallback_value is not None:
                if isinstance(fallback_value, response_model):
                    return fallback_value
                elif isinstance(fallback_value, dict):
                    return response_model(**fallback_value)
            
            # 尝试创建空的响应模型
            if hasattr(response_model, 'root'):
                # RootModel类型
                return response_model([])
            else:
                # 普通BaseModel类型
                return response_model()
                
        except Exception as e:
            logger.error(f"创建降级响应失败: {e}")
            # 最后的降级策略
            if hasattr(response_model, 'root'):
                return response_model([])
            else:
                return response_model()
    
    def clear_cache(self):
        """清理客户端缓存"""
        self._client_cache.clear()


class LLMServiceMixin:
    """
    LLM服务混入类，为节点提供便捷的LLM调用方法
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._llm_service: Optional[OptimizedLLMService] = None
    
    def get_llm_service(self, db_session: Session) -> OptimizedLLMService:
        """获取LLM服务实例"""
        if self._llm_service is None:
            self._llm_service = OptimizedLLMService(db_session)
        return self._llm_service
    
    async def call_llm_structured(
        self,
        state: Dict[str, Any],
        db_session: Session,
        system_prompt: str,
        response_model: Type[T],
        user_message: Optional[str] = None,
        fallback_value: Optional[Any] = None
    ) -> T:
        """
        便捷的结构化LLM调用方法
        
        Args:
            state: 状态字典，包含memory_config
            db_session: 数据库会话
            system_prompt: 系统提示词
            response_model: 响应模型类
            user_message: 用户消息（可选）
            fallback_value: 失败时的降级值
            
        Returns:
            结构化响应对象
        """
        memory_config = state.get('memory_config')
        if not memory_config:
            raise ValueError("State中缺少memory_config")
        
        llm_model_id = memory_config.llm_model_id
        if not llm_model_id:
            raise ValueError("Memory config中缺少llm_model_id")
        
        llm_service = self.get_llm_service(db_session)
        return await llm_service.structured_response(
            llm_model_id=llm_model_id,
            system_prompt=system_prompt,
            response_model=response_model,
            user_message=user_message,
            fallback_value=fallback_value
        )
    
    async def call_llm_simple(
        self,
        state: Dict[str, Any],
        db_session: Session,
        system_prompt: str,
        user_message: Optional[str] = None,
        fallback_message: str = "信息不足，无法回答"
    ) -> str:
        """
        便捷的简单LLM调用方法
        
        Args:
            state: 状态字典，包含memory_config
            db_session: 数据库会话
            system_prompt: 系统提示词
            user_message: 用户消息（可选）
            fallback_message: 失败时的降级消息
            
        Returns:
            响应文本
        """
        memory_config = state.get('memory_config')
        if not memory_config:
            raise ValueError("State中缺少memory_config")
        
        llm_model_id = memory_config.llm_model_id
        if not llm_model_id:
            raise ValueError("Memory config中缺少llm_model_id")
        
        llm_service = self.get_llm_service(db_session)
        return await llm_service.simple_response(
            llm_model_id=llm_model_id,
            system_prompt=system_prompt,
            user_message=user_message,
            fallback_message=fallback_message
        )