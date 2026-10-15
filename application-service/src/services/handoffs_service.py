"""Handoffs service - LangGraph-based multi-agent collaboration

Graph state (checkpointer) is externalized from the in-process `MemorySaver` to
Redis, and thread_id changed from a bare conversation_id to
`{tenant}:{conversation}` (tenant isolation + bounded length). Handoff history is
no longer lost when the process restarts or multiple workers alternate on the same
conversation; the trade-off is that the `messages` channel accumulates across
turns, so the reducer switches to bounded appending (see `append_messages`). The
checkpointer is injected by the orchestration layer; when Redis is unavailable it
falls back to MemorySaver (behavior identical to before).
"""
import logging

import json
import os
import uuid
import threading
from collections import OrderedDict
from typing import List, Dict, Any, Optional, AsyncGenerator, Annotated
from typing_extensions import TypedDict

from langchain_core.messages import (
    HumanMessage,
    AIMessage,
    BaseMessage,
    AIMessageChunk,
    ToolMessage,
)
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..model_clients import RedBearChatModel
from ..schemas.model_schema import ModelInfo
from .model_service import ModelConfigService
from .multi_agent_variable_contract import (
    render_messages,
    render_template,
)

logger = logging.getLogger(__name__)


# ==================== Reducer functions ====================

def replace_value(current, new):
    """Reducer that replaces the value - always takes the new value."""
    return new


# ==================== Constants ====================

MAX_HANDOFFS = 5  # Maximum number of handoffs

# With the checkpointer externalized, HandoffState.messages (previously
# operator.add) would grow unbounded across turns - a per-request MemorySaver
# meant collaboration mode was effectively history-less. History continuity is
# the point of externalization, but unbounded growth both overflows the model
# context and lets checkpoint size run away. So the reducer appends with a
# bound: the state keeps at most the most recent STATE_HISTORY_MESSAGES entries.
STATE_HISTORY_MESSAGES = int(os.getenv("CLUSTER_HANDOFFS_STATE_HISTORY", "60"))
# The window sent to the LLM can be smaller (saves tokens); defaults to the state limit.
MAX_HISTORY_MESSAGES = int(
    os.getenv("CLUSTER_HANDOFFS_MAX_HISTORY", str(STATE_HISTORY_MESSAGES))
)


def _drop_orphan_tool_messages(messages: List[BaseMessage]) -> List[BaseMessage]:
    """If the head is a ToolMessage (whose paired AIMessage.tool_calls was trimmed
    away), keep advancing.

    Some providers error out on a ToolMessage that has no paired tool_call, so the
    trim point must land on a non-ToolMessage.
    """
    start = 0
    while start < len(messages) and isinstance(messages[start], ToolMessage):
        start += 1
    return messages[start:] if start else messages


def append_messages(current: Optional[List[BaseMessage]], new) -> List[BaseMessage]:
    """Bounded message-append reducer (replaces operator.add).

    current may be None (first turn); new may be a single message or a list. After
    merging, trims to STATE_HISTORY_MESSAGES and avoids leaving an orphan
    ToolMessage at the head.
    """
    base = list(current or [])
    if new is None:
        merged = base
    elif isinstance(new, list):
        merged = base + list(new)
    else:
        merged = base + [new]
    if len(merged) > STATE_HISTORY_MESSAGES:
        merged = merged[-STATE_HISTORY_MESSAGES:]
    return _drop_orphan_tool_messages(merged)


def trim_history(messages: List[BaseMessage]) -> List[BaseMessage]:
    """Trim messages sent to the LLM to the most recent MAX_HISTORY_MESSAGES (and drop orphan ToolMessages)."""
    if not messages:
        return messages
    if len(messages) > MAX_HISTORY_MESSAGES:
        messages = messages[-MAX_HISTORY_MESSAGES:]
    return _drop_orphan_tool_messages(messages)


# ==================== State definition ====================

class HandoffState(TypedDict):
    """Handoff state"""
    messages: Annotated[List[BaseMessage], append_messages]  # bounded append
    active_agent: Annotated[Optional[str], replace_value]
    handoff_count: Annotated[int, replace_value]
    handoff_history: Annotated[List[str], replace_value]
    pending_question: Annotated[Optional[str], replace_value]
    previous_answer: Annotated[Optional[str], replace_value]


# ==================== Tool input models ====================

class TransferInput(BaseModel):
    """Input parameters for the transfer tool."""
    reason: str = Field(description="转移原因，说明为什么需要转交")
    unhandled_question: str = Field(
        description="需要转交给其他专家处理的具体问题。注意：只转交你无法回答的部分，不要转交整个原始问题"
    )
    your_answer: str = Field(
        default="",
        description="你已经回答的内容摘要（如果有的话）。如果你已经回答了部分问题，在这里简要说明"
    )


# ==================== Tool creation ====================

def create_transfer_tool(target_agent: str, description: str):
    """Dynamically create a transfer tool

    Args:
        target_agent: Name of the target agent
        description: Tool description

    Returns:
        The transfer tool function
    """
    tool_name = f"transfer_to_{target_agent}"
    
    @tool(tool_name, args_schema=TransferInput)
    def transfer_tool(reason: str, unhandled_question: str, your_answer: str = "") -> Command:
        """Dynamically generated transfer tool

        Args:
            reason: Reason for the transfer
            unhandled_question: The specific question to hand off (only the unhandled part)
            your_answer: Summary of what you already answered
        """
        return Command(
            goto=target_agent,
            update={
                "active_agent": target_agent,
                "pending_question": unhandled_question,  # store the specific question to hand off
                "previous_answer": your_answer,  # store the previous answer
                # handoff_count and handoff_history are updated in agent_node
            },
        )
    
    transfer_tool.__doc__ = description
    transfer_tool.description = description
    return transfer_tool


def create_tools_for_agent(agent_name: str, configs: Dict) -> List:
    """Dynamically create the transfer tools available to an agent, based on its config

    Args:
        agent_name: Name of the current agent
        configs: Agent configuration dict

    Returns:
        List of tools available to this agent
    """
    config = configs.get(agent_name, {})
    can_transfer_to = config.get("can_transfer_to", [])
    
    tools = []
    for target_agent in can_transfer_to:
        target_config = configs.get(target_agent, {})
        description = target_config.get("description", f"转移到 {target_agent}")
        tools.append(create_transfer_tool(target_agent, description))
    
    return tools


# ==================== Agent node creation ====================

def create_agent_node(agent_name: str, system_prompt: str, tools: List,
                      model_view: ModelInfo,
                      variables: Optional[Dict[str, Any]] = None,
                      run_config: Optional[Dict[str, Any]] = None):
    """Create an agent node (non-streaming)

    Args:
        variables: Cluster variable value bundle. When non-empty:
            1) system_prompt goes through full Jinja rendering (same contract as the
               single-agent AgentRunService);
            2) message bodies get conservative rendering (only {{name}} present in the
               bundle is substituted).
        run_config: Runtime config passed through to the LangChain call (metadata/tags),
            carrying the user_id / memory / storage_type / user_rag_memory_id call context.
    """
    llm = RedBearChatModel.for_invoke(
        model_view, params={"temperature": 0.7, "max_tokens": 2000}
    )

    # Bind tools
    if tools:
        llm = llm.bind_tools(tools)

    # Render variables once at node creation (system_prompt is fixed for this run);
    # avoids re-parsing on every token
    rendered_system_prompt = render_template(system_prompt, variables)

    async def agent_node(state: HandoffState) -> Dict[str, Any]:
        """Agent node execution function"""
        logger.debug(f"Agent {agent_name} 执行, active_agent: {state.get('active_agent')}")

        # Fetch current handoff state
        handoff_count = state.get("handoff_count", 0)
        handoff_history = state.get("handoff_history", [])
        pending_question = state.get("pending_question")
        previous_answer = state.get("previous_answer", "")
        
        # Check whether the max handoff count is reached
        if handoff_count >= MAX_HANDOFFS:
            logger.warning(f"Agent {agent_name}: 达到最大 handoff 次数，直接回复")
            return {
                "messages": [AIMessage(content="抱歉，我无法继续处理这个请求。请尝试重新提问。")],
                "handoff_count": handoff_count,
                "handoff_history": handoff_history,
                "pending_question": None,
                "previous_answer": ""
            }

        messages = state.get("messages", [])

        # If there is a pending_question, build a new message context
        if pending_question and handoff_count > 0:
            # Build the message carrying the context
            context_msg = f"【来自其他专家的转交】\n"
            if previous_answer:
                context_msg += f"之前的专家已经回答了: {previous_answer}\n\n"
            context_msg += f"现在需要你回答的问题是: {pending_question}\n\n"
            if handoff_history:
                context_msg += f"【注意】以下专家已经处理过这个问题，不能再转交给他们: {', '.join(handoff_history)}"
            
            # Use the specific handed-off question, not the original message
            effective_messages = [HumanMessage(content=context_msg)]
            logger.info(f"Agent {agent_name} 收到转交问题（非流式）: {pending_question[:100]}...")
        else:
            # Messages accumulate across turns once the checkpointer is externalized;
            # trim the window before sending to the LLM
            effective_messages = trim_history(messages)
        
        # Render {{variables}} in user messages / handoff context (conservative
        # substitution; undefined placeholders stay as-is)
        if variables:
            effective_messages = render_messages(effective_messages, variables)

        full_messages = [{"role": "system", "content": rendered_system_prompt}] + effective_messages
        
        response = await llm.ainvoke(full_messages, config=run_config) if run_config \
            else await llm.ainvoke(full_messages)
        
        # Check tool calls
        if hasattr(response, 'tool_calls') and response.tool_calls:
            tool_call = response.tool_calls[0]
            tool_name = tool_call["name"] if isinstance(tool_call, dict) else tool_call.name
            tool_args = tool_call["args"] if isinstance(tool_call, dict) else tool_call.args
            
            if isinstance(tool_args, str):
                try:
                    tool_args = json.loads(tool_args)
                except (json.JSONDecodeError, ValueError):
                    tool_args = {}
            
            # Make sure required arguments exist
            if not tool_args.get("reason"):
                tool_args["reason"] = "用户请求转移"
            
            # Get the unhandled_question provided by the LLM
            llm_unhandled_question = tool_args.get("unhandled_question", "")
            
            # Extract the target agent
            target_agent = tool_name.replace("transfer_to_", "")
            
            # Check for a loop: whether the target agent is already in handoff_history
            if target_agent in handoff_history:
                logger.warning(f"Agent {agent_name} 尝试移交给已处理过的 {target_agent}，强制直接回复")
                return {
                    "messages": [AIMessage(content="抱歉，这个问题超出了我的专业范围，我无法回答。")],
                    "handoff_count": handoff_count,
                    "handoff_history": handoff_history,
                    "pending_question": None,
                    "previous_answer": ""
                }
            
            # First transfer: check whether unhandled_question was provided
            if not llm_unhandled_question:
                # Fall back to the original message
                last_human_msg = next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)
                llm_unhandled_question = last_human_msg.content if last_human_msg else ""
            
            tool_args["unhandled_question"] = llm_unhandled_question

            for t in tools:
                if t.name == tool_name:
                    # Extract the target agent
                    target_agent = tool_name.replace("transfer_to_", "")
                    new_history = handoff_history + [agent_name]
                    
                    logger.info(f"Agent {agent_name} handoff 到 {target_agent} (count: {handoff_count + 1}), 转交问题: {tool_args.get('unhandled_question', '')[:50]}...")
                    
                    # Return a Command that updates the handoff state
                    return Command(
                        goto=target_agent,
                        update={
                            "active_agent": target_agent,
                            "handoff_count": handoff_count + 1,
                            "handoff_history": new_history,
                            "pending_question": tool_args.get("unhandled_question", ""),
                            "previous_answer": tool_args.get("your_answer", "")
                        }
                    )

        return {
            "messages": [response],
            "handoff_count": handoff_count,
            "handoff_history": handoff_history,
            "pending_question": None,  # clear pending_question
            "previous_answer": ""
        }

    return agent_node


def create_streaming_agent_node(agent_name: str, system_prompt: str, tools: List,
                                 model_view: ModelInfo,
                                 variables: Optional[Dict[str, Any]] = None,
                                 run_config: Optional[Dict[str, Any]] = None):
    """Create an agent node that supports streaming output

    Args:
        variables / run_config: Same as create_agent_node (variable contract and
            context pass-through).
    """
    llm = RedBearChatModel.for_invoke(
        model_view,
        params={"temperature": 0.7, "max_tokens": 2000},
        streaming=True,
    )
    
    # Bind tools
    if tools:
        llm = llm.bind_tools(tools)

    rendered_system_prompt = render_template(system_prompt, variables)

    async def agent_node(state: HandoffState):
        """Agent node execution function (streaming)"""
        logger.debug(f"Agent {agent_name} 流式执行, active_agent: {state.get('active_agent')}")
        
        # Fetch current handoff state
        handoff_count = state.get("handoff_count", 0)
        handoff_history = state.get("handoff_history", [])
        pending_question = state.get("pending_question")
        previous_answer = state.get("previous_answer", "")
        
        logger.info(f"Agent {agent_name} 状态: handoff_count={handoff_count}, pending_question={pending_question}, previous_answer={previous_answer[:50] if previous_answer else ''}")
        
        # Check whether the max handoff count is reached
        if handoff_count >= MAX_HANDOFFS:
            logger.warning(f"Agent {agent_name}: 达到最大 handoff 次数，直接回复")
            return {
                "messages": [AIMessage(content="抱歉，我无法继续处理这个请求。请尝试重新提问。")],
                "handoff_count": handoff_count,
                "handoff_history": handoff_history,
                "pending_question": None,
                "previous_answer": ""
            }

        messages = state.get("messages", [])

        # If there is a pending_question, build a new message context
        if pending_question and handoff_count > 0:
            # Build the message carrying the context
            context_msg = f"【来自其他专家的转交】\n"
            if previous_answer:
                context_msg += f"之前的专家已经回答了: {previous_answer}\n\n"
            context_msg += f"现在需要你回答的问题是: {pending_question}\n\n"
            if handoff_history:
                context_msg += f"【注意】以下专家已经处理过这个问题，不能再转交给他们: {', '.join(handoff_history)}"
            
            # Use the specific handed-off question, not the original message
            effective_messages = [HumanMessage(content=context_msg)]
            logger.info(f"Agent {agent_name} 收到转交问题（流式）: {pending_question[:100]}...")
        else:
            # Messages accumulate across turns once the checkpointer is externalized;
            # trim the window before sending to the LLM
            effective_messages = trim_history(messages)
        
        # Render {{variables}} in user messages / handoff context (conservative
        # substitution; undefined placeholders stay as-is)
        if variables:
            effective_messages = render_messages(effective_messages, variables)

        full_messages = [{"role": "system", "content": rendered_system_prompt}] + effective_messages

        full_content = ""
        collected_tool_calls = {}
        
        _chunks = llm.astream(full_messages, config=run_config) if run_config else llm.astream(full_messages)
        async for chunk in _chunks:
            if hasattr(chunk, 'content') and chunk.content:
                full_content += chunk.content
            
            # Collect tool calls
            if hasattr(chunk, 'tool_calls') and chunk.tool_calls:
                for tc in chunk.tool_calls:
                    tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, 'id', "0")
                    tc_id = tc_id or "0"
                    if tc_id not in collected_tool_calls:
                        collected_tool_calls[tc_id] = {"id": tc_id, "name": "", "args": ""}
                    
                    tc_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, 'name', None)
                    tc_args = tc.get("args") if isinstance(tc, dict) else getattr(tc, 'args', None)
                    
                    if tc_name:
                        collected_tool_calls[tc_id]["name"] = tc_name
                    if tc_args:
                        if isinstance(tc_args, dict):
                            collected_tool_calls[tc_id]["args"] = tc_args
                        elif isinstance(tc_args, str):
                            if isinstance(collected_tool_calls[tc_id]["args"], str):
                                collected_tool_calls[tc_id]["args"] += tc_args
            
            # Handle tool_call_chunks
            if hasattr(chunk, 'tool_call_chunks') and chunk.tool_call_chunks:
                for tc_chunk in chunk.tool_call_chunks:
                    idx = str(tc_chunk.get("index", 0) if isinstance(tc_chunk, dict) else getattr(tc_chunk, 'index', 0))
                    if idx not in collected_tool_calls:
                        tc_id = tc_chunk.get("id", idx) if isinstance(tc_chunk, dict) else getattr(tc_chunk, 'id', idx)
                        collected_tool_calls[idx] = {"id": tc_id, "name": "", "args": ""}
                    
                    tc_id = tc_chunk.get("id") if isinstance(tc_chunk, dict) else getattr(tc_chunk, 'id', None)
                    tc_name = tc_chunk.get("name") if isinstance(tc_chunk, dict) else getattr(tc_chunk, 'name', None)
                    tc_args = tc_chunk.get("args") if isinstance(tc_chunk, dict) else getattr(tc_chunk, 'args', None)
                    
                    if tc_id:
                        collected_tool_calls[idx]["id"] = tc_id
                    if tc_name:
                        collected_tool_calls[idx]["name"] = tc_name
                    if tc_args:
                        if isinstance(collected_tool_calls[idx]["args"], str):
                            collected_tool_calls[idx]["args"] += tc_args

        # Parse tool calls
        tool_calls_list = list(collected_tool_calls.values())
        for tc in tool_calls_list:
            if isinstance(tc.get("args"), str) and tc["args"]:
                try:
                    tc["args"] = json.loads(tc["args"])
                except (json.JSONDecodeError, ValueError):
                    tc["args"] = {}
            elif not tc.get("args"):
                tc["args"] = {}

        # Execute the tool call - pick the call with the most complete arguments
        if tool_calls_list:
            # Find the transfer tool call with the most complete arguments
            best_tool_call = None
            best_args_len = -1
            
            for tc in tool_calls_list:
                tc_name = tc.get("name", "")
                if tc_name.startswith("transfer_to_"):
                    tc_args = tc.get("args", {})
                    if isinstance(tc_args, str):
                        try:
                            tc_args = json.loads(tc_args)
                        except:
                            tc_args = {}
                    # Measure argument completeness
                    args_len = len(str(tc_args.get("unhandled_question", ""))) + len(str(tc_args.get("reason", "")))
                    if args_len > best_args_len:
                        best_args_len = args_len
                        best_tool_call = (tc_name, tc_args)
            
            if best_tool_call:
                tool_name, tool_args = best_tool_call
                
                # Make sure required arguments exist
                if not tool_args.get("reason"):
                    tool_args["reason"] = "用户请求转移"
                
                # Get the unhandled_question provided by the LLM
                llm_unhandled_question = tool_args.get("unhandled_question", "")
                
                # Extract the target agent
                target_agent = tool_name.replace("transfer_to_", "")
                
                # Check for a loop: whether the target agent is already in handoff_history
                if target_agent in handoff_history:
                    logger.warning(f"Agent {agent_name} 尝试移交给已处理过的 {target_agent}，强制直接回复")
                    return {
                        "messages": [AIMessage(content=full_content if full_content else "抱歉，这个问题超出了我的专业范围，我无法回答。")],
                        "handoff_count": handoff_count,
                        "handoff_history": handoff_history,
                        "pending_question": None,
                        "previous_answer": ""
                    }
                
                # Check whether unhandled_question was provided
                if not llm_unhandled_question:
                    # Fall back to the original message
                    last_human_msg = next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)
                    llm_unhandled_question = last_human_msg.content if last_human_msg else ""
                
                new_history = handoff_history + [agent_name]
                
                logger.info(f"Agent {agent_name} handoff 到 {target_agent} (count: {handoff_count + 1}), 转交问题: {llm_unhandled_question[:100]}...")
                
                # Return a Command that updates the handoff state
                return Command(
                    goto=target_agent,
                    update={
                        "active_agent": target_agent,
                        "handoff_count": handoff_count + 1,
                        "handoff_history": new_history,
                        "pending_question": llm_unhandled_question,
                        "previous_answer": tool_args.get("your_answer", "")
                    }
                )

        return {
            "messages": [AIMessage(content=full_content)],
            "handoff_count": handoff_count,
            "handoff_history": handoff_history,
            "pending_question": None,  # clear pending_question
            "previous_answer": ""
        }

    return agent_node


# ==================== Routing functions ====================

def create_route_initial(default_agent: str):
    """Create the initial routing function"""
    def route_initial(state: HandoffState) -> str:
        active = state.get("active_agent")
        if active:
            return active
        return default_agent
    return route_initial


def route_after_agent(state: HandoffState) -> str:
    """Route after an agent executes

    Checks:
    1. Whether the max handoff count is reached
    2. Whether a loop has formed (alternating between two agents)
    3. Whether the last message has tool_calls
    """
    messages = state.get("messages", [])
    handoff_count = state.get("handoff_count", 0)
    handoff_history = state.get("handoff_history", [])
    
    # Check whether the max handoff count is reached
    if handoff_count >= MAX_HANDOFFS:
        logger.warning(f"达到最大 handoff 次数 ({MAX_HANDOFFS})，强制结束")
        return END

    # Check whether a loop has formed (A -> B -> A -> B pattern)
    if len(handoff_history) >= 4:
        # Check whether the last 4 handoffs form an A-B-A-B loop
        recent = handoff_history[-4:]
        if recent[0] == recent[2] and recent[1] == recent[3] and recent[0] != recent[1]:
            logger.warning(f"检测到循环 handoff: {recent}，强制结束")
            return END
    
    # Check the last message
    if messages:
        last_msg = messages[-1]
        if isinstance(last_msg, AIMessage) and not getattr(last_msg, 'tool_calls', None):
            return END
    
    return state.get("active_agent", END)


# ==================== Configuration conversion ====================

async def _resolve_release_tenant_id(db, release: Any) -> uuid.UUID | None:
    from ..models import App
    from ..repositories.tool_repository import ToolRepository
    from sqlalchemy.ext.asyncio import AsyncSession

    app_id = getattr(release, "app_id", None)
    if not app_id:
        return None
    if isinstance(db, AsyncSession):
        app = await db.get(App, app_id)
    else:
        app = db.get(App, app_id)
    if not app:
        return None
    if isinstance(db, AsyncSession):
        return await ToolRepository.get_tenant_id_by_workspace_id_async(db, str(app.workspace_id))
    return ToolRepository.get_tenant_id_by_workspace_id(db, str(app.workspace_id))


async def convert_multi_agent_config_to_handoffs(
    multi_agent_config: Dict,
    db
) -> Dict[str, Dict]:
    """Convert multi_agent_config to the handoffs configuration format

    Args:
        multi_agent_config: Multi-agent config stored in the database
        db: Database session

    Returns:
        agent_configs dict, where each agent carries its own model_view
    """
    from ..models import AppRelease, App
    
    sub_agents = multi_agent_config.get("sub_agents", [])
    agent_configs = {}
    agent_names = []
    
    # Walk the sub agents and build the configs
    for sub_agent in sub_agents:
        agent_id = sub_agent.get("agent_id")  # may be a release_id or an app_id
        agent_name = sub_agent.get("name", f"agent_{agent_id[:8] if agent_id else 'unknown'}")
        # Use a safe agent name (strip special characters)
        safe_name = agent_name.replace(" ", "_").replace("-", "_").lower()
        agent_names.append(safe_name)

        # Fetch the agent's system prompt and model config from AppRelease
        system_prompt = f"你是 {agent_name}。"
        capabilities = sub_agent.get("capabilities", [])
        model_view = None
        release = None
        
        if agent_id:
            try:
                from sqlalchemy.ext.asyncio import AsyncSession
                # Version policy: the orchestrator passes resolved entries (agent_id =
                # a valid release ID); with raw DB entries agent_id may be an app ID -
                # when pinned with a release_id, use that fixed release; otherwise fall
                # through to the lookup below ("try release_id first, then fall back
                # to the app's current release").
                _pinned_release_id = (
                    sub_agent.get("release_id")
                    if sub_agent.get("release_policy") == "pinned"
                    else None
                )
                _lookup_id = _pinned_release_id or agent_id
                agent_id_uuid = uuid.UUID(_lookup_id) if isinstance(_lookup_id, str) else _lookup_id
                is_async = isinstance(db, AsyncSession)

                # First try looking it up as a release_id
                release = await db.get(AppRelease, agent_id_uuid) if is_async else db.get(AppRelease, agent_id_uuid)

                # If not found, try it as an app_id and use current_release
                if not release:
                    app = await db.get(App, agent_id_uuid) if is_async else db.get(App, agent_id_uuid)
                    if app and app.current_release_id:
                        release = await db.get(AppRelease, app.current_release_id) if is_async else db.get(AppRelease, app.current_release_id)

                if release:
                    # Get system_prompt from release.config
                    if release.config:
                        config_data = release.config
                        release_system_prompt = config_data.get("system_prompt")
                        if release_system_prompt:
                            system_prompt = release_system_prompt

                    # Get this agent's model view (non-decrypting; calls go through the model-service invoke seam)
                    if release.default_model_config_id:
                        tenant_id = await _resolve_release_tenant_id(db, release)
                        model_view = await ModelConfigService.get_runtime_model_view_bridge_async(
                            db,
                            release.default_model_config_id,
                            tenant_id=tenant_id,
                        )
                        if model_view:
                            logger.debug(f"Agent {agent_name} 使用模型: {model_view.model_name}")
                        else:
                            logger.warning(f"Agent {agent_name} 模型配置无效: {release.default_model_config_id}")
                    else:
                        logger.warning(f"Agent {agent_name} 没有配置 default_model_config_id")
                else:
                    logger.warning(f"Agent {agent_name} 找不到发布版本: agent_id={agent_id}")
            except Exception as e:
                logger.warning(f"获取 Agent {agent_name} 配置失败: {str(e)}")
        
        # If capabilities exist, append them to the system prompt
        if capabilities:
            if not system_prompt.endswith("。"):
                system_prompt += "。"
            system_prompt += f" 你的专长是: {', '.join(capabilities)}。"
        
        agent_configs[safe_name] = {
            "agent_id": agent_id,
            "name": agent_name,
            "description": f"转移到 {agent_name}。{sub_agent.get('role') or ''}",
            "system_prompt": system_prompt,
            "capabilities": capabilities,
            "model_view": model_view,  # per-agent model view
            "can_transfer_to": []  # filled in later
        }

    # Set which other agents each agent can transfer to
    for safe_name in agent_names:
        agent_configs[safe_name]["can_transfer_to"] = [
            name for name in agent_names if name != safe_name
        ]
        # Update the system prompt with the transfer instructions
        other_agents = agent_configs[safe_name]["can_transfer_to"]
        current_capabilities = agent_configs[safe_name].get("capabilities", [])

        if other_agents:
            # Build the expertise info for the other agents
            other_agents_info = []
            for other_name in other_agents:
                other_config = agent_configs[other_name]
                other_caps = other_config.get("capabilities", [])
                if other_caps:
                    other_agents_info.append(f"- {other_config['name']}: 专长 {', '.join(other_caps)}")
                else:
                    other_agents_info.append(f"- {other_config['name']}")
            
            transfer_instructions = f"""

【重要工作原则】
1. 你必须先输出你对专长范围（{', '.join(current_capabilities) if current_capabilities else '你的领域'}）问题的完整回答
2. 回答完成后，如果还有其他部分需要其他专家处理，再调用转移工具
3. 不能转移给已经处理过这个问题的专家

【回答流程】
1. 先直接输出你的回答内容（不要放在工具参数里）
2. 输出完成后，调用转移工具转交剩余问题

【其他可用的专家】
{chr(10).join(other_agents_info)}

【转移工具参数】
- reason: 转移原因
- unhandled_question: 需要其他专家回答的具体问题
- your_answer: 简要说明你回答了什么（摘要即可）

【转移工具】"""
            for other_name in other_agents:
                other_config = agent_configs[other_name]
                transfer_instructions += f"\n- transfer_to_{other_name}: {other_config['description']}"
            
            agent_configs[safe_name]["system_prompt"] += transfer_instructions
    
    return agent_configs


# ==================== Handoffs service class ====================

class HandoffsService:
    """Handoffs service - manages multi-agent collaboration"""
    
    def __init__(
        self,
        agent_configs: Dict[str, Dict],
        streaming: bool = True,
        variables: Optional[Dict[str, Any]] = None,
        runtime_context: Optional[Dict[str, Any]] = None,
        checkpointer: Optional[Any] = None,
        thread_prefix: Optional[str] = None
    ):
        """Initialize the Handoffs service

        Args:
            agent_configs: Agent config dict, where each agent carries its own model_view
            streaming: Whether to enable streaming output
            variables: Cluster variable value bundle. Collaboration previously had **no
                variables at all** - variables passed in at the cluster entry were
                dropped at the orchestrator layer, leaving collaboration mode as a
                "bare LLM group chat". They are accepted here and handed to each agent
                node for system_prompt / message rendering, aligning the experience
                with the single-agent app.
            runtime_context: Cluster call context {user_id, memory, storage_type,
                user_rag_memory_id}. Also a previously discarded set of parameters; for
                now attached to every LLM call as LangChain metadata/tags (observability
                attribution), so wiring in memory/knowledge later won't require another
                signature change.
            checkpointer: Graph state backend. Injected by the orchestration layer via
                `create_async_checkpointer()` - state is externalized when Redis is
                available, so handoff history for the same conversation survives
                multiple workers / restarts; pass None when unavailable to degrade to
                the in-process `MemorySaver` (same as before). The class deliberately
                does not open connections itself, keeping the degradation decision in
                one place, the orchestration layer.
            thread_prefix: Tenant prefix for thread_id (`{tenant}:{conversation}`).
                Tenant isolation relies on it - cross-tenant parties cannot compose the
                same thread_id and therefore cannot read each other's checkpoints.
        """
        self.agent_configs = agent_configs
        self.streaming = streaming
        self.variables = variables or None
        self.runtime_context = runtime_context or None
        self.checkpointer = checkpointer
        self.thread_prefix = thread_prefix or None
        self._graph = None

        # Verify every agent has a model view
        for agent_name, config in agent_configs.items():
            if not config.get("model_view"):
                raise ValueError(f"Agent {agent_name} 没有配置模型")
        
        logger.info(
            f"HandoffsService 初始化, agents: {list(self.agent_configs.keys())}",
            extra={
                "variable_count": len(self.variables or {}),
                "runtime_context": sorted((self.runtime_context or {}).keys()),
                "checkpoint_backend": type(self.checkpointer).__name__ if self.checkpointer else "memory",
                "thread_prefix": self.thread_prefix,
            },
        )

    def _thread_id(self, conversation_id: str) -> str:
        """Build the checkpoint thread_id: `{tenant}:{conversation}`, auto-digested when too long.

        Previously a bare conversation_id was used - it lacked tenant isolation (the
        same UUID under different tenants would collide) and, once externalized to
        Redis, the key length needed a bound.
        """
        from ..agent.redis_checkpoint import build_thread_id

        return build_thread_id(self.thread_prefix, conversation_id)

    def _build_run_config(self) -> Optional[Dict[str, Any]]:
        """Turn the cluster call context into a LangChain run config.

        Returns None when there is no context (keeps the previous call shape; no
        empty config is passed).
        """
        if not self.runtime_context:
            return None
        metadata = {k: v for k, v in self.runtime_context.items() if v not in (None, "")}
        metadata["graph"] = "handoffs"
        tags = ["cluster:collaboration"]
        user_id = metadata.get("user_id")
        if user_id:
            tags.append(f"end_user:{user_id}")
        return {"metadata": metadata, "tags": tags}
    
    def _build_graph(self):
        """Build the LangGraph graph"""
        builder = StateGraph(HandoffState)
        agent_names = list(self.agent_configs.keys())
        
        if not agent_names:
            
            raise ValueError("至少需要一个 Agent 配置")
        
        run_config = self._build_run_config()
        for agent_name in agent_names:
            config = self.agent_configs[agent_name]
            tools = create_tools_for_agent(agent_name, self.agent_configs)
            
            # Use each agent's own model view
            agent_model_view = config.get("model_view")

            if self.streaming:
                agent_node = create_streaming_agent_node(
                    agent_name=agent_name,
                    system_prompt=config.get("system_prompt", f"你是 {agent_name}"),
                    tools=tools,
                    model_view=agent_model_view,
                    variables=self.variables,
                    run_config=run_config
                )
            else:
                agent_node = create_agent_node(
                    agent_name=agent_name,
                    system_prompt=config.get("system_prompt", f"你是 {agent_name}"),
                    tools=tools,
                    model_view=agent_model_view,
                    variables=self.variables,
                    run_config=run_config,
                )
            builder.add_node(agent_name, agent_node)

        # Add edges
        default_agent = agent_names[0]
        builder.add_conditional_edges(START, create_route_initial(default_agent), agent_names)
        
        for agent_name in agent_names:
            builder.add_conditional_edges(agent_name, route_after_agent, agent_names + [END])

        # Checkpointer externalization: use whatever is injected (the Redis-backed
        # one is passed in by the orchestration layer after probing); without an
        # injection keep MemorySaver - same behavior as before, graph state is
        # per-process only.
        checkpointer = self.checkpointer or MemorySaver()
        return builder.compile(checkpointer=checkpointer)
    
    @property
    def graph(self):
        """Get the graph instance (lazy-loaded)"""
        if self._graph is None:
            self._graph = self._build_graph()
        return self._graph
    
    def reset(self):
        """Reset the graph instance"""
        self._graph = None
        logger.info("HandoffsService 图已重置")
    
    async def chat(
        self,
        message: str,
        conversation_id: str = None
    ) -> Dict[str, Any]:
        """Non-streaming chat

        The thread_id carries the tenant prefix. Note the semantic change from
        checkpoint persistence - previously each request built a fresh
        HandoffsService + MemorySaver and graph state was wiped every turn
        (collaboration mode had no history in practice); now the same conversation
        continues across turns: messages accumulate (bounded), and active_agent
        carries over from where the previous turn ended (context continuity).
        handoff_count / handoff_history / pending_question are explicitly reset in
        the input, so this turn's handoff budget is unaffected by the previous turn.
        """
        conversation_id = conversation_id or f"conv-{uuid.uuid4().hex[:8]}"
        config = {"configurable": {"thread_id": self._thread_id(conversation_id)}}
        
        logger.info(f"Handoffs chat: conversation_id={conversation_id}, message={message[:50]}...")
        
        result = await self.graph.ainvoke({
            "messages": [HumanMessage(content=message)],
            "handoff_count": 0,
            "handoff_history": [],
            "pending_question": None,
            "previous_answer": ""
        }, config=config)
        
        # Extract the response
        # With the checkpointer externalized, result["messages"] contains cross-turn
        # history, so taking the "first AIMessage" would return the previous turn's
        # reply. Search backwards from the tail instead, and only after this turn's
        # input HumanMessage - if this turn produced no AIMessage with content,
        # return empty rather than falling back to the previous turn's reply.
        response_content = ""
        total_tokens = 0
        all_messages = result.get("messages", [])
        turn_start = 0
        for i in range(len(all_messages) - 1, -1, -1):
            if isinstance(all_messages[i], HumanMessage):
                turn_start = i + 1
                break
        for msg in reversed(all_messages[turn_start:]):
            if isinstance(msg, AIMessage) and msg.content:
                response_content = msg.content
                response_meta = msg.response_metadata if hasattr(msg, 'response_metadata') else None
                total_tokens = response_meta.get("token_usage", {}).get("total_tokens", 0) if response_meta else 0
                break
        
        return {
            "conversation_id": str(conversation_id),
            "active_agent": result.get("active_agent"),
            "response": response_content,
            "message_count": len(result.get("messages", [])),
            "handoff_count": result.get("handoff_count", 0),
            # Agents activated in order during this run (each agent appends itself
            # when it initiates a handoff); the orchestration layer uses it to add a
            # sub-execution record per activation (aligned with the tree structure of
            # the streaming path)
            "handoff_history": list(result.get("handoff_history", [])),
            "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": total_tokens
                }
        }
    
    async def chat_stream(
        self,
        message: str,
        conversation_id: str = None
    ) -> AsyncGenerator[str, None]:
        """Streaming chat (thread_id carries the tenant prefix; graph state continues across turns, same semantics as `chat`)"""
        conversation_id = conversation_id or f"conv-{uuid.uuid4().hex[:8]}"
        config = {"configurable": {"thread_id": self._thread_id(conversation_id)}}
        
        logger.info(f"Handoffs stream chat: conversation_id={conversation_id}, message={message[:50]}...")
        
        # Emit the start event
        yield f"event: start\ndata: {json.dumps({'conversation_id': str(conversation_id)}, ensure_ascii=False)}\n\n"

        current_agent = None
        handoff_count = 0
        collected_tool_calls = {}  # collect tool call info
        
        try:
            async for event in self.graph.astream_events(
                {
                    "messages": [HumanMessage(content=message)],
                    "handoff_count": 0,
                    "handoff_history": [],
                    "pending_question": None,
                    "previous_answer": ""
                },
                config=config,
                version="v2"
            ):
                kind = event["event"]
                
                # Capture node start (agent switch)
                if kind == "on_chain_start":
                    node_name = event.get("name", "")
                    if node_name in self.agent_configs:
                        if current_agent != node_name:
                            current_agent = node_name
                            agent_display_name = self.agent_configs[node_name].get("name", node_name)
                            yield f"event: agent\ndata: {json.dumps({'agent': node_name, 'agent_name': agent_display_name}, ensure_ascii=False)}\n\n"
                
                # Capture streaming LLM output
                elif kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    content = chunk.content if hasattr(chunk, 'content') else ""
                    if content:
                        yield f"event: message\ndata: {json.dumps({'content': content, 'agent': current_agent}, ensure_ascii=False)}\n\n"
                    
                    # Collect tool call info
                    if hasattr(chunk, 'tool_calls') and chunk.tool_calls:
                        for tc in chunk.tool_calls:
                            tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, 'id', "0")
                            tc_id = tc_id or "0"
                            if tc_id not in collected_tool_calls:
                                collected_tool_calls[tc_id] = {"id": tc_id, "name": "", "args": ""}
                            
                            tc_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, 'name', None)
                            tc_args = tc.get("args") if isinstance(tc, dict) else getattr(tc, 'args', None)
                            
                            if tc_name:
                                collected_tool_calls[tc_id]["name"] = tc_name
                            if tc_args:
                                if isinstance(tc_args, dict):
                                    collected_tool_calls[tc_id]["args"] = tc_args
                                elif isinstance(tc_args, str):
                                    if isinstance(collected_tool_calls[tc_id]["args"], str):
                                        collected_tool_calls[tc_id]["args"] += tc_args
                    
                    # Handle tool_call_chunks
                    if hasattr(chunk, 'tool_call_chunks') and chunk.tool_call_chunks:
                        for tc_chunk in chunk.tool_call_chunks:
                            idx = str(tc_chunk.get("index", 0) if isinstance(tc_chunk, dict) else getattr(tc_chunk, 'index', 0))
                            if idx not in collected_tool_calls:
                                tc_id = tc_chunk.get("id", idx) if isinstance(tc_chunk, dict) else getattr(tc_chunk, 'id', idx)
                                collected_tool_calls[idx] = {"id": tc_id, "name": "", "args": ""}
                            
                            tc_id = tc_chunk.get("id") if isinstance(tc_chunk, dict) else getattr(tc_chunk, 'id', None)
                            tc_name = tc_chunk.get("name") if isinstance(tc_chunk, dict) else getattr(tc_chunk, 'name', None)
                            tc_args = tc_chunk.get("args") if isinstance(tc_chunk, dict) else getattr(tc_chunk, 'args', None)
                            
                            if tc_id:
                                collected_tool_calls[idx]["id"] = tc_id
                            if tc_name:
                                collected_tool_calls[idx]["name"] = tc_name
                            if tc_args:
                                if isinstance(collected_tool_calls[idx]["args"], str):
                                    collected_tool_calls[idx]["args"] += tc_args
                
                # Capture the LLM end event and emit the collected tool calls
                elif kind == "on_chat_model_end":
                    output_message = event.get("data", {}).get("output", {})
                    if isinstance(output_message, AIMessageChunk):
                        # Token usage is read from three possible levels for
                        # compatibility (newer LangChain keeps it in usage_metadata,
                        # OpenAI-family in response_metadata.token_usage, and some
                        # providers in response_metadata.usage).
                        total_tokens = 0
                        usage_meta = getattr(output_message, 'usage_metadata', None)
                        if isinstance(usage_meta, dict):
                            total_tokens = usage_meta.get("total_tokens", 0)
                        else:
                            response_meta = output_message.response_metadata if hasattr(output_message, 'response_metadata') else None
                            if response_meta:
                                _tu = response_meta.get("token_usage") or response_meta.get("usage") or {}
                                if isinstance(_tu, dict):
                                    total_tokens = _tu.get("total_tokens", 0)
                        usage_payload = json.dumps({"total_tokens": total_tokens}, ensure_ascii=False)
                        yield f"event: sub_usage\ndata: {usage_payload}\n\n"
                    if collected_tool_calls:
                        # Find the transfer tool call with the most complete arguments
                        best_tc = None
                        best_args_len = -1
                        for tc_id, tc_info in collected_tool_calls.items():
                            if tc_info.get("name", "").startswith("transfer_to_"):
                                args = tc_info.get("args", {})
                                if isinstance(args, str):
                                    try:
                                        args = json.loads(args)
                                    except:
                                        args = {}
                                # Measure argument completeness (prefer calls with unhandled_question)
                                args_len = len(str(args.get("unhandled_question", ""))) + len(str(args.get("reason", "")))
                                if args_len > best_args_len:
                                    best_args_len = args_len
                                    best_tc = (tc_info, args)
                        
                        if best_tc:
                            tc_info, args = best_tc
                            handoff_count += 1
                            target_agent = tc_info["name"].replace("transfer_to_", "")
                            target_name = self.agent_configs.get(target_agent, {}).get("name", target_agent)
                            yield f"event: handoff\ndata: {json.dumps({'from': current_agent, 'to': target_agent, 'to_name': target_name, 'handoff_count': handoff_count, 'reason': args.get('reason', ''), 'unhandled_question': args.get('unhandled_question', ''), 'your_answer': args.get('your_answer', '')}, ensure_ascii=False)}\n\n"
                        collected_tool_calls = {}  # clear, ready to collect the next agent's tool calls
            
            # Emit the end event
            yield f"event: end\ndata: {json.dumps({'conversation_id': str(conversation_id), 'final_agent': current_agent, 'total_handoffs': handoff_count}, ensure_ascii=False)}\n\n"
            
        except Exception as e:
            logger.error(f"Handoffs stream error: {str(e)}")
            yield f"event: error\ndata: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
    
    def get_agents(self) -> List[Dict[str, Any]]:
        """Get the list of available agents"""
        agents = []
        for name, config in self.agent_configs.items():
            agents.append({
                "id": name,
                "name": config.get("name", name),
                "description": config.get("description", ""),
                "capabilities": config.get("capabilities", []),
                "can_transfer_to": config.get("can_transfer_to", [])
            })
        return agents


# ==================== Service factory ====================

# Cache service instances (keyed by app_id). The app_id cardinality can reach
# hundreds of thousands, so the cache must be bounded: evict the least recently
# used instance when over the limit, preventing unbounded growth with the number
# of apps.
_service_cache_max_size = 500
_service_cache: "OrderedDict[str, HandoffsService]" = OrderedDict()
_service_cache_lock = threading.Lock()


def get_handoffs_service_for_app(
    app_id: uuid.UUID,
    db: Session,
    streaming: bool = True
) -> HandoffsService:
    """Get the Handoffs service instance for an app_id

    Args:
        app_id: App ID
        db: Database session
        streaming: Whether to use streaming

    Returns:
        A HandoffsService instance
    """
    from .multi_agent_service import MultiAgentService

    cache_key = f"{app_id}_{streaming}"

    # The hit path completes under the lock to avoid interleaving with insert/evict
    with _service_cache_lock:
        cached = _service_cache.get(cache_key)
        if cached is not None:
            _service_cache.move_to_end(cache_key)
            return cached

    # Fetch the multi-agent config (the slow DB work happens outside the lock)
    multi_agent_service = MultiAgentService(db)
    multi_agent_config = multi_agent_service.get_multi_agent_configs(app_id)

    if not multi_agent_config:
        raise ValueError(f"应用 {app_id} 没有多 Agent 配置")

    # Convert the config (each agent carries its own model_view)
    agent_configs = convert_multi_agent_config_to_handoffs(multi_agent_config, db)

    if not agent_configs:
        raise ValueError(f"应用 {app_id} 没有配置子 Agent")

    # Create the service
    service = HandoffsService(agent_configs, streaming)

    # Cache it (evicting the least recently used instance when over the limit).
    # Double-check: while this thread was waiting on the DB another thread may have
    # written the same key; reuse that instance instead of inserting again.
    with _service_cache_lock:
        existing = _service_cache.get(cache_key)
        if existing is not None:
            return existing
        _service_cache[cache_key] = service
        while len(_service_cache) > _service_cache_max_size:
            _service_cache.popitem(last=False)

    return service


def reset_handoffs_service_cache(app_id: uuid.UUID = None):
    """Reset the service cache

    Args:
        app_id: App ID; clears the entire cache when None
    """
    global _service_cache

    with _service_cache_lock:
        if app_id:
            keys_to_remove = [k for k in _service_cache if k.startswith(str(app_id))]
            for key in keys_to_remove:
                del _service_cache[key]
        else:
            _service_cache = OrderedDict()

    logger.info(f"Handoffs 服务缓存已重置: app_id={app_id}")
