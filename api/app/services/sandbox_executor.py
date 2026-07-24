"""
Shared sandbox execution utilities.

Used by both AgentRunService (draft_run / draft_run_compare) and
AppChatService (public_share.chat / app_api.chat) to route agent
execution to the E2B sandbox when E2B_ENABLED is true.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, AsyncGenerator, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Tool serialization
# ──────────────────────────────────────────────────────────────

def serialize_tools_for_sandbox(*, tools: list) -> list[dict]:
    """Serialize already-loaded tool instances for sandbox consumption.

    Iterates the in-process tool list and produces a flat list of tool
    descriptors that the sandbox's tool loader (runtime.tools.loader)
    understands.  Uses the same names / descriptions / parameters as the
    in-process path, so tool_start events and LLM behaviour are identical.
    """
    from app.core.tools.langchain_adapter import LangchainToolWrapper

    serialized: list[dict] = []

    for tool in tools:
        meta = getattr(tool, "_tool_meta", None) or {}
        meta_type = meta.get("tool_type", "")

        # ── Knowledge retrieval ──────────────────────────
        if meta_type == "knowledge_retrieval":
            sources = meta.get("sources", [])
            kb_ids = [s["id"] for s in sources if s.get("id")]
            serialized.append({
                "name": tool.name,
                "description": tool.description or "",
                "type": "knowledge_retrieval",
                "tool_id": None,
                "kb_ids": kb_ids,
                "config": {
                    "kb_ids": kb_ids,
                    "top_k": getattr(tool, "top_k", 3),
                    "score_threshold": getattr(tool, "score_threshold", 0.7),
                },
            })
            continue

        # ── Memory (long-term) ────────────────────────────
        if meta_type == "long_term_memory":
            sources = meta.get("sources", [])
            config_id = sources[0]["id"] if sources else None
            serialized.append({
                "name": "memory_read",
                "description": "Read user's long-term memories",
                "type": "memory_read",
                "tool_id": None,
                "config": {"config_id": config_id},
            })
            serialized.append({
                "name": "memory_write",
                "description": "Save information to user's long-term memory",
                "type": "memory_write",
                "tool_id": None,
                "config": {"config_id": config_id},
            })
            continue

        # ── Web search (via callback, same as in-process Search()) ─
        if meta_type == "web_search":
            serialized.append({
                "name": tool.name,
                "description": tool.description or "",
                "type": "builtin",
                "tool_id": None,
                "config": {},
            })
            continue

        # ── Skill ─────────────────────────────────────────
        if meta_type == "skill":
            sources = meta.get("sources", [])
            skill_id = sources[0]["id"] if sources else None
            serialized.append({
                "name": tool.name,
                "description": tool.description or "",
                "type": "skill",
                "tool_id": skill_id,
                "config": {
                    "skill_id": skill_id,
                },
            })
            continue

        # ── Builtin / Custom / MCP (LangchainToolWrapper) ─
        if isinstance(tool, LangchainToolWrapper):
            ti = tool.tool_instance
            tool_type = ti.tool_type.value if hasattr(ti.tool_type, "value") else str(ti.tool_type)
            config_data = dict(ti.config) if hasattr(ti, "config") and ti.config else {}

            # Include parameters so sandbox can rebuild the args_schema
            if hasattr(ti, "parameters") and ti.parameters:
                props = {}
                required: list[str] = []
                for p in ti.parameters:
                    if hasattr(p, "model_dump"):
                        pd = p.model_dump()
                    elif isinstance(p, dict):
                        pd = p
                    else:
                        continue
                    pname = pd.get("name", "")
                    if not pname:
                        continue
                    props[pname] = {
                        "type": pd.get("type", "string"),
                        "description": pd.get("description", ""),
                    }
                    if pd.get("required"):
                        required.append(pname)
                    if pd.get("default") is not None:
                        props[pname]["default"] = pd.get("default")
                    if pd.get("enum"):
                        props[pname]["enum"] = pd.get("enum")
                config_data["parameters"] = {"properties": props, "required": required}

            # Operation may be on the wrapper (custom tools) or baked into
            # the inner instance (builtin tools via OperationTool).
            operation = tool.operation
            if operation is None and hasattr(ti, "operation"):
                operation = ti.operation

            serialized.append({
                "name": tool.name,
                "description": tool.description or "",
                "type": tool_type,
                "tool_id": ti.tool_id if hasattr(ti, "tool_id") else None,
                "config": config_data,
                "operation": operation,
            })
            continue

        # ── Fallback (plain LangChain tool) ───────────────
        serialized.append({
            "name": tool.name,
            "description": tool.description or "",
            "type": meta_type or "builtin",
            "tool_id": None,
            "config": {},
        })

    return serialized


# ──────────────────────────────────────────────────────────────
# Payload builder
# ──────────────────────────────────────────────────────────────

async def build_sandbox_payload(
    *,
    agent_config: Any,
    model_config: Any = None,
    api_key_config: dict,
    effective_params: dict,
    message: str,
    system_prompt: str,
    workspace_id,
    user_id: Optional[str],
    conversation_id: Optional[str],
    tools: list,
    history: list | None = None,
    context: str | None = None,
    variables: dict | None = None,
    files_config: dict | None = None,
) -> dict:
    """Build a rich snapshot payload for E2B sandbox execution.

    Serializes the already-loaded tool instances directly from ``tools``
    so names / descriptions / parameters match the in-process path exactly.

    ``model_config`` is optional — AppChatService does not have a separate
    ModelConfig object and passes None.
    """
    serialized_tools = serialize_tools_for_sandbox(tools=tools)

    sandbox_agent_config = {
        "system_prompt": system_prompt,
        "tools": serialized_tools,
        "max_iterations": getattr(agent_config, "max_iterations", None),
        "strategy": getattr(agent_config, "strategy", "react"),
        "tool_call_limit": getattr(agent_config, "tool_call_limit", 1),
    }

    sandbox_model_config = {
        "model_name": api_key_config.get("model_name", ""),
        "api_key": api_key_config.get("api_key", ""),
        "api_base": api_key_config.get("api_base", ""),
        "provider": api_key_config.get("provider", "openai"),
        "temperature": effective_params.get("temperature", 0.7),
        "max_tokens": effective_params.get("max_tokens", 2000),
        "top_p": effective_params.get("top_p"),
        "top_k": effective_params.get("top_k"),
        "seed": effective_params.get("seed"),
        "stop": effective_params.get("stop"),
        "repetition_penalty": effective_params.get("repetition_penalty"),
        "frequency_penalty": effective_params.get("frequency_penalty"),
        "presence_penalty": effective_params.get("presence_penalty"),
        "deep_thinking": effective_params.get("deep_thinking", False),
        "thinking_budget_tokens": effective_params.get("thinking_budget_tokens"),
        "json_output": effective_params.get("json_output", False),
        "enable_search": effective_params.get("enable_search", False),
        "is_omni": api_key_config.get("is_omni", False),
        "capability": api_key_config.get("capability") or [],
        "extra_headers": getattr(model_config, "extra_headers", None) if model_config is not None else None,
        "concurrency": getattr(model_config, "concurrency", 5) if model_config is not None else 5,
    }

    sandbox_context = {
        "history": history or [],
        "knowledge": context or "",
        "variables": variables or {},
    }

    return {
        "type": "agent_stream",
        "agent_config": sandbox_agent_config,
        "model_config": sandbox_model_config,
        "message": message,
        "context": sandbox_context,
        "runtime_env": {
            "callback_url": settings.E2B_CALLBACK_URL,
            "callback_secret": settings.E2B_CALLBACK_SECRET,
            "workspace_id": str(workspace_id),
            "user_id": user_id or "",
            "execution_id": str(uuid.uuid4()),
            "conversation_id": str(conversation_id or ""),
        },
    }


# ──────────────────────────────────────────────────────────────
# Sandbox stream runner
# ──────────────────────────────────────────────────────────────

async def run_sandbox_stream(
    *,
    payload: dict,
    workspace_id: str,
    user_id: str,
    conversation_id: str,
    adapter: Any = None,
) -> AsyncGenerator[Any, None]:
    """Stream execution events from E2B sandbox.

    Translates sandbox protocol events into chunks compatible with
    agent.chat_stream() output format (str|int|dict).

    Returns the adapter as a second value so callers can retrieve
    _sandbox_citations after streaming completes.
    """
    from app.services.e2b_sandbox_service import get_sandbox_service

    sandbox_service = get_sandbox_service()

    async for event in sandbox_service.run_agent(
        agent_config=payload["agent_config"],
        model_config=payload["model_config"],
        message=payload["message"],
        context=payload["context"],
        workspace_id=workspace_id,
        user_id=user_id,
        conversation_id=conversation_id,
        execution_id=payload["runtime_env"]["execution_id"],
    ):
        if adapter is not None:
            chunk = adapter._translate_event_to_chunk(event)
        else:
            chunk = event
        if chunk is not None:
            yield chunk
