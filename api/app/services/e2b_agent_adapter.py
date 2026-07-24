"""
E2B Agent Execution Adapter

当 E2B_ENABLED=true 时，将 Agent/Workflow 的执行委托给 E2B sandbox。
提供与原有 in-process 执行相同的接口，使调用方无感切换。

使用方式：
    在 AgentRunService 的 run() 和 run_stream() 开头检查 E2B 开关：
        if settings.E2B_ENABLED:
            return self._e2b_adapter.run_stream(...)
"""
import json
import logging
import time
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class E2BAgentAdapter:
    """Adapter that routes Agent execution to E2B sandbox"""

    def __init__(self, db: Session):
        self.db = db
        self._sandbox_citations: list[dict] = []
        from app.services.e2b_sandbox_service import get_sandbox_service
        self.sandbox_service = get_sandbox_service()

    async def run_stream(
        self,
        *,
        agent_config: Any,
        model_config: Any,
        api_key_config: dict,
        message: str,
        workspace_id: str,
        user_id: str,
        conversation_id: str = "",
        system_prompt: str = "",
        tools_serialized: list[dict] | None = None,
        history: list[dict] | None = None,
        context: str = "",
        variables: dict | None = None,
    ) -> AsyncGenerator[str, None]:
        """Execute Agent in E2B sandbox with streaming output

        Translates sandbox events into SSE events compatible with the
        existing frontend protocol.

        Args:
            agent_config: Agent config object
            model_config: Model config object
            api_key_config: Dict with api_key, model_name, provider, api_base
            message: User message
            workspace_id: Workspace ID
            user_id: User ID
            conversation_id: Conversation ID
            system_prompt: Rendered system prompt
            tools_serialized: Pre-serialized tool configs for sandbox
            history: Conversation history
            context: Knowledge context string
            variables: Template variables

        Yields:
            SSE-formatted event strings
        """
        execution_id = str(uuid.uuid4())
        start_time = time.time()

        # Build sandbox-compatible configs
        sandbox_agent_config = {
            "system_prompt": system_prompt or getattr(agent_config, "system_prompt", ""),
            "tools": tools_serialized or self._serialize_tools(agent_config),
            "max_iterations": getattr(agent_config, "max_iterations", None),
            "strategy": getattr(agent_config, "strategy", "react"),
            "tool_call_limit": getattr(agent_config, "tool_call_limit", 1),
        }

        sandbox_model_config = {
            "model_name": api_key_config.get("model_name", ""),
            "api_key": api_key_config.get("api_key", ""),
            "api_base": api_key_config.get("api_base", ""),
            "provider": api_key_config.get("provider", "openai"),
            "temperature": getattr(model_config, "temperature", 0.7),
            "max_tokens": getattr(model_config, "max_tokens", 2000),
            "top_p": getattr(model_config, "top_p", None),
            "top_k": getattr(model_config, "top_k", None),
            "seed": getattr(model_config, "seed", None),
            "stop": getattr(model_config, "stop", None),
            "repetition_penalty": getattr(model_config, "repetition_penalty", None),
            "frequency_penalty": getattr(model_config, "frequency_penalty", None),
            "presence_penalty": getattr(model_config, "presence_penalty", None),
            "deep_thinking": getattr(model_config, "deep_thinking", False),
            "thinking_budget_tokens": getattr(model_config, "thinking_budget_tokens", None),
            "json_output": getattr(model_config, "json_output", False),
            "enable_search": getattr(model_config, "enable_search", False),
            "is_omni": getattr(model_config, "is_omni", False),
            "capability": getattr(model_config, "capability", None) or [],
            "extra_headers": getattr(model_config, "extra_headers", None),
            "concurrency": getattr(model_config, "concurrency", 5),
        }

        sandbox_context = {
            "history": history or [],
            "knowledge": context,
            "variables": variables or {},
        }

        logger.info(
            "Routing agent execution to E2B sandbox",
            extra={
                "execution_id": execution_id,
                "model": sandbox_model_config["model_name"],
                "workspace_id": workspace_id,
            },
        )

        try:
            async for event in self.sandbox_service.run_agent(
                agent_config=sandbox_agent_config,
                model_config=sandbox_model_config,
                message=message,
                context=sandbox_context,
                workspace_id=workspace_id,
                user_id=user_id,
                conversation_id=conversation_id,
                execution_id=execution_id,
            ):
                # Translate sandbox events → SSE events
                sse_event = self._translate_event_to_sse(event)
                if sse_event:
                    yield sse_event

        except Exception as e:
            logger.error(f"E2B agent execution failed: {e}", exc_info=True)
            yield self._format_sse("error", {
                "message": str(e),
                "type": type(e).__name__,
            })

        finally:
            elapsed = time.time() - start_time
            logger.info(
                "E2B agent execution completed",
                extra={"execution_id": execution_id, "elapsed": elapsed},
            )

    async def run(
        self,
        *,
        agent_config: Any,
        model_config: Any,
        api_key_config: dict,
        message: str,
        workspace_id: str,
        user_id: str,
        conversation_id: str = "",
        system_prompt: str = "",
        tools_serialized: list[dict] | None = None,
        history: list[dict] | None = None,
        context: str = "",
        variables: dict | None = None,
    ) -> dict:
        """Execute Agent in E2B sandbox (non-streaming)

        Collects all streaming events and returns the final result.

        Returns:
            Dict with content, node_executions, usage, etc.
        """
        content = ""
        node_executions = []

        async for sse_event in self.run_stream(
            agent_config=agent_config,
            model_config=model_config,
            api_key_config=api_key_config,
            message=message,
            workspace_id=workspace_id,
            user_id=user_id,
            conversation_id=conversation_id,
            system_prompt=system_prompt,
            tools_serialized=tools_serialized,
            history=history,
            context=context,
            variables=variables,
        ):
            # Parse SSE to accumulate result
            # Format: "event: {type}\ndata: {json}\n\n"
            try:
                if sse_event.startswith("event: message\n"):
                    data_line = sse_event.split("\ndata: ", 1)[1].rstrip("\n")
                    data = json.loads(data_line)
                    content += data.get("content", "")
                elif sse_event.startswith("event: end\n"):
                    data_line = sse_event.split("\ndata: ", 1)[1].rstrip("\n")
                    data = json.loads(data_line)
                    content = data.get("message", content)
            except (json.JSONDecodeError, KeyError, IndexError):
                pass

        return {
            "content": content,
            "node_executions": node_executions,
            "usage": {},
            "execution_id": str(uuid.uuid4()),
        }

    # ──────────────────────────────────────────────────────────
    # Event Translation
    # ──────────────────────────────────────────────────────────

    def _translate_event_to_sse(self, event: dict) -> Optional[str]:
        """Translate sandbox protocol event → SSE event string

        Maps sandbox events to the existing frontend SSE protocol.
        Uses format: event: {type}\ndata: {json}\n\n
        """
        event_type = event.get("event", "")
        data = event.get("data", {})

        if event_type == "execution_start":
            return self._format_sse("start", {
                "conversation_id": "",
                "message_id": "",
                "timestamp": event.get("timestamp", time.time()),
            })

        elif event_type == "agent_chunk":
            return self._format_sse("message", {
                "content": data.get("content", ""),
            })

        elif event_type == "agent_tool_start":
            return self._format_sse("tool_start", {
                "step_id": data.get("step_id", ""),
                "name": data.get("tool_name", ""),
                "input": data.get("tool_input"),
            })

        elif event_type == "agent_tool_end":
            return self._format_sse("tool_end", {
                "step_id": data.get("step_id", ""),
                "name": data.get("tool_name", ""),
                "output": data.get("tool_output", ""),
            })

        elif event_type == "agent_tool_error":
            return self._format_sse("tool_error", {
                "step_id": data.get("step_id", ""),
                "name": data.get("tool_name", ""),
                "error": data.get("error", ""),
            })

        elif event_type == "execution_end":
            result = data.get("result", {})
            content = result.get("content", "")
            return self._format_sse("end", {
                "message": content,
                "answer": content,
                "usage": {},
                "elapsed_time": result.get("elapsed_time", 0),
                "message_length": len(content),
            })

        elif event_type == "execution_error":
            return self._format_sse("error", {
                "message": data.get("error", "Unknown error"),
                "type": data.get("error_type", "RuntimeError"),
            })

        elif event_type == "agent_thinking":
            return self._format_sse("reasoning", {
                "content": data.get("content", ""),
            })

        elif event_type == "agent_log":
            # Match in-process format: {"type": "agent_log", "data": <trace>}
            trace = data.get("trace", data)
            return f"event: agent_log\ndata: {json.dumps({'type': 'agent_log', 'data': trace}, ensure_ascii=False)}\n\n"

        elif event_type == "agent_log_final":
            # Match in-process format: {"type": "agent_log_final", "data": <trace>}
            trace = data.get("trace", data)
            return f"event: agent_log_final\ndata: {json.dumps({'type': 'agent_log_final', 'data': trace}, ensure_ascii=False)}\n\n"

        # Pass through other events
        elif event_type:
            return self._format_sse(event_type, data)

        return None

    # ──────────────────────────────────────────────────────────
    # Chunk Translation (for unified streaming loop)
    # ──────────────────────────────────────────────────────────

    def _translate_event_to_chunk(self, event: dict):
        """Translate sandbox protocol event → chunk compatible with agent.chat_stream()

        Returns one of:
            str          → content chunk (treated as message content)
            int          → total_tokens
            dict         → typed event {"type": "reasoning|tool_start|tool_end|...", ...}
            None         → skip this event

        This enables the unified streaming loop in AgentRunService.run_stream()
        to handle sandbox and in-process chunks identically.
        """
        event_type = event.get("event", "")
        data = event.get("data", {})

        if event_type == "agent_chunk":
            return data.get("content", "")

        elif event_type == "agent_thinking":
            return {"type": "reasoning", "content": data.get("content", "")}

        elif event_type == "agent_tool_start":
            return {
                "type": "tool_start",
                "step_id": data.get("step_id", ""),
                "name": data.get("tool_name", ""),
                "input": data.get("tool_input"),
            }

        elif event_type == "agent_tool_end":
            tool_name = data.get("tool_name", "")
            tool_output = data.get("tool_output", "")
            # Collect KB citations
            if tool_name == "knowledge_retrieval" and isinstance(tool_output, str):
                self._collect_kb_citations(tool_output)
            return {
                "type": "tool_end",
                "step_id": data.get("step_id", ""),
                "name": tool_name,
                "output": tool_output,
            }

        elif event_type == "agent_tool_error":
            return {
                "type": "tool_error",
                "step_id": data.get("step_id", ""),
                "name": data.get("tool_name", ""),
                "error": data.get("error", ""),
            }

        elif event_type == "agent_log":
            return {
                "type": "agent_log",
                "data": data.get("trace", data),
            }

        elif event_type == "agent_log_final":
            return {
                "type": "agent_log_final",
                "data": data.get("trace", data),
            }

        elif event_type == "execution_end":
            result = data.get("result", {})
            usage = result.get("usage", {})
            # Collect citations passed back from sandbox
            sandbox_citations = result.get("citations", [])
            if sandbox_citations:
                self._sandbox_citations.extend(sandbox_citations)
            total_tokens = usage.get("total_tokens", 0)
            return total_tokens if isinstance(total_tokens, int) else 0

        elif event_type == "execution_error":
            error_msg = data.get("error", "Sandbox execution failed")
            error_type = data.get("error_type", "RuntimeError")
            raise RuntimeError(f"[Sandbox] {error_type}: {error_msg}")

        elif event_type == "execution_start":
            return None  # Handled separately by SSE start event

        # Pass through other events as dicts
        elif event_type:
            return {"type": event_type, **data}

        return None

    def _collect_kb_citations(self, tool_output: str):
        """Attempt to parse KB citations from tool output text.

        The sandbox's KnowledgeRetrievalTool includes source info in its
        output. Citations are also collected from execution_end result.
        """
        # Citations are primarily collected from execution_end.
        # This is a fallback for inline extraction.
        pass

    def _format_sse(self, event: str, data: dict) -> str:
        """Format as SSE event string matching AgentRunService._format_sse_event"""
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    # ──────────────────────────────────────────────────────────
    # Tool Serialization
    # ──────────────────────────────────────────────────────────

    def _serialize_tools(self, agent_config: Any) -> list[dict]:
        """Serialize agent tool configs for sandbox consumption

        Converts the agent_config.tools format into a flat list of
        tool descriptors that the sandbox's tool loader understands.
        """
        tools_config = getattr(agent_config, "tools", None)
        if not tools_config:
            return []

        serialized = []

        # Handle list format (newer)
        if isinstance(tools_config, list):
            for tc in tools_config:
                if isinstance(tc, dict):
                    serialized.append({
                        "name": tc.get("name", tc.get("tool_name", "unknown")),
                        "description": tc.get("description", ""),
                        "type": tc.get("type", tc.get("tool_type", "builtin")),
                        "tool_id": tc.get("tool_id", tc.get("id")),
                        "config": tc.get("config", {}),
                    })

        # Handle dict format (older)
        elif isinstance(tools_config, dict):
            for tool_type, tool_list in tools_config.items():
                if isinstance(tool_list, list):
                    for tc in tool_list:
                        if isinstance(tc, dict) and tc.get("enabled", True):
                            serialized.append({
                                "name": tc.get("name", tc.get("tool_name", "unknown")),
                                "description": tc.get("description", ""),
                                "type": tool_type,
                                "tool_id": tc.get("tool_id", tc.get("id")),
                                "config": tc.get("config", {}),
                            })

        return serialized
