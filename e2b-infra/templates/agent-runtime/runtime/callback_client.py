"""Callback Client - sandbox → API HTTP communication"""
import logging
from typing import Optional
import httpx

logger = logging.getLogger(__name__)


class CallbackClient:
    def __init__(self, config):
        self.base_url = (config.callback_url or "").rstrip("/")
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

    async def execute_tool(self, tool_name, tool_type, tool_input, tool_id=None):
        client = await self._get_client()
        try:
            resp = await client.post("/internal/sandbox/tools/execute", json={
                "tool_name": tool_name, "tool_type": tool_type,
                "tool_input": tool_input, "tool_id": tool_id,
            })
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"error": str(e)}
