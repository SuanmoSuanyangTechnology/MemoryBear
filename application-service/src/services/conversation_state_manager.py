"""Conversation state manager - fixes multi-turn conversation routing faults

The default backend is Redis (async) with a tenant prefix on keys
`conv_state:{tenant_id}:{conversation_id}` - routing state no longer starts from
zero across multiple workers or restarts (previously a no-arg
`MultiAgentOrchestrator.__init__` built an InMemoryStorage per request, so
"current agent / consecutive turns on the same agent / switch_count" were all
invalid).

Both sync and async paths exist because the storage backend can be either
synchronous (InMemoryStorage, legacy RedisStorage on redis.StrictRedis) or
asynchronous (AsyncRedisStorage). Everything goes through `*_async` methods +
`_maybe_await`, so callers only await one interface.
"""
import logging

import inspect
import json
from typing import Optional, Dict, Any, List
from datetime import datetime
from ..utils.datetime_utils import to_iso_z, utcnow_naive

logger = logging.getLogger(__name__)

_STATE_KEY_PREFIX = "conv_state"


class ConversationStateManager:
    """Conversation state manager
    
    Manages conversation state across multi-turn dialogs, including:
    - The agent currently in use
    - Routing history
    - Topic tracking
    - Agent switch statistics
    """
    
    def __init__(self, storage_backend: Optional[Any] = None, tenant_id: Optional[str] = None):
        """Initialize the state manager
        
        Args:
            storage_backend: Storage backend (Redis/in-memory, etc.)
            tenant_id: Tenant ID (used to prefix keys for isolation; no prefix when None)
        """
        self.storage = storage_backend or InMemoryStorage()
        self.ttl = 3600  # 1-hour TTL
        self.tenant_id = str(tenant_id) if tenant_id else None

    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        """Unified sync/async storage helper: await the value if awaitable, else return it as-is."""
        if inspect.isawaitable(value):
            return await value
        return value

    def _key(self, conversation_id: str) -> str:
        """State key: `conv_state:{tenant_id}:{conversation_id}` (legacy format without a tenant)."""
        if self.tenant_id:
            return f"{_STATE_KEY_PREFIX}:{self.tenant_id}:{conversation_id}"
        return f"{_STATE_KEY_PREFIX}:{conversation_id}"

    # ── Async path (production primary path: Redis backend) ──────────────

    async def aget_state(self, conversation_id: str) -> Dict[str, Any]:
        """Get conversation state (async)."""
        try:
            state = await self._maybe_await(self.storage.get(self._key(conversation_id)))
        except Exception as e:
            # Routing-state read failure must not block the chat: degrade to a fresh state
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
        """Update conversation state (async)."""
        state = await self.aget_state(conversation_id)
        self._apply_update(state, agent_id, message, topic, confidence)
        try:
            await self._maybe_await(self.storage.set(self._key(conversation_id), state, ttl=self.ttl))
        except Exception as e:
            logger.warning(f"写入会话状态失败（不影响本轮对话）: {e}")
        return state

    async def aclear_state(self, conversation_id: str) -> None:
        """Clear conversation state (async)."""
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

    # ── Sync path (kept for the in-memory backend and existing callers) ───

    def get_state(self, conversation_id: str) -> Dict[str, Any]:
        """Get conversation state
        
        Args:
            conversation_id: Conversation ID
            
        Returns:
            Conversation state dictionary
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
        """Update conversation state
        
        Args:
            conversation_id: Conversation ID
            agent_id: Current agent ID
            message: User message
            topic: Message topic
            confidence: Routing confidence
            
        Returns:
            The updated state
        """
        state = self.get_state(conversation_id)
        self._apply_update(state, agent_id, message, topic, confidence)

        # Persist state
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
        """Apply a state update (shared by both sync/async paths; mutates in-memory state only)."""
        # Detect an agent switch
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
        
        # Update the current agent
        state["current_agent_id"] = agent_id
        state["last_message"] = message
        state["last_topic"] = topic
        state["updated_at"] = to_iso_z(utcnow_naive())
        
        # Append to routing history
        history_item = {
            "message": (message or "")[:100],  # truncate long messages
            "agent_id": agent_id,
            "topic": topic,
            "confidence": confidence,
            "agent_changed": agent_changed,
            "timestamp": to_iso_z(utcnow_naive())
        }
        state.setdefault("routing_history", []).append(history_item)
        
        # Keep only the latest 10 history entries
        if len(state["routing_history"]) > 10:
            state["routing_history"] = state["routing_history"][-10:]

    def clear_state(self, conversation_id: str) -> None:
        """Clear conversation state
        
        Args:
            conversation_id: Conversation ID
        """
        self.storage.delete(self._key(conversation_id))
        logger.info(f"清除会话状态: {conversation_id}")
    
    def get_routing_history(
        self,
        conversation_id: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get routing history
        
        Args:
            conversation_id: Conversation ID
            limit: Max entries to return
            
        Returns:
            Routing history list
        """
        state = self.get_state(conversation_id)
        history = state.get("routing_history", [])
        return history[-limit:] if history else []
    
    def get_statistics(self, conversation_id: str) -> Dict[str, Any]:
        """Get conversation statistics
        
        Args:
            conversation_id: Conversation ID
            
        Returns:
            Statistics dictionary
        """
        state = self.get_state(conversation_id)
        history = state.get("routing_history", [])
        
        # Count usage per agent
        agent_usage = {}
        for item in history:
            agent_id = item["agent_id"]
            agent_usage[agent_id] = agent_usage.get(agent_id, 0) + 1
        
        # Count topic distribution
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
        """Create a new conversation state
        
        Args:
            conversation_id: Conversation ID
            
        Returns:
            New state dictionary
        """
        state = self._blank_state(conversation_id)

        # Persist the initial state
        self.storage.set(
            self._key(conversation_id),
            state,
            ttl=self.ttl
        )

        return state

    @staticmethod
    def _blank_state(conversation_id: str) -> Dict[str, Any]:
        """Blank state template (shared by sync/async paths)."""
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
    """In-memory storage backend (for development and testing)."""
    
    def __init__(self):
        self._storage: Dict[str, Dict[str, Any]] = {}
    
    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """Get data."""
        return self._storage.get(key)
    
    def set(self, key: str, value: Dict[str, Any], ttl: int = 3600) -> None:
        """Set data."""
        self._storage[key] = value
    
    def delete(self, key: str) -> None:
        """Delete data."""
        if key in self._storage:
            del self._storage[key]
    
    def clear(self) -> None:
        """Clear all data."""
        self._storage.clear()


class RedisStorage:
    """Redis storage backend (sync client, for non-async call paths)."""
    
    def __init__(self, redis_client):
        """Initialize Redis storage
        
        Args:
            redis_client: Redis client instance
        """
        self.redis = redis_client
    
    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """Get data."""
        data = self.redis.get(key)
        if data:
            return json.loads(data)
        return None
    
    def set(self, key: str, value: Dict[str, Any], ttl: int = 3600) -> None:
        """Set data."""
        self.redis.setex(key, ttl, json.dumps(value))
    
    def delete(self, key: str) -> None:
        """Delete data."""
        self.redis.delete(key)


class AsyncRedisStorage:
    """Redis storage backend (async; production default).

    Uses `aioRedis.aio_redis_*` (redis.asyncio connection pool) instead of the
    sync StrictRedis: all calls run on the async path, and a sync client would
    block the entire event loop on Redis RTT.
    """

    def __init__(self, ttl: int = 3600):
        self.ttl = ttl

    async def get(self, key: str) -> Optional[Dict[str, Any]]:
        from ..aio_redis import aio_redis_get

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
        from ..aio_redis import aio_redis_set

        await aio_redis_set(key, value, expire=ttl or self.ttl)

    async def delete(self, key: str) -> None:
        from ..aio_redis import aio_redis_delete

        await aio_redis_delete(key)


def create_conversation_state_manager(
    tenant_id: Optional[str] = None,
    ttl: int = 3600,
) -> "ConversationStateManager":
    """Build a conversation state manager: async Redis backend by default.

    The Redis backend connects lazily (construction performs no I/O); availability
    is decided on each read/write: `aget_state/aupdate_state` wrap calls in
    try/except, log a warning and continue with an empty state on failure --
    routing state is observational/optimization data, so its unavailability must
    not block the chat (same behavior as the previous in-process memory, except
    cross-turn state is lost).
    """
    return ConversationStateManager(
        storage_backend=AsyncRedisStorage(ttl=ttl),
        tenant_id=tenant_id,
    )
