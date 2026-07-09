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


def setup_timeout(timeout: int):
    """Set up execution timeout using SIGALRM"""
    def handler(signum, frame):
        from .protocol import emitter
        emitter.emit_error("Execution timed out", "TimeoutError")
        sys.exit(124)
    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, handler)
        signal.alarm(timeout)


async def run_agent(config: dict, stream: bool = False):
    """Execute Agent in sandbox"""
    from .config import SandboxRuntimeConfig
    from .callback_client import CallbackClient
    from .protocol import emitter
    from .tools.loader import load_tools

    runtime_config = SandboxRuntimeConfig.from_env()
    model_config = config.get("model_config", {})
    runtime_config.llm_api_key = model_config.get("api_key", runtime_config.llm_api_key)
    runtime_config.llm_api_base = model_config.get("api_base", runtime_config.llm_api_base)
    runtime_config.llm_model_name = model_config.get("model_name", runtime_config.llm_model_name)
    runtime_config.llm_provider = model_config.get("provider", runtime_config.llm_provider)

    callback_client = CallbackClient(runtime_config)

    try:
        tool_configs = config.get("agent_config", {}).get("tools", [])
        tools = load_tools(tool_configs, callback_client)
        agent_config = config.get("agent_config", {})
        message = config.get("message", "")
        context = config.get("context", {})

        emitter.emit_start("agent", runtime_config.execution_id)

        from langchain_openai import ChatOpenAI
        from langchain.agents import create_agent
        from langchain_core.messages import HumanMessage, AIMessage

        llm_kwargs = {
            "model": runtime_config.llm_model_name,
            "api_key": runtime_config.llm_api_key,
            "temperature": model_config.get("temperature", 0.7),
            "max_tokens": model_config.get("max_tokens", 2000),
            "streaming": stream,
        }
        if runtime_config.llm_api_base:
            llm_kwargs["base_url"] = runtime_config.llm_api_base
        llm = ChatOpenAI(**llm_kwargs)

        system_prompt = agent_config.get("system_prompt", "You are a helpful assistant.")
        agent = create_agent(model=llm, tools=tools, system_prompt=system_prompt)

        messages = []
        for msg in context.get("history", []):
            if msg.get("role") == "user":
                messages.append(HumanMessage(content=msg["content"]))
            elif msg.get("role") == "assistant":
                messages.append(AIMessage(content=msg["content"]))
        messages.append(HumanMessage(content=message))

        if stream:
            content = ""
            async for event in agent.astream_events({"messages": messages}, version="v2"):
                kind = event.get("event")
                if kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        content += chunk.content
                        emitter.emit_agent_chunk(chunk.content)
                elif kind == "on_tool_start":
                    emitter.emit_agent_tool_start(
                        event.get("name", "unknown"),
                        event.get("data", {}).get("input"),
                    )
                elif kind == "on_tool_end":
                    emitter.emit_agent_tool_end(
                        event.get("name", "unknown"),
                        str(event.get("data", {}).get("output", ""))[:500],
                    )
            emitter.emit_end({"content": content})
        else:
            result = await agent.ainvoke({"messages": messages})
            final_messages = result.get("messages", [])
            content = ""
            if final_messages:
                last_msg = final_messages[-1]
                content = getattr(last_msg, "content", str(last_msg))
            emitter.emit_end({"content": content})

    except Exception as e:
        logger.error(f"Agent execution failed: {e}", exc_info=True)
        emitter.emit_error(str(e), type(e).__name__)
    finally:
        await callback_client.close()


async def run_workflow(config: dict, stream: bool = False):
    """Execute Workflow in sandbox"""
    from .config import SandboxRuntimeConfig
    from .callback_client import CallbackClient
    from .protocol import emitter, EventType

    runtime_config = SandboxRuntimeConfig.from_env()
    callback_client = CallbackClient(runtime_config)

    try:
        workflow_config = config.get("workflow_config", {})
        input_data = config.get("input_data", {})
        execution_context = config.get("execution_context", {})

        emitter.emit_start("workflow", runtime_config.execution_id)

        sys.path.insert(0, "/app")
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
            else:
                emitter.emit(event_type, event_data)

    except Exception as e:
        logger.error(f"Workflow execution failed: {e}", exc_info=True)
        emitter.emit_error(str(e), type(e).__name__)
    finally:
        await callback_client.close()


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
        from .protocol import emitter
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
        from .protocol import emitter
        emitter.emit_error(f"Unknown execution type: {run_type}", "ValueError")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
