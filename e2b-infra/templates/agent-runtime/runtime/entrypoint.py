"""
Sandbox Entrypoint - sandbox 内的主执行入口

接收 JSON 配置文件，运行 Agent 或 Workflow，通过 stdout JSON Lines 输出流式事件。

Usage:
    python -m runtime.entrypoint --config /app/run_config.json [--stream]
"""
import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("sandbox.entrypoint")


def _collect_citations(tools: list) -> list[dict]:
    """Collect citations from KnowledgeRetrievalTool instances after execution."""
    citations: list[dict] = []
    for tool in tools:
        if hasattr(tool, "get_citations"):
            citations.extend(tool.get_citations())
    return citations


def setup_timeout(timeout: int):
    """Set up execution timeout using SIGALRM"""
    def handler(signum, frame):
        from runtime.protocol import emitter
        emitter.emit_error("Execution timed out", "TimeoutError")
        sys.exit(124)
    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, handler)
        signal.alarm(timeout)


async def run_agent(config: dict, stream: bool = False):
    """Execute Agent in sandbox using LangChainAgent from runtime.core.agent"""
    from runtime.config import SandboxRuntimeConfig
    from runtime.callback_client import CallbackClient
    from runtime.protocol import emitter
    from runtime.tools.loader import load_tools
    from runtime.core.agent.langchain_agent import LangChainAgent

    runtime_config = SandboxRuntimeConfig.from_env()
    model_config = config.get("model_config", {})
    runtime_config.llm_api_key = model_config.get("api_key", runtime_config.llm_api_key)
    runtime_config.llm_api_base = model_config.get("api_base", runtime_config.llm_api_base)
    runtime_config.llm_model_name = model_config.get("model_name", runtime_config.llm_model_name)
    runtime_config.llm_provider = model_config.get("provider", runtime_config.llm_provider)

    runtime_env = config.get("runtime_env", {})
    if runtime_env:
        for key in ("callback_url", "callback_secret", "workspace_id", "user_id", "execution_id", "conversation_id"):
            val = runtime_env.get(key, "")
            if val:
                setattr(runtime_config, key, val)

    callback_client = CallbackClient(runtime_config)

    try:
        tool_configs = config.get("agent_config", {}).get("tools", [])
        tools = load_tools(tool_configs, callback_client)
        agent_config = config.get("agent_config", {})
        message = config.get("message", "")
        context = config.get("context", {})

        system_prompt = agent_config.get("system_prompt", "You are a helpful assistant.")
        strategy = agent_config.get("strategy", "react")
        tool_call_limit = agent_config.get("tool_call_limit", 1)

        emitter.emit_start("agent", runtime_config.execution_id)

        agent = LangChainAgent(
            model_name=model_config.get("model_name", ""),
            api_key=model_config.get("api_key", ""),
            provider=model_config.get("provider", "openai"),
            api_base=model_config.get("api_base") or None,
            temperature=model_config.get("temperature", 0.7),
            max_tokens=model_config.get("max_tokens", 2000),
            system_prompt=system_prompt,
            tools=tools,
            streaming=stream,
            top_p=model_config.get("top_p"),
            top_k=model_config.get("top_k"),
            seed=model_config.get("seed"),
            repetition_penalty=model_config.get("repetition_penalty"),
            frequency_penalty=model_config.get("frequency_penalty"),
            presence_penalty=model_config.get("presence_penalty"),
            enable_search=model_config.get("enable_search", False),
            stop=model_config.get("stop"),
            extra_headers=model_config.get("extra_headers"),
            strategy=strategy,
            deep_thinking=model_config.get("deep_thinking", False),
            thinking_budget_tokens=model_config.get("thinking_budget_tokens"),
            json_output=model_config.get("json_output", False),
            capability=model_config.get("capability") or [],
            tool_call_limit=tool_call_limit,
            is_omni=model_config.get("is_omni", False),
            max_iterations=agent_config.get("max_iterations"),
        )

        history = context.get("history", [])
        knowledge = context.get("knowledge", "")
        processed_files = context.get("processed_files")

        if stream:
            started_at = time.time()
            full_content = ""

            async for chunk in agent.chat_stream(
                message=message,
                history=history,
                context=knowledge,
                files=processed_files,
            ):
                if isinstance(chunk, str):
                    full_content += chunk
                    emitter.emit_agent_chunk(chunk)
                elif isinstance(chunk, dict):
                    typ = chunk.get("type")
                    if typ == "agent_log":
                        emitter.emit_agent_log(chunk["data"])
                    elif typ == "agent_log_final":
                        emitter.emit_agent_log_final(chunk["data"])
                    elif typ == "reasoning":
                        emitter.emit_agent_thinking(chunk["content"])
                    elif typ == "tool_start":
                        emitter.emit_agent_tool_start(
                            chunk["name"],
                            chunk.get("input"),
                            step_id=chunk.get("step_id", ""),
                        )
                    elif typ == "tool_end":
                        emitter.emit_agent_tool_end(
                            chunk["name"],
                            chunk.get("output"),
                            step_id=chunk.get("step_id", ""),
                        )
                    elif typ == "tool_error":
                        emitter.emit_agent_tool_error(
                            chunk["name"],
                            chunk.get("error", ""),
                            step_id=chunk.get("step_id", ""),
                        )
                elif isinstance(chunk, int):
                    pass

            elapsed = round(time.time() - started_at, 3)
            # Collect citations from knowledge retrieval tools
            citations = _collect_citations(tools)
            end_data: dict = {"content": full_content, "elapsed_time": elapsed}
            if citations:
                end_data["citations"] = citations
            emitter.emit_end(end_data)
        else:
            result = await agent.chat(
                message=message,
                history=history,
                context=knowledge,
                files=processed_files,
            )
            content = result.get("content", "")
            elapsed = result.get("elapsed_time", 0)
            agent_log = result.get("agent_log")
            if agent_log:
                emitter.emit_agent_log_final(agent_log)
            citations = _collect_citations(tools)
            end_data = {"content": content, "elapsed_time": elapsed}
            if citations:
                end_data["citations"] = citations
            emitter.emit_end(end_data)

    except Exception as e:
        logger.error(f"Agent execution failed: {e}", exc_info=True)
        emitter.emit_error(str(e), type(e).__name__)
    finally:
        await callback_client.close()


async def run_workflow(config: dict, stream: bool = False):
    """Execute Workflow in sandbox"""
    from runtime.protocol import emitter, EventType
    from runtime.config import SandboxRuntimeConfig

    runtime_config = SandboxRuntimeConfig.from_env()

    try:
        # Apply sandbox patches BEFORE importing workflow code
        import importlib.util
        spec = importlib.util.spec_from_file_location("sandbox_patches", "/app/stubs/sandbox_patches.py")
        sandbox_patches = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sandbox_patches)
        sandbox_patches.apply()

        workflow_config = config.get("workflow_config", {})
        input_data = config.get("input_data", {})
        execution_context = config.get("execution_context", {})

        emitter.emit_start("workflow", runtime_config.execution_id)

        # Now safe to import workflow executor (patches applied)
        from app.core.workflow.executor import WorkflowExecutor
        from app.core.workflow.engine.runtime_schema import ExecutionContext

        ctx = ExecutionContext.create(
            execution_id=execution_context.get("execution_id", runtime_config.execution_id),
            workspace_id=execution_context.get("workspace_id", runtime_config.workspace_id),
            user_id=execution_context.get("user_id", runtime_config.user_id),
            conversation_id=execution_context.get("conversation_id", ""),
            memory_storage_type=execution_context.get("memory_storage_type", ""),
            user_rag_memory_id=execution_context.get("user_rag_memory_id", ""),
        )

        executor = WorkflowExecutor(workflow_config=workflow_config, execution_context=ctx)

        async for event in executor.execute_stream(input_data):
            event_type = event.get("event", "unknown")
            event_data = event.get("data", {})
            if event_type == "workflow_start":
                emitter.emit(EventType.WORKFLOW_START, event_data)
            elif event_type == "workflow_end":
                emitter.emit(EventType.WORKFLOW_END, event_data)
                emitter.emit_end(event_data)
            elif event_type == "node_start":
                emitter.emit_node_start(event_data.get("node_id", ""), event_data.get("node_type", ""))
            elif event_type == "node_end":
                emitter.emit_node_end(event_data.get("node_id", ""), event_data.get("output"))
            elif event_type == "node_chunk":
                emitter.emit_node_chunk(event_data.get("node_id", ""), event_data.get("content", ""))
            elif event_type == "message":
                emitter.emit("message", event_data)
            else:
                emitter.emit(event_type, event_data)

    except Exception as e:
        logger.error(f"Workflow execution failed: {e}", exc_info=True)
        emitter.emit_error(str(e), type(e).__name__)


async def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="MemoryBear Sandbox Runtime")
    parser.add_argument("--config", required=True, help="Path to run config JSON")
    parser.add_argument("--stream", action="store_true", help="Enable streaming output")
    args = parser.parse_args()

    try:
        with open(args.config) as f:
            config = json.load(f)
    except Exception as e:
        from runtime.protocol import emitter
        emitter.emit_error(f"Failed to load config: {e}", "ConfigError")
        sys.exit(1)

    timeout = config.get("timeout", int(os.getenv("MAX_EXECUTION_TIME", "300")))
    setup_timeout(timeout)

    run_type = config.get("type", "agent")
    logger.info(f"Starting sandbox execution: type={run_type}, stream={args.stream}")

    if run_type in ("agent", "agent_stream"):
        await run_agent(config, stream=args.stream or run_type == "agent_stream")
    elif run_type in ("workflow", "workflow_stream"):
        await run_workflow(config, stream=args.stream or run_type == "workflow_stream")
    else:
        from runtime.protocol import emitter
        emitter.emit_error(f"Unknown execution type: {run_type}", "ValueError")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
