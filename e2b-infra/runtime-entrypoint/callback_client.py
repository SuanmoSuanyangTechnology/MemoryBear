"""
Callback Client - Sandbox → API Communication

当 sandbox 内的 Agent/Workflow 需要访问外部资源（数据库查询、知识库检索、
记忆读写等）时，通过此客户端回调主 API 的内部端点。

通信流程:
    Sandbox Runtime → HTTP POST → API /internal/sandbox/callback → 返回结果

这样 sandbox 内无需数据库连接，所有数据访问都通过 API 代理。
"""
import asyncio
import logging
from typing import Any, Optional

import httpx

from .config import SandboxRuntimeConfig

logger = logging.getLogger(__name__)


class CallbackClient:
    """HTTP client for sandbox → API callbacks"""

    def __init__(self, config: SandboxRuntimeConfig):
        self.base_url = config.callback_url.rstrip("/")
        self.secret = config.callback_secret
        self.workspace_id = config.workspace_id
        self.user_id = config.user_id
        self.execution_id = config.execution_id
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "x-sandbox-secret": self.secret,
                    "x-sandbox-execution-id": self.execution_id,
                    "x-sandbox-workspace-id": self.workspace_id,
                    "x-sandbox-user-id": self.user_id,
                },
                timeout=httpx.Timeout(60.0, connect=10.0),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ──────────────────────────────────────────────────────────
    # Tool Execution Callbacks
    # ──────────────────────────────────────────────────────────

    async def execute_tool(
        self,
        tool_name: str,
        tool_type: str,
        tool_input: dict,
        tool_id: Optional[str] = None,
    ) -> dict:
        """Request the API to execute a tool on behalf of the sandbox

        Used for tools that require DB access or external service integration
        that's not available inside the sandbox.

        Args:
            tool_name: Tool name
            tool_type: Tool type (builtin, custom, mcp, etc.)
            tool_input: Tool input parameters
            tool_id: Optional tool UUID

        Returns:
            Tool execution result
        """
        client = await self._get_client()
        try:
            response = await client.post(
                "/internal/sandbox/tools/execute",
                json={
                    "tool_name": tool_name,
                    "tool_type": tool_type,
                    "tool_input": tool_input,
                    "tool_id": tool_id,
                },
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Tool callback failed: {e.response.status_code} - {e.response.text}")
            return {"error": f"Tool execution failed: {e.response.text}"}
        except httpx.RequestError as e:
            logger.error(f"Tool callback connection error: {e}")
            return {"error": f"Connection to API failed: {str(e)}"}

    # ──────────────────────────────────────────────────────────
    # Knowledge Retrieval
    # ──────────────────────────────────────────────────────────

    async def retrieve_knowledge(
        self,
        query: str,
        knowledge_base_ids: list[str],
        top_k: int = 5,
        score_threshold: float = 0.5,
    ) -> list[dict]:
        """Retrieve knowledge from knowledge bases via API

        Args:
            query: Search query
            knowledge_base_ids: List of KB IDs to search
            top_k: Number of results
            score_threshold: Minimum relevance score

        Returns:
            List of knowledge retrieval results
        """
        client = await self._get_client()
        try:
            response = await client.post(
                "/internal/sandbox/knowledge/retrieve",
                json={
                    "query": query,
                    "knowledge_base_ids": knowledge_base_ids,
                    "top_k": top_k,
                    "score_threshold": score_threshold,
                },
            )
            response.raise_for_status()
            return response.json().get("results", [])
        except Exception as e:
            logger.error(f"Knowledge retrieval callback failed: {e}")
            return []

    # ──────────────────────────────────────────────────────────
    # Memory Operations
    # ──────────────────────────────────────────────────────────

    async def read_memory(self, query: str, memory_type: str = "long_term") -> list[dict]:
        """Read user memory via API

        Args:
            query: Memory query
            memory_type: Type of memory (long_term, episodic, etc.)

        Returns:
            List of memory items
        """
        client = await self._get_client()
        try:
            response = await client.post(
                "/internal/sandbox/memory/read",
                json={
                    "query": query,
                    "memory_type": memory_type,
                },
            )
            response.raise_for_status()
            return response.json().get("memories", [])
        except Exception as e:
            logger.error(f"Memory read callback failed: {e}")
            return []

    async def write_memory(self, content: str, memory_type: str = "long_term", metadata: dict = None) -> bool:
        """Write user memory via API

        Args:
            content: Memory content to write
            memory_type: Type of memory
            metadata: Additional metadata

        Returns:
            Success flag
        """
        client = await self._get_client()
        try:
            response = await client.post(
                "/internal/sandbox/memory/write",
                json={
                    "content": content,
                    "memory_type": memory_type,
                    "metadata": metadata or {},
                },
            )
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Memory write callback failed: {e}")
            return False

    # ──────────────────────────────────────────────────────────
    # Conversation History
    # ──────────────────────────────────────────────────────────

    async def get_conversation_history(self, limit: int = 20) -> list[dict]:
        """Get conversation history from API

        Args:
            limit: Maximum number of messages to retrieve

        Returns:
            List of message dicts [{"role": "user/assistant", "content": "..."}]
        """
        client = await self._get_client()
        try:
            response = await client.get(
                "/internal/sandbox/conversation/history",
                params={"limit": limit},
            )
            response.raise_for_status()
            return response.json().get("messages", [])
        except Exception as e:
            logger.error(f"Conversation history callback failed: {e}")
            return []

    # ──────────────────────────────────────────────────────────
    # Report Results
    # ──────────────────────────────────────────────────────────

    async def report_execution_result(self, result: dict) -> bool:
        """Report final execution result back to API

        Args:
            result: Execution result data

        Returns:
            Success flag
        """
        client = await self._get_client()
        try:
            response = await client.post(
                "/internal/sandbox/execution/result",
                json=result,
            )
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Result report callback failed: {e}")
            return False
