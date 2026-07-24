"""
E2B Workflow Executor - Drop-in replacement for execute_workflow / execute_workflow_stream

当 E2B_ENABLED=true 时，替代原有的 in-process workflow 执行逻辑，
将 workflow 执行委托给 E2B sandbox。

接口与 executor.py 中的 execute_workflow / execute_workflow_stream 完全一致，
使得调用方（workflow_service.py）可以无缝切换。

用法:
    from app.core.config import settings
    if settings.E2B_ENABLED:
        from app.core.workflow.e2b_executor import execute_workflow_stream
    else:
        from app.core.workflow.executor import execute_workflow_stream
"""
import logging
from typing import Any, AsyncGenerator

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)


async def execute_workflow(
    workflow_config: dict[str, Any],
    input_data: dict[str, Any],
    execution_id: str,
    workspace_id: str,
    user_id: str,
    memory_storage_type: str,
    user_rag_memory_id: str,
) -> dict[str, Any]:
    """Execute a workflow in E2B sandbox (non-streaming)

    Same signature as app.core.workflow.executor.execute_workflow
    """
    result = None
    async for event in execute_workflow_stream(
        workflow_config=workflow_config,
        input_data=input_data,
        execution_id=execution_id,
        workspace_id=workspace_id,
        user_id=user_id,
        memory_storage_type=memory_storage_type,
        user_rag_memory_id=user_rag_memory_id,
    ):
        if event.get("event") == "workflow_end":
            result = event.get("data")
    return result or {"error": "Workflow did not complete"}


async def execute_workflow_stream(
    workflow_config: dict[str, Any],
    input_data: dict[str, Any],
    execution_id: str,
    workspace_id: str,
    user_id: str,
    memory_storage_type: str,
    user_rag_memory_id: str,
):
    """Execute a workflow in E2B sandbox (streaming)

    Same signature and yield format as app.core.workflow.executor.execute_workflow_stream

    Yields:
        dict: Streaming events: workflow_start, node_start, node_end, node_chunk, workflow_end
    """
    from app.services.e2b_sandbox_service import get_sandbox_service

    sandbox_service = get_sandbox_service()

    execution_context = {
        "execution_id": execution_id,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "conversation_id": input_data.get("conversation_id", ""),
        "memory_storage_type": memory_storage_type,
        "user_rag_memory_id": user_rag_memory_id,
    }

    # Determine model config for LLM nodes (if available in workflow)
    model_config = _extract_default_model_config(workflow_config)

    logger.info(
        "Routing workflow execution to E2B sandbox",
        extra={
            "execution_id": execution_id,
            "workspace_id": workspace_id,
        },
    )

    try:
        async for event in sandbox_service.run_workflow(
            workflow_config=workflow_config,
            input_data=input_data,
            execution_context=execution_context,
            model_config=model_config,
            timeout=settings.E2B_SANDBOX_TIMEOUT,
        ):
            event_type = event.get("event", "")
            data = event.get("data", {})

            # Re-emit in the same format as WorkflowExecutor.execute_stream
            if event_type in (
                "workflow_start", "workflow_end",
                "node_start", "node_end", "node_chunk",
                "message",
            ):
                yield {"event": event_type, "data": data}

            elif event_type == "execution_end":
                yield {"event": "workflow_end", "data": data.get("result", data)}

            elif event_type == "execution_error":
                yield {
                    "event": "workflow_end",
                    "data": {
                        "status": "failed",
                        "error": data.get("error", "Sandbox execution error"),
                    },
                }

            else:
                # Pass through any other events
                yield {"event": event_type, "data": data}

    except Exception as e:
        logger.error(f"E2B workflow execution failed: {e}", exc_info=True)
        yield {
            "event": "workflow_end",
            "data": {
                "status": "failed",
                "error": str(e),
            },
        }


def _extract_default_model_config(workflow_config: dict) -> dict | None:
    """Extract a default model config from workflow for sandbox LLM nodes

    Looks for the first LLM node in the workflow and extracts its model config.
    The sandbox will use this as the default LLM configuration.
    """
    nodes = workflow_config.get("nodes", [])
    for node in nodes:
        if node.get("type") in ("llm", "agent"):
            config = node.get("config", {})
            model_id = config.get("model_id")
            if model_id:
                # Note: In production, you'd resolve the model_id to actual credentials
                # For now, this is handled by the sandbox's callback mechanism
                return {"model_id": model_id}
    return None
