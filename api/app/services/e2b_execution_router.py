"""
E2B Execution Router

决定执行路径：in-process 还是 E2B sandbox。
作为中间层插入到 Controller → Service 之间。

使用方式（在 controller 中）:
    from app.services.e2b_execution_router import E2BExecutionRouter

    router = E2BExecutionRouter(db)
    if router.should_use_sandbox(agent_config):
        # 走 sandbox 路径
        async for event in router.run_agent_stream(...):
            yield event
    else:
        # 原有 in-process 路径
        async for event in agent_run_service.run_stream(...):
            yield event
"""
import logging
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class E2BExecutionRouter:
    """Routes execution between in-process and E2B sandbox

    策略：
    - E2B_ENABLED=false → 全部 in-process（保持现有行为）
    - E2B_ENABLED=true → Agent 和 Workflow 走 sandbox
    - 可通过 agent_config 中的 sandbox_mode 字段覆盖单个 Agent 的行为
    """

    def __init__(self, db: Session):
        self.db = db
        self._adapter = None

    @property
    def adapter(self):
        """Lazy-load E2B adapter"""
        if self._adapter is None:
            from app.services.e2b_agent_adapter import E2BAgentAdapter
            self._adapter = E2BAgentAdapter(self.db)
        return self._adapter

    def should_use_sandbox(self, agent_config: Any = None) -> bool:
        """Determine if execution should go through E2B sandbox

        Args:
            agent_config: Optional agent config to check for overrides

        Returns:
            True if should use sandbox execution
        """
        # Global kill switch
        if not settings.E2B_ENABLED:
            return False

        # Per-agent override (allows opt-out)
        if agent_config:
            sandbox_mode = None
            if hasattr(agent_config, "sandbox_mode"):
                sandbox_mode = agent_config.sandbox_mode
            elif hasattr(agent_config, "execution_config"):
                exec_config = agent_config.execution_config or {}
                sandbox_mode = exec_config.get("sandbox_mode")

            if sandbox_mode == "disabled":
                return False
            if sandbox_mode == "enabled":
                return True

        return True

    def should_use_sandbox_for_workflow(self, workflow_config: dict = None) -> bool:
        """Determine if workflow should go through sandbox

        Args:
            workflow_config: Workflow configuration dict

        Returns:
            True if should use sandbox execution
        """
        if not settings.E2B_ENABLED:
            return False

        if workflow_config:
            exec_config = workflow_config.get("execution_config", {})
            sandbox_mode = exec_config.get("sandbox_mode")
            if sandbox_mode == "disabled":
                return False
            if sandbox_mode == "enabled":
                return True

        return True

    # ──────────────────────────────────────────────────────────
    # Agent Execution
    # ──────────────────────────────────────────────────────────

    async def run_agent_stream(
        self,
        *,
        agent_config: Any,
        model_config: Any,
        api_key_config: dict,
        message: str,
        workspace_id: uuid.UUID,
        user_id: str,
        conversation_id: str = "",
        system_prompt: str = "",
        tools_serialized: list[dict] | None = None,
        history: list[dict] | None = None,
        context: str = "",
        variables: dict | None = None,
    ) -> AsyncGenerator[str, None]:
        """Route agent streaming execution to E2B sandbox

        This is the main entry point for E2B agent execution.
        Returns SSE-formatted events compatible with the existing protocol.
        """
        async for event in self.adapter.run_stream(
            agent_config=agent_config,
            model_config=model_config,
            api_key_config=api_key_config,
            message=message,
            workspace_id=str(workspace_id),
            user_id=user_id or "",
            conversation_id=conversation_id,
            system_prompt=system_prompt,
            tools_serialized=tools_serialized,
            history=history,
            context=context,
            variables=variables,
        ):
            yield event

    async def run_agent(
        self,
        *,
        agent_config: Any,
        model_config: Any,
        api_key_config: dict,
        message: str,
        workspace_id: uuid.UUID,
        user_id: str,
        conversation_id: str = "",
        system_prompt: str = "",
        tools_serialized: list[dict] | None = None,
        history: list[dict] | None = None,
        context: str = "",
        variables: dict | None = None,
    ) -> dict:
        """Route agent non-streaming execution to E2B sandbox"""
        return await self.adapter.run(
            agent_config=agent_config,
            model_config=model_config,
            api_key_config=api_key_config,
            message=message,
            workspace_id=str(workspace_id),
            user_id=user_id or "",
            conversation_id=conversation_id,
            system_prompt=system_prompt,
            tools_serialized=tools_serialized,
            history=history,
            context=context,
            variables=variables,
        )

    # ──────────────────────────────────────────────────────────
    # Workflow Execution
    # ──────────────────────────────────────────────────────────

    async def run_workflow_stream(
        self,
        *,
        workflow_config: dict,
        input_data: dict,
        execution_id: str,
        workspace_id: str,
        user_id: str,
        conversation_id: str = "",
        memory_storage_type: str = "",
        user_rag_memory_id: str = "",
        model_config: dict | None = None,
        timeout: int | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Route workflow streaming execution to E2B sandbox

        Yields:
            Workflow event dicts (same format as WorkflowExecutor.execute_stream)
        """
        from app.services.e2b_sandbox_service import get_sandbox_service

        sandbox_service = get_sandbox_service()

        execution_context = {
            "execution_id": execution_id,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "conversation_id": conversation_id,
            "memory_storage_type": memory_storage_type,
            "user_rag_memory_id": user_rag_memory_id,
        }

        async for event in sandbox_service.run_workflow(
            workflow_config=workflow_config,
            input_data=input_data,
            execution_context=execution_context,
            model_config=model_config,
            timeout=timeout,
        ):
            # Translate sandbox protocol events back to workflow event format
            event_type = event.get("event", "")
            data = event.get("data", {})

            if event_type in ("workflow_start", "workflow_end", "node_start", "node_end", "node_chunk"):
                yield {"event": event_type, "data": data}
            elif event_type == "execution_end":
                yield {"event": "workflow_end", "data": data.get("result", data)}
            elif event_type == "execution_error":
                yield {"event": "workflow_end", "data": {
                    "status": "failed",
                    "error": data.get("error", "Unknown error"),
                }}
            else:
                # Pass through
                yield {"event": event_type, "data": data}
