"""会话状态管理器 - 解决多轮对话路由错乱

P0-1：默认后端从"进程内内存"改为 Redis（异步），并给 key 加租户前缀
`conv_state:{tenant_id}:{conversation_id}` —— 多 worker / 重启后路由状态不再从零开始
（此前 `MultiAgentOrchestrator.__init__` 无参构造 = InMemoryStorage，每个请求一份，
"当前使用的 Agent / 连续使用轮数 / switch_count" 全部失真）。

同步 / 异步两条路径并存的原因：存储后端既可能是同步的（InMemoryStorage、老式
RedisStorage 用 redis.StrictRedis），也可能是异步的（AsyncRedisStorage）。统一走
`*_async` 方法 + `_maybe_await`，调用方只需 await 一套接口。
"""
import inspect
import json
from typing import Optional, Dict, Any, List
from datetime import datetime
from app.core.utils.datetime_utils import to_iso_z, utcnow_naive
from app.core.logging_config import get_business_logger

logger = get_business_logger()

_STATE_KEY_PREFIX = "conv_state"


class ConversationStateManager:
    """会话状态管理器
    
    用于管理多轮对话中的会话状态，包括：
    - 当前使用的 Agent
    - 路由历史
    - 主题追踪
    - Agent 切换统计
    """
    
    def __init__(self, storage_backend: Optional[Any] = None, tenant_id: Optional[str] = None):
        """初始化状态管理器
        
        Args:
            storage_backend: 存储后端（Redis/内存等）
            tenant_id: 租户 ID（用于 key 前缀隔离，None 时退化为无前缀）
        """
        self.storage = storage_backend or InMemoryStorage()
        self.ttl = 3600  # 1小时过期
        self.tenant_id = str(tenant_id) if tenant_id else None

    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        """同步/异步存储后端统一出口：awaitable 就 await，否则原样返回。"""
        if inspect.isawaitable(value):
            return await value
        return value

    def _key(self, conversation_id: str) -> str:
        """状态 key：`conv_state:{tenant_id}:{conversation_id}`（无租户时退化为旧格式）。"""
        if self.tenant_id:
            return f"{_STATE_KEY_PREFIX}:{self.tenant_id}:{conversation_id}"
        return f"{_STATE_KEY_PREFIX}:{conversation_id}"

    # ── 异步路径（生产主路径：Redis 后端）──────────────────────────────

    async def aget_state(self, conversation_id: str) -> Dict[str, Any]:
        """获取会话状态（异步）。"""
        try:
            state = await self._maybe_await(self.storage.get(self._key(conversation_id)))
        except Exception as e:
            # 观测/路由状态读失败不阻断对话：退化为本轮新建
            logger.warning(f"读取会话状态失败（降级为新建）: {e}")
            state = None
        if not state:
            logger.info(f"创建新会话状态: {conversation_id}")
            return await self._acreate_new_state(conversation_id)
        return state

    async def aupdate_state(
        self,
        conversation_id: str,
        agent_id: str,
        message: str,
        topic: Optional[str] = None,
        confidence: float = 1.0,
    ) -> Dict[str, Any]:
        """更新会话状态（异步）。"""
        state = await self.aget_state(conversation_id)
        self._apply_update(state, agent_id, message, topic, confidence)
        try:
            await self._maybe_await(self.storage.set(self._key(conversation_id), state, ttl=self.ttl))
        except Exception as e:
            logger.warning(f"写入会话状态失败（不影响本轮对话）: {e}")
        return state

    async def aclear_state(self, conversation_id: str) -> None:
        """清除会话状态（异步）。"""
        try:
            await self._maybe_await(self.storage.delete(self._key(conversation_id)))
        except Exception as e:
            logger.warning(f"清除会话状态失败: {e}")

    async def _acreate_new_state(self, conversation_id: str) -> Dict[str, Any]:
        state = self._blank_state(conversation_id)
        try:
            await self._maybe_await(self.storage.set(self._key(conversation_id), state, ttl=self.ttl))
        except Exception as e:
            logger.warning(f"初始化会话状态失败（不影响本轮对话）: {e}")
        return state

    # ── 同步路径（保留：内存后端与既有调用方）──────────────────────────

    def get_state(self, conversation_id: str) -> Dict[str, Any]:
        """获取会话状态
        
        Args:
            conversation_id: 会话 ID
            
        Returns:
            会话状态字典
        """
        state = self.storage.get(self._key(conversation_id))
        
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
        self._apply_update(state, agent_id, message, topic, confidence)

        # 保存状态
        self.storage.set(
            self._key(conversation_id),
            state,
            ttl=self.ttl
        )
        
        return state

    def _apply_update(
        self,
        state: Dict[str, Any],
        agent_id: str,
        message: str,
        topic: Optional[str] = None,
        confidence: float = 1.0,
    ) -> None:
        """状态更新逻辑（同步/异步两条路径共用，只改内存里的 state，不落存储）。"""
        # 检测 Agent 切换
        agent_changed = False
        if state.get("current_agent_id") and state["current_agent_id"] != agent_id:
            agent_changed = True
            state["switch_count"] += 1
            state["previous_agent_id"] = state["current_agent_id"]
            state["same_agent_turns"] = 0
            
            logger.info(
                "Agent 切换",
                extra={
                    "conversation_id": state.get("conversation_id"),
                    "from": state["current_agent_id"],
                    "to": agent_id,
                    "switch_count": state["switch_count"]
                }
            )
        else:
            state["same_agent_turns"] = int(state.get("same_agent_turns") or 0) + 1
        
        # 更新当前 Agent
        state["current_agent_id"] = agent_id
        state["last_message"] = message
        state["last_topic"] = topic
        state["updated_at"] = to_iso_z(utcnow_naive())
        
        # 添加到历史
        history_item = {
            "message": (message or "")[:100],  # 截断长消息
            "agent_id": agent_id,
            "topic": topic,
            "confidence": confidence,
            "agent_changed": agent_changed,
            "timestamp": to_iso_z(utcnow_naive())
        }
        state.setdefault("routing_history", []).append(history_item)
        
        # 保持最近 10 条历史
        if len(state["routing_history"]) > 10:
            state["routing_history"] = state["routing_history"][-10:]

    def clear_state(self, conversation_id: str) -> None:
        """清除会话状态
        
        Args:
            conversation_id: 会话 ID
        """
        self.storage.delete(self._key(conversation_id))
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
        state = self._blank_state(conversation_id)

        # 保存初始状态
        self.storage.set(
            self._key(conversation_id),
            state,
            ttl=self.ttl
        )

        return state

    @staticmethod
    def _blank_state(conversation_id: str) -> Dict[str, Any]:
        """空状态骨架（同步/异步共用）。"""
        return {
            "conversation_id": conversation_id,
            "current_agent_id": None,
            "previous_agent_id": None,
            "routing_history": [],
            "last_message": None,
            "last_topic": None,
            "switch_count": 0,
            "same_agent_turns": 0,
            "created_at": to_iso_z(utcnow_naive()),
            "updated_at": to_iso_z(utcnow_naive())
        }


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
    """Redis 存储后端（同步客户端，用于非 async 调用路径）"""
    
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


class AsyncRedisStorage:
    """Redis 存储后端（异步，P0-1 生产默认）。

    用 `aioRedis.aio_redis_*`（redis.asyncio 连接池）而不是同步 StrictRedis：
    调用全在 async 链路上，同步客户端会把 Redis RTT 阻塞在整个事件循环上。
    """

    def __init__(self, ttl: int = 3600):
        self.ttl = ttl

    async def get(self, key: str) -> Optional[Dict[str, Any]]:
        from app.aioRedis import aio_redis_get

        raw = await aio_redis_get(key)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("会话状态反序列化失败，按空状态处理")
            return None
        return data if isinstance(data, dict) else None

    async def set(self, key: str, value: Dict[str, Any], ttl: int = 3600) -> None:
        from app.aioRedis import aio_redis_set

        await aio_redis_set(key, value, expire=ttl or self.ttl)

    async def delete(self, key: str) -> None:
        from app.aioRedis import aio_redis_delete

        await aio_redis_delete(key)


def create_conversation_state_manager(
    tenant_id: Optional[str] = None,
    ttl: int = 3600,
) -> "ConversationStateManager":
    """构造会话状态管理器：默认异步 Redis 后端。

    Redis 后端是懒连接（构造不触网），可用性在每次读写时判定：
    `aget_state/aupdate_state` 内部 try/except，失败只记 warning 并按空状态
    继续 —— 路由状态是观测/优化数据，不可用时不阻断对话（行为等同改造前
    的进程内内存，只是跨轮状态丢失）。
    """
    return ConversationStateManager(
        storage_backend=AsyncRedisStorage(ttl=ttl),
        tenant_id=tenant_id,
    )
