"""
E2B Sandbox Service - API 侧的 Sandbox 客户端

负责与自建 E2B Orchestrator 通信，管理 sandbox 生命周期，
在 sandbox 内执行 Agent/Workflow 并读取流式输出事件。

架构流程:
    AgentRunService / WorkflowService
        → E2BSandboxService.run_agent() / run_workflow()
            → Orchestrator API: create sandbox
            → Orchestrator API: write config file
            → Orchestrator API: run entrypoint command (streaming)
            → Parse stdout JSON Lines events
            → Orchestrator API: kill sandbox
        ← yield events to caller
"""
import asyncio
import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, AsyncGenerator, Optional

import httpx

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# Singleton instance
_sandbox_service_instance: Optional["E2BSandboxService"] = None


def get_sandbox_service() -> "E2BSandboxService":
    """Get or create the singleton E2BSandboxService instance"""
    global _sandbox_service_instance
    if _sandbox_service_instance is None:
        _sandbox_service_instance = E2BSandboxService()
    return _sandbox_service_instance


class E2BSandboxService:
    """Manages sandbox lifecycle and execution via self-hosted E2B orchestrator

    Supports warm pool: pre-creates idle sandboxes so requests don't
    pay the container startup cost.
    """

    def __init__(self):
        self.orchestrator_url = settings.E2B_ORCHESTRATOR_URL.rstrip("/")
        self.api_secret = settings.E2B_ORCHESTRATOR_SECRET
        self.template_id = settings.E2B_TEMPLATE_ID
        self.default_timeout = settings.E2B_SANDBOX_TIMEOUT
        self._client: Optional[httpx.AsyncClient] = None
        # Warm pool: pre-created sandbox IDs ready for use
        self._warm_pool: asyncio.Queue = asyncio.Queue()
        self._pool_size = int(os.getenv("E2B_WARM_POOL_SIZE", "2"))
        self._pool_task: Optional[asyncio.Task] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client for orchestrator communication"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.orchestrator_url,
                headers={"x-api-key": self.api_secret},
                timeout=httpx.Timeout(
                    connect=10.0,
                    read=600.0,  # Long read timeout for streaming
                    write=30.0,
                    pool=10.0,
                ),
            )
        return self._client

    async def close(self):
        """Close HTTP client"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ──────────────────────────────────────────────────────────
    # Warm Pool Management
    # ──────────────────────────────────────────────────────────

    async def ensure_warm_pool(self):
        """Start the warm pool background task (call once at app startup)"""
        if self._pool_size > 0 and self._pool_task is None:
            self._pool_task = asyncio.create_task(self._warm_pool_loop())
            logger.info(f"E2B warm pool started (size={self._pool_size})")

    async def _warm_pool_loop(self):
        """Background task that keeps the warm pool filled"""
        while True:
            try:
                while self._warm_pool.qsize() < self._pool_size:
                    sandbox_info = await self.create_sandbox(
                        timeout=self.default_timeout + 60,
                        metadata={"pool": "warm", "status": "idle"},
                    )
                    sandbox_id = sandbox_info["sandbox_id"]
                    # Wait for it to be ready
                    if await self.wait_for_sandbox_ready(sandbox_id, max_wait=15):
                        await self._warm_pool.put(sandbox_id)
                        logger.debug(f"Warm pool: added sandbox {sandbox_id[:8]}, pool_size={self._warm_pool.qsize()}")
                    else:
                        await self.kill_sandbox(sandbox_id)
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Warm pool error: {e}")
                await asyncio.sleep(5)

    async def _get_warm_sandbox(self) -> Optional[str]:
        """Try to get a pre-warmed sandbox from the pool"""
        try:
            sandbox_id = self._warm_pool.get_nowait()
            # Verify it's still alive
            client = await self._get_client()
            resp = await client.get(f"/v1/sandboxes/{sandbox_id}")
            if resp.status_code == 200 and resp.json().get("status") == "running":
                logger.info(f"Using warm sandbox {sandbox_id[:8]} (pool remaining: {self._warm_pool.qsize()})")
                return sandbox_id
            # Dead sandbox, discard
            return None
        except asyncio.QueueEmpty:
            return None

    # ──────────────────────────────────────────────────────────
    # Sandbox Lifecycle
    # ──────────────────────────────────────────────────────────

    async def create_sandbox(
        self,
        env_vars: dict[str, str] | None = None,
        timeout: int | None = None,
        metadata: dict[str, str] | None = None,
    ) -> dict:
        """Create a new sandbox instance

        Args:
            env_vars: Environment variables to inject
            timeout: Sandbox lifetime in seconds
            metadata: Custom metadata for tracking

        Returns:
            Sandbox info dict with sandbox_id, status, etc.
        """
        client = await self._get_client()
        payload = {
            "template_id": self.template_id,
            "timeout": timeout or self.default_timeout,
            "env_vars": env_vars or {},
            "metadata": metadata or {},
            "cpu_count": settings.E2B_SANDBOX_CPU,
            "memory_mb": settings.E2B_SANDBOX_MEMORY_MB,
            "enable_network": True,
        }

        response = await client.post("/v1/sandboxes", json=payload)
        response.raise_for_status()
        sandbox_info = response.json()

        logger.info(
            "Sandbox created",
            extra={
                "sandbox_id": sandbox_info.get("sandbox_id"),
                "template": self.template_id,
                "timeout": payload["timeout"],
            },
        )
        return sandbox_info

    async def wait_for_sandbox_ready(
        self, sandbox_id: str, max_wait: float = 30.0, poll_interval: float = 0.5
    ) -> bool:
        """Wait until sandbox status becomes 'running'

        Args:
            sandbox_id: Sandbox ID
            max_wait: Maximum seconds to wait
            poll_interval: Seconds between status checks

        Returns:
            True if sandbox is running, False if timed out
        """
        client = await self._get_client()
        start = time.time()

        while time.time() - start < max_wait:
            response = await client.get(f"/v1/sandboxes/{sandbox_id}")
            if response.status_code == 200:
                info = response.json()
                if info.get("status") == "running":
                    return True
                if info.get("status") == "error":
                    raise RuntimeError(
                        f"Sandbox {sandbox_id} failed to start"
                    )
            await asyncio.sleep(poll_interval)

        return False

    async def kill_sandbox(self, sandbox_id: str) -> bool:
        """Kill and cleanup a sandbox

        Args:
            sandbox_id: Sandbox ID to kill

        Returns:
            True if successfully killed
        """
        client = await self._get_client()
        try:
            response = await client.delete(f"/v1/sandboxes/{sandbox_id}")
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"Failed to kill sandbox {sandbox_id}: {e}")
            return False

    # ──────────────────────────────────────────────────────────
    # File Operations
    # ──────────────────────────────────────────────────────────

    async def write_file(
        self, sandbox_id: str, path: str, content: str
    ) -> None:
        """Write a file into the sandbox filesystem

        Args:
            sandbox_id: Target sandbox
            path: File path inside sandbox
            content: File content (text)
        """
        client = await self._get_client()
        response = await client.post(
            f"/v1/sandboxes/{sandbox_id}/files",
            json={"path": path, "content": content, "is_base64": False},
        )
        response.raise_for_status()

    async def read_file(self, sandbox_id: str, path: str) -> str:
        """Read a file from the sandbox filesystem"""
        client = await self._get_client()
        response = await client.get(
            f"/v1/sandboxes/{sandbox_id}/files",
            params={"path": path},
        )
        response.raise_for_status()
        return response.json().get("content", "")

    # ──────────────────────────────────────────────────────────
    # Command Execution
    # ──────────────────────────────────────────────────────────

    async def run_command(
        self, sandbox_id: str, cmd: str, timeout: int = 300, cwd: str = "/app"
    ) -> dict:
        """Run a command in the sandbox (blocking)

        Args:
            sandbox_id: Target sandbox
            cmd: Command to execute
            timeout: Command timeout in seconds
            cwd: Working directory

        Returns:
            CommandResult dict with stdout, stderr, exit_code
        """
        client = await self._get_client()
        response = await client.post(
            f"/v1/sandboxes/{sandbox_id}/commands",
            json={"cmd": cmd, "timeout": timeout, "cwd": cwd},
        )
        response.raise_for_status()
        return response.json()

    async def run_command_stream(
        self, sandbox_id: str, cmd: str, timeout: int = 300, cwd: str = "/app"
    ) -> AsyncGenerator[dict, None]:
        """Run a command with streaming SSE output

        Args:
            sandbox_id: Target sandbox
            cmd: Command to execute
            timeout: Command timeout
            cwd: Working directory

        Yields:
            Event dicts from the command output stream
        """
        client = await self._get_client()

        async with client.stream(
            "POST",
            f"/v1/sandboxes/{sandbox_id}/commands/stream",
            json={"cmd": cmd, "timeout": timeout, "cwd": cwd},
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    try:
                        yield json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

    # ──────────────────────────────────────────────────────────
    # Agent Execution (High-Level)
    # ──────────────────────────────────────────────────────────

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
        """Run an Agent inside a sandbox

        Creates a sandbox, writes the run config, executes the entrypoint,
        and streams back events. Cleans up the sandbox when done.

        Args:
            agent_config: Serialized agent configuration (tools, prompts, etc.)
            model_config: LLM provider config (model_name, api_key, api_base, etc.)
            message: User message
            context: Context dict (history, knowledge, etc.)
            workspace_id: Workspace ID
            user_id: User ID
            conversation_id: Conversation ID
            execution_id: Optional execution ID (generated if not provided)
            timeout: Sandbox/execution timeout

        Yields:
            Event dicts from the sandbox runtime
        """
        execution_id = execution_id or str(uuid.uuid4())
        sandbox_timeout = timeout or self.default_timeout
        sandbox_id = None
        used_warm = False

        try:
            # 1. Try warm pool first (instant, no startup cost)
            warm_id = await self._get_warm_sandbox()
            if warm_id:
                sandbox_id = warm_id
                used_warm = True
                # Inject env vars via command
                env_vars = {
                    "LLM_API_KEY": model_config.get("api_key", ""),
                    "LLM_API_BASE": model_config.get("api_base", ""),
                    "LLM_MODEL_NAME": model_config.get("model_name", ""),
                    "LLM_PROVIDER": model_config.get("provider", "openai"),
                    "CALLBACK_URL": settings.E2B_CALLBACK_URL,
                    "CALLBACK_SECRET": settings.E2B_CALLBACK_SECRET,
                    "WORKSPACE_ID": workspace_id,
                    "USER_ID": user_id,
                    "EXECUTION_ID": execution_id,
                    "CONVERSATION_ID": conversation_id,
                    "MAX_EXECUTION_TIME": str(sandbox_timeout),
                }
            else:
                # Cold start: create new sandbox
                env_vars = {
                    "LLM_API_KEY": model_config.get("api_key", ""),
                    "LLM_API_BASE": model_config.get("api_base", ""),
                    "LLM_MODEL_NAME": model_config.get("model_name", ""),
                    "LLM_PROVIDER": model_config.get("provider", "openai"),
                    "CALLBACK_URL": settings.E2B_CALLBACK_URL,
                    "CALLBACK_SECRET": settings.E2B_CALLBACK_SECRET,
                    "WORKSPACE_ID": workspace_id,
                    "USER_ID": user_id,
                    "EXECUTION_ID": execution_id,
                    "CONVERSATION_ID": conversation_id,
                    "MAX_EXECUTION_TIME": str(sandbox_timeout),
                }

                sandbox_info = await self.create_sandbox(
                    env_vars=env_vars,
                    timeout=sandbox_timeout + 30,
                    metadata={
                        "type": "agent",
                        "execution_id": execution_id,
                        "workspace_id": workspace_id,
                    },
                )
                sandbox_id = sandbox_info["sandbox_id"]

                # 2. Wait for sandbox to be ready
                if not await self.wait_for_sandbox_ready(sandbox_id):
                    raise RuntimeError("Sandbox failed to become ready within timeout")

            # 3. Write run config
            run_config = {
                "type": "agent_stream",
                "timeout": sandbox_timeout,
                "agent_config": agent_config,
                "model_config": model_config,
                "message": message,
                "context": context,
            }
            await self.write_file(
                sandbox_id, "/app/run_config.json", json.dumps(run_config, ensure_ascii=False)
            )

            # 4. Execute entrypoint and stream events
            # For warm sandbox, pass env vars inline since container is already running
            env_export = " ".join(f"{k}='{v}'" for k, v in env_vars.items()) if used_warm else ""
            cmd = f"PYTHONPATH=/app {env_export} python -m runtime.entrypoint --config /app/run_config.json --stream"

            async for event in self.run_command_stream(
                sandbox_id, cmd, timeout=sandbox_timeout
            ):
                # Parse stdout events from the sandbox
                event_type = event.get("type")
                if event_type == "stdout":
                    line = event.get("data", "")
                    if line.strip():
                        try:
                            parsed = json.loads(line)
                            yield parsed
                        except json.JSONDecodeError:
                            logger.debug(f"Non-JSON stdout: {line[:100]}")
                elif event_type == "stderr":
                    logger.warning(f"Sandbox stderr: {event.get('data', '')}")
                elif event_type == "exit":
                    exit_code = event.get("exit_code", -1)
                    if exit_code != 0:
                        logger.warning(f"Sandbox exited with code {exit_code}")
                    break

        except Exception as e:
            logger.error(f"Agent sandbox execution failed: {e}", exc_info=True)
            yield {
                "event": "execution_error",
                "data": {"error": str(e), "error_type": type(e).__name__},
                "timestamp": time.time(),
            }
        finally:
            # 5. Cleanup sandbox
            if sandbox_id:
                await self.kill_sandbox(sandbox_id)

    # ──────────────────────────────────────────────────────────
    # Workflow Execution (High-Level)
    # ──────────────────────────────────────────────────────────

    async def run_workflow(
        self,
        *,
        workflow_config: dict,
        input_data: dict,
        execution_context: dict,
        model_config: dict | None = None,
        timeout: int | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Run a Workflow inside a sandbox

        Creates a sandbox, writes config, executes workflow, streams events.

        Args:
            workflow_config: Full workflow configuration
            input_data: Input data for the workflow
            execution_context: Execution context (IDs, storage type, etc.)
            model_config: Optional default model config for LLM nodes
            timeout: Sandbox/execution timeout

        Yields:
            Event dicts from the sandbox workflow runtime
        """
        execution_id = execution_context.get("execution_id", str(uuid.uuid4()))
        sandbox_timeout = timeout or self.default_timeout
        sandbox_id = None

        try:
            env_vars = {
                "CALLBACK_URL": settings.E2B_CALLBACK_URL,
                "CALLBACK_SECRET": settings.E2B_CALLBACK_SECRET,
                "WORKSPACE_ID": execution_context.get("workspace_id", ""),
                "USER_ID": execution_context.get("user_id", ""),
                "EXECUTION_ID": execution_id,
                "CONVERSATION_ID": execution_context.get("conversation_id", ""),
                "MAX_EXECUTION_TIME": str(sandbox_timeout),
            }

            # Add model config if available
            if model_config:
                env_vars.update({
                    "LLM_API_KEY": model_config.get("api_key", ""),
                    "LLM_API_BASE": model_config.get("api_base", ""),
                    "LLM_MODEL_NAME": model_config.get("model_name", ""),
                    "LLM_PROVIDER": model_config.get("provider", "openai"),
                })

            sandbox_info = await self.create_sandbox(
                env_vars=env_vars,
                timeout=sandbox_timeout + 30,
                metadata={
                    "type": "workflow",
                    "execution_id": execution_id,
                    "workspace_id": execution_context.get("workspace_id", ""),
                },
            )
            sandbox_id = sandbox_info["sandbox_id"]

            if not await self.wait_for_sandbox_ready(sandbox_id):
                raise RuntimeError("Sandbox failed to become ready")

            run_config = {
                "type": "workflow_stream",
                "timeout": sandbox_timeout,
                "workflow_config": workflow_config,
                "input_data": input_data,
                "execution_context": execution_context,
            }
            await self.write_file(
                sandbox_id, "/app/run_config.json", json.dumps(run_config, ensure_ascii=False, default=str)
            )

            cmd = "PYTHONPATH=/app python -m runtime.entrypoint --config /app/run_config.json --stream"

            async for event in self.run_command_stream(
                sandbox_id, cmd, timeout=sandbox_timeout
            ):
                event_type = event.get("type")
                if event_type == "stdout":
                    line = event.get("data", "")
                    if line.strip():
                        try:
                            parsed = json.loads(line)
                            yield parsed
                        except json.JSONDecodeError:
                            pass
                elif event_type == "stderr":
                    logger.warning(f"Sandbox stderr: {event.get('data', '')}")
                elif event_type == "exit":
                    exit_code = event.get("exit_code", -1)
                    if exit_code != 0:
                        logger.warning(f"Workflow sandbox exited with code {exit_code}")
                    break

        except Exception as e:
            logger.error(f"Workflow sandbox execution failed: {e}", exc_info=True)
            yield {
                "event": "execution_error",
                "data": {"error": str(e), "error_type": type(e).__name__},
                "timestamp": time.time(),
            }
        finally:
            if sandbox_id:
                await self.kill_sandbox(sandbox_id)

    # ──────────────────────────────────────────────────────────
    # Utility
    # ──────────────────────────────────────────────────────────

    async def health_check(self) -> dict:
        """Check orchestrator health"""
        try:
            client = await self._get_client()
            response = await client.get("/health")
            return response.json()
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}
