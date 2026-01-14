
import asyncio
from typing import Dict, Optional
from app.core.memory.utils.llm.llm_utils import get_llm_client_fast
from app.db import get_db
from app.core.logging_config import get_agent_logger

logger = get_agent_logger(__name__)

class LLMClientPool:
    """LLM客户端连接池"""
    
    def __init__(self, max_size: int = 5):
        self.max_size = max_size
        self.pools: Dict[str, asyncio.Queue] = {}
        self.active_clients: Dict[str, int] = {}
        
    async def get_client(self, llm_model_id: str):
        """获取LLM客户端"""
        if llm_model_id not in self.pools:
            self.pools[llm_model_id] = asyncio.Queue(maxsize=self.max_size)
            self.active_clients[llm_model_id] = 0
            
        pool = self.pools[llm_model_id]
        
        try:
            # 尝试从池中获取客户端
            client = pool.get_nowait()
            logger.debug(f"从池中获取LLM客户端: {llm_model_id}")
            return client
        except asyncio.QueueEmpty:
            # 池为空，创建新客户端
            if self.active_clients[llm_model_id] < self.max_size:
                db_session = next(get_db())
                client = get_llm_client_fast(llm_model_id, db_session)
                self.active_clients[llm_model_id] += 1
                logger.debug(f"创建新LLM客户端: {llm_model_id}")
                return client
            else:
                # 等待可用客户端
                logger.debug(f"等待LLM客户端可用: {llm_model_id}")
                return await pool.get()
                
    async def return_client(self, llm_model_id: str, client):
        """归还LLM客户端到池中"""
        if llm_model_id in self.pools:
            try:
                self.pools[llm_model_id].put_nowait(client)
                logger.debug(f"归还LLM客户端到池: {llm_model_id}")
            except asyncio.QueueFull:
                # 池已满，丢弃客户端
                self.active_clients[llm_model_id] -= 1
                logger.debug(f"池已满，丢弃LLM客户端: {llm_model_id}")

# 全局客户端池
llm_client_pool = LLMClientPool()
