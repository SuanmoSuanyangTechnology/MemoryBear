"""
E2B Sandbox Service - API 侧的 Sandbox 客户端

负责与自建 E2B Orchestrator 通信，管理 sandbox 生命周期，
在 sandbox 内执行 Agent/Workflow 并读取流式输出事件。

架构流程:
    E2BAgentAdapter
        → E2BSandboxService.run_agent() / run_workflow()
            → POST /v1/sandboxes               (创建/获取 sandbox)
            → POST /v1/sandboxes/{id}/exec      (写入 snapshot + 执行 + SSE 流)
            → DELETE /v1/sandboxes/{id}         (销毁 sandbox)
        ← yield event dicts to caller

Orchestrator 负责 warm pool、容器生命周期、snapshot 写入和执行。
"""
import asyncio
import json
import logging
import time
import uuid
from typing import Any, AsyncGenerator, Optional

import httpx

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

_sandbox_service_instance: Optional["E2BSandboxService"] = None


def get_sandbox_service() -> "E2BSandboxService":
    """Get or create the singleton E2BSandboxService instance"""
    global _sandbox_service_instance
    if _sandbox_service_instance is None:
        _sandbox_service_instance = E2BSandboxService()
    return _sandbox_service_instance


class E2BSandboxService:
    """Manages sandbox lifecycle and execution via self-hosted E2B orchestrator.

    The orchestrator handles warm pool, container lifecycle, snapshot writing,
    and agent execution internally. This client just calls the unified API.
    """

    def __init__(self):
        self.orchestrator_url = settings.E2B_ORCHESTRATOR_URL.rstrip("/")
        self.api_secret = settings.E2B_ORCHESTRATOR_SECRET
        self.default_timeout = settings.E2B_SANDBOX_TIMEOUT
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client for orchestrator communication"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.orchestrator_url,
                headers={"x-api-key": self.api_secret},
                timeout=httpx.Timeout(
                    connect=10.0,
                    read=600.0,
                    write=30.0,
                    pool=10.0,
                ),
            )
        return self._client

    async def close(self):
        """Close HTTP client"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── Sandbox Lifecycle ──

    async def create_sandbox(self) -> dict:
        """Create or acquire a sandbox from the orchestrator.

        The orchestrator handles warm pool internally.
        """
        client = await self._get_client()
        response = await client.post("/v1/sandboxes")
        response.raise_for_status()
        sandbox_info = response.json()
        logger.info(
            "Sandbox acquired: %s (pool_hit=%s)",
            sandbox_info.get("sandbox_id"),
            sandbox_info.get("pool_hit"),
        )
        return sandbox_info

    async def kill_sandbox(self, sandbox_id: str) -> bool:
        """Kill and cleanup a sandbox"""
        client = await self._get_client()
        try:
            response = await client.delete(f"/v1/sandboxes/{sandbox_id}")
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"Failed to kill sandbox {sandbox_id}: {e}")
            return False

    # ── Agent Execution ──

    async def run_agent(
        self,
        *,
        agent_config: dict,
        model_config: dict,
        message: str,
        context: dict,
        workspace_id: str,
        user_id: str,
        conversation_id: str = "",
        execution_id: str | None = None,
        timeout: int | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Run an Agent inside a sandbox.

        Acquires a sandbox, sends snapshot to the orchestrator's unified
        exec endpoint, and streams back SSE events as dicts.

        Yields:
            Event dicts: {"event": str, "data": dict, "timestamp": float}
        """
        execution_id = execution_id or str(uuid.uuid4())
        sandbox_timeout = timeout or self.default_timeout
        sandbox_id = None

        try:
            client = await self._get_client()

            # 1. Acquire sandbox (orchestrator handles warm pool internally)
            resp = await client.post("/v1/sandboxes")
            resp.raise_for_status()
            sandbox_data = resp.json()
            sandbox_id = sandbox_data["sandbox_id"]
            logger.info(
                "Sandbox acquired: %s (pool_hit=%s)",
                sandbox_id,
                sandbox_data.get("pool_hit"),
            )

            # 2. Build snapshot
            snapshot = {
                "type": "agent_stream",
                "timeout": sandbox_timeout,
                "agent_config": agent_config,
                "model_config": model_config,
                "message": message,
                "context": context,
                "runtime_env": {
                    "callback_url": settings.E2B_CALLBACK_URL,
                    "callback_secret": settings.E2B_CALLBACK_SECRET,
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                    "execution_id": execution_id,
                    "conversation_id": conversation_id,
                },
            }

            # 3. Exec agent via unified endpoint (SSE stream)
            async with client.stream(
                "POST",
                f"/v1/sandboxes/{sandbox_id}/exec",
                json={"run_id": execution_id, "snapshot": snapshot},
            ) as response:
                response.raise_for_status()

                current_event = ""
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("event:"):
                        current_event = line[6:].strip()
                    elif line.startswith("data:"):
                        data_text = line[5:].strip()
                        try:
                            data = json.loads(data_text)
                        except json.JSONDecodeError:
                            data = {"content": data_text}
                        yield {
                            "event": current_event or "message",
                            "data": data,
                            "timestamp": time.time(),
                        }
                        current_event = ""

        except Exception as e:
            logger.error("Agent sandbox execution failed: %s", e, exc_info=True)
            yield {
                "event": "execution_error",
                "data": {"error": str(e), "error_type": type(e).__name__},
                "timestamp": time.time(),
            }
        finally:
            if sandbox_id:
                await self.kill_sandbox(sandbox_id)

    # ── Workflow Execution ──

    async def run_workflow(
        self,
        *,
        workflow_config: dict,
        input_data: dict,
        execution_context: dict,
        model_config: dict | None = None,
        timeout: int | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Run a Workflow inside a sandbox.

        Uses the same unified exec endpoint as run_agent. The snapshot
        type is set to 'workflow_stream' so the agent-runner dispatches
        to its workflow executor.
        """
        execution_id = execution_context.get("execution_id", str(uuid.uuid4()))
        sandbox_timeout = timeout or self.default_timeout
        sandbox_id = None

        try:
            client = await self._get_client()

            # 1. Acquire sandbox
            resp = await client.post("/v1/sandboxes")
            resp.raise_for_status()
            sandbox_data = resp.json()
            sandbox_id = sandbox_data["sandbox_id"]

            # 2. Build snapshot
            snapshot = {
                "type": "workflow_stream",
                "timeout": sandbox_timeout,
                "workflow_config": workflow_config,
                "input_data": input_data,
                "execution_context": execution_context,
                "runtime_env": {
                    "callback_url": settings.E2B_CALLBACK_URL,
                    "callback_secret": settings.E2B_CALLBACK_SECRET,
                    "workspace_id": execution_context.get("workspace_id", ""),
                    "user_id": execution_context.get("user_id", ""),
                    "execution_id": execution_id,
                    "conversation_id": execution_context.get("conversation_id", ""),
                },
            }
            if model_config:
                snapshot["model_config"] = model_config

            # 3. Exec via unified endpoint
            async with client.stream(
                "POST",
                f"/v1/sandboxes/{sandbox_id}/exec",
                json={"run_id": execution_id, "snapshot": snapshot},
            ) as response:
                response.raise_for_status()

                current_event = ""
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("event:"):
                        current_event = line[6:].strip()
                    elif line.startswith("data:"):
                        data_text = line[5:].strip()
                        try:
                            data = json.loads(data_text)
                        except json.JSONDecodeError:
                            data = {"content": data_text}
                        yield {
                            "event": current_event or "message",
                            "data": data,
                            "timestamp": time.time(),
                        }
                        current_event = ""

        except Exception as e:
            logger.error("Workflow sandbox execution failed: %s", e, exc_info=True)
            yield {
                "event": "execution_error",
                "data": {"error": str(e), "error_type": type(e).__name__},
                "timestamp": time.time(),
            }
        finally:
            if sandbox_id:
                await self.kill_sandbox(sandbox_id)

    # ── Utility ──

    async def health_check(self) -> dict:
        """Check orchestrator health"""
        try:
            client = await self._get_client()
            response = await client.get("/v1/health")
            return response.json()
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def get_stats(self) -> dict:
        """Get orchestrator stats (pool, active runs, hosts)"""
        try:
            client = await self._get_client()
            response = await client.get("/v1/stats")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            return {"error": str(e)}
