"""会话状态管理器 - 解决多轮对话路由错乱"""
import json
from typing import Optional, Dict, Any, List
from datetime import datetime
from app.core.logging_config import get_business_logger

logger = get_business_logger()


class ConversationStateManager:
    """会话状态管理器
    
    用于管理多轮对话中的会话状态，包括：
    - 当前使用的 Agent
    - 路由历史
    - 主题追踪
    - Agent 切换统计
    """
    
    def __init__(self, storage_backend: Optional[Any] = None):
        """初始化状态管理器
        
        Args:
            storage_backend: 存储后端（Redis/内存等）
        """
        self.storage = storage_backend or InMemoryStorage()
        self.ttl = 3600  # 1小时过期
    
    def get_state(self, conversation_id: str) -> Dict[str, Any]:
        """获取会话状态
        
        Args:
            conversation_id: 会话 ID
            
        Returns:
            会话状态字典
        """
        state = self.storage.get(f"conv_state:{conversation_id}")
        
        if not state:
            logger.info(f"创建新会话状态: {conversation_id}")
            return self._create_new_state(conversation_id)
        
        return state
    
    def update_state(
        self,
        conversation_id: str,
        agent_id: str,
        message: str,
        topic: Optional[str] = None,
        confidence: float = 1.0
    ) -> Dict[str, Any]:
        """更新会话状态
        
        Args:
            conversation_id: 会话 ID
            agent_id: 当前 Agent ID
            message: 用户消息
            topic: 消息主题
            confidence: 路由置信度
            
        Returns:
            更新后的状态
        """
        state = self.get_state(conversation_id)
        
        # 检测 Agent 切换
        agent_changed = False
        if state["current_agent_id"] and state["current_agent_id"] != agent_id:
            agent_changed = True
            state["switch_count"] += 1
            state["previous_agent_id"] = state["current_agent_id"]
            state["same_agent_turns"] = 0
            
            logger.info(
                "Agent 切换",
                extra={
                    "conversation_id": conversation_id,
                    "from": state["current_agent_id"],
                    "to": agent_id,
                    "switch_count": state["switch_count"]
                }
            )
        else:
            state["same_agent_turns"] += 1
        
        # 更新当前 Agent
        state["current_agent_id"] = agent_id
        state["last_message"] = message
        state["last_topic"] = topic
        state["updated_at"] = datetime.now().isoformat()
        
        # 添加到历史
        history_item = {
            "message": message[:100],  # 截断长消息
            "agent_id": agent_id,
            "topic": topic,
            "confidence": confidence,
            "agent_changed": agent_changed,
            "timestamp": datetime.now().isoformat()
        }
        state["routing_history"].append(history_item)
        
        # 保持最近 10 条历史
        if len(state["routing_history"]) > 10:
            state["routing_history"] = state["routing_history"][-10:]
        
        # 保存状态
        self.storage.set(
            f"conv_state:{conversation_id}",
            state,
            ttl=self.ttl
        )
        
        return state
    
    def clear_state(self, conversation_id: str) -> None:
        """清除会话状态
        
        Args:
            conversation_id: 会话 ID
        """
        self.storage.delete(f"conv_state:{conversation_id}")
        logger.info(f"清除会话状态: {conversation_id}")
    
    def get_routing_history(
        self,
        conversation_id: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """获取路由历史
        
        Args:
            conversation_id: 会话 ID
            limit: 返回数量限制
            
        Returns:
            路由历史列表
        """
        state = self.get_state(conversation_id)
        history = state.get("routing_history", [])
        return history[-limit:] if history else []
    
    def get_statistics(self, conversation_id: str) -> Dict[str, Any]:
        """获取会话统计信息
        
        Args:
            conversation_id: 会话 ID
            
        Returns:
            统计信息
        """
        state = self.get_state(conversation_id)
        history = state.get("routing_history", [])
        
        # 统计各 Agent 使用次数
        agent_usage = {}
        for item in history:
            agent_id = item["agent_id"]
            agent_usage[agent_id] = agent_usage.get(agent_id, 0) + 1
        
        # 统计主题分布
        topic_distribution = {}
        for item in history:
            topic = item.get("topic", "未知")
            topic_distribution[topic] = topic_distribution.get(topic, 0) + 1
        
        return {
            "conversation_id": conversation_id,
            "total_turns": len(history),
            "switch_count": state.get("switch_count", 0),
            "current_agent_id": state.get("current_agent_id"),
            "same_agent_turns": state.get("same_agent_turns", 0),
            "agent_usage": agent_usage,
            "topic_distribution": topic_distribution,
            "created_at": state.get("created_at"),
            "updated_at": state.get("updated_at")
        }
    
    def _create_new_state(self, conversation_id: str) -> Dict[str, Any]:
        """创建新的会话状态
        
        Args:
            conversation_id: 会话 ID
            
        Returns:
            新的状态字典
        """
        state = {
            "conversation_id": conversation_id,
            "current_agent_id": None,
            "previous_agent_id": None,
            "routing_history": [],
            "last_message": None,
            "last_topic": None,
            "switch_count": 0,
            "same_agent_turns": 0,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        
        # 保存初始状态
        self.storage.set(
            f"conv_state:{conversation_id}",
            state,
            ttl=self.ttl
        )
        
        return state


class InMemoryStorage:
    """内存存储后端（用于开发和测试）"""
    
    def __init__(self):
        self._storage: Dict[str, Dict[str, Any]] = {}
    
    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """获取数据"""
        return self._storage.get(key)
    
    def set(self, key: str, value: Dict[str, Any], ttl: int = 3600) -> None:
        """设置数据"""
        self._storage[key] = value
    
    def delete(self, key: str) -> None:
        """删除数据"""
        if key in self._storage:
            del self._storage[key]
    
    def clear(self) -> None:
        """清空所有数据"""
        self._storage.clear()


class RedisStorage:
    """Redis 存储后端（用于生产环境）"""
    
    def __init__(self, redis_client):
        """初始化 Redis 存储
        
        Args:
            redis_client: Redis 客户端实例
        """
        self.redis = redis_client
    
    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """获取数据"""
        data = self.redis.get(key)
        if data:
            return json.loads(data)
        return None
    
    def set(self, key: str, value: Dict[str, Any], ttl: int = 3600) -> None:
        """设置数据"""
        self.redis.setex(key, ttl, json.dumps(value))
    
    def delete(self, key: str) -> None:
        """删除数据"""
        self.redis.delete(key)
