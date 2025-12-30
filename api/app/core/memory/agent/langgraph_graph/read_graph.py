import json
import os
import re
import time
import warnings
from contextlib import asynccontextmanager
from typing import Literal

from app.core.logging_config import get_agent_logger
from app.core.memory.agent.langgraph_graph.nodes import (
    ToolExecutionNode,
    create_input_message,
)
from app.core.memory.agent.mcp_server.services.parameter_builder import ParameterBuilder
from app.core.memory.agent.utils.llm_tools import COUNTState, ReadState
from app.core.memory.agent.utils.multimodal import MultimodalProcessor
from app.schemas.memory_config_schema import MemoryConfig
from dotenv import load_dotenv
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.constants import END, START
from langgraph.graph import StateGraph
from langgraph.prebuilt import ToolNode

logger = get_agent_logger(__name__)

warnings.filterwarnings("ignore", category=RuntimeWarning)
load_dotenv()
redishost=os.getenv("REDISHOST")
redisport=os.getenv('REDISPORT')
redisdb=os.getenv('REDISDB')
redispassword=os.getenv('REDISPASSWORD')
counter = COUNTState(limit=3)

# Update loop count in workflow
async def update_loop_count(state):
    """Update loop counter"""
    current_count = state.get("loop_count", 0)
    return {"loop_count": current_count + 1}


def Verify_continue(state: ReadState) -> Literal["Summary", "Summary_fails", "content_input"]:
    messages = state["messages"]

    # Add boundary check
    if not messages:
        return END
    counter.add(1)  # Increment by 1

    loop_count = counter.get_total()
    logger.debug(f"[should_continue] Current loop count: {loop_count}")

    last_message = messages[-1]
    last_message_str = str(last_message).replace('\\', '')
    status_tools = re.findall(r'"split_result": "(.*?)"', last_message_str)
    logger.debug(f"Status tools: {status_tools}")

    if "success" in status_tools:
        counter.reset()
        return "Summary"
    elif "failed" in status_tools:
        if loop_count < 2:  # Maximum loop count is 3
            return "content_input"
        else:
            counter.reset()
            return "Summary_fails"
    else:
        # Add default return value to avoid returning None
        counter.reset()
        return "Summary"  # Default based on business requirements


def Retrieve_continue(state) -> Literal["Verify", "Retrieve_Summary"]:
    """
    Determine routing based on search_switch value.

    Args:
        state: State dictionary containing search_switch

    Returns:
        Next node to execute
    """
    # Direct dictionary access instead of regex parsing
    search_switch = state.get("search_switch")

    # Handle case where search_switch might be in messages
    if search_switch is None and "messages" in state:
        messages = state.get("messages", [])
        if messages:
            last_message = messages[-1]
            # Try to extract from tool_calls args
            if hasattr(last_message, "tool_calls") and last_message.tool_calls:
                for tool_call in last_message.tool_calls:
                    if isinstance(tool_call, dict) and "args" in tool_call:
                        search_switch = tool_call["args"].get("search_switch")
                        break

    # Convert to string for comparison if needed
    if search_switch is not None:
        search_switch = str(search_switch)
        if search_switch == '0':
            return 'Verify'
        elif search_switch == '1':
            return 'Retrieve_Summary'

    # Add default return value to avoid returning None
    return 'Retrieve_Summary'  # Default based on business logic


def Split_continue(state) -> Literal["Split_The_Problem", "Input_Summary"]:
    """
    Determine routing based on search_switch value.

    Args:
        state: State dictionary containing search_switch

    Returns:
        Next node to execute
    """
    logger.debug(f"Split_continue state: {state}")

    # Direct dictionary access instead of regex parsing
    search_switch = state.get("search_switch")

    # Handle case where search_switch might be in messages
    if search_switch is None and "messages" in state:
        messages = state.get("messages", [])
        if messages:
            last_message = messages[-1]
            # Try to extract from tool_calls args
            if hasattr(last_message, "tool_calls") and last_message.tool_calls:
                for tool_call in last_message.tool_calls:
                    if isinstance(tool_call, dict) and "args" in tool_call:
                        search_switch = tool_call["args"].get("search_switch")
                        break

    # Convert to string for comparison if needed
    if search_switch is not None:
        search_switch = str(search_switch)
        if search_switch == '2':
            return 'Input_Summary'
    return 'Split_The_Problem'  # Default case


class ProblemExtensionNode:
    def __init__(self, tool, id, namespace, search_switch, apply_id, group_id, storage_type="", user_rag_memory_id=""):
        self.tool_node = ToolNode([tool])
        self.id = id
        self.tool_name = tool.name if hasattr(tool, 'name') else str(tool)
        self.namespace = namespace
        self.search_switch = search_switch
        self.apply_id = apply_id
        self.group_id = group_id
        self.storage_type = storage_type
        self.user_rag_memory_id = user_rag_memory_id

    async def __call__(self, state):
        messages = state["messages"]
        last_message = messages[-1] if messages else ""
        logger.debug(f"ProblemExtensionNode {self.id} - Current time: {time.time()} - Message: {last_message}")
        if self.tool_name == 'Input_Summary':
            tool_call = re.findall("'id': '(.*?)'", str(last_message))[0]
        else:
            tool_call = str(re.findall(r"tool_call_id=.*?'(.*?)'", str(last_message))[0]).replace('\\', '').split('_id')[1]
        
        # Try to extract actual content payload from previous tool result
        raw_msg = last_message.content if hasattr(last_message, 'content') else str(last_message)
        extracted_payload = None
        # Capture ToolMessage content field (supports single/double quotes), avoid greedy matching
        m = re.search(r"content=(?:\"|\')(.*?)(?:\"|\'),\s*name=", raw_msg, flags=re.S)
        if m:
            extracted_payload = m.group(1)
        else:
            # Fallback: use raw string directly
            extracted_payload = raw_msg

        # Try to parse content as JSON first
        try:
            content = json.loads(extracted_payload)
        except Exception:
            # Try to extract JSON fragment from text and parse
            parsed = None
            candidates = re.findall(r"[\[{].*[\]}]", extracted_payload, flags=re.S)
            for cand in candidates:
                try:
                    parsed = json.loads(cand)
                    break
                except Exception:
                    continue
            # If still fails, use raw string as content
            content = parsed if parsed is not None else extracted_payload

        # Build correct parameters based on tool name
        tool_args = {}

        if self.tool_name == "Verify":
            # Verify tool requires context and usermessages parameters
            if isinstance(content, dict):
                tool_args["context"] = content
            else:
                tool_args["context"] = {"content": content}
            tool_args["usermessages"] = str(tool_call)
            tool_args["apply_id"] = str(self.apply_id)
            tool_args["group_id"] = str(self.group_id)
        elif self.tool_name == "Retrieve":
            # Retrieve tool requires context and usermessages parameters
            if isinstance(content, dict):
                tool_args["context"] = content
            else:
                tool_args["context"] = {"content": content}
            tool_args["usermessages"] = str(tool_call)
            tool_args["search_switch"] = str(self.search_switch)
            tool_args["apply_id"] = str(self.apply_id)
            tool_args["group_id"] = str(self.group_id)
        elif self.tool_name == "Summary":
            # Summary tool requires string type context parameter
            if isinstance(content, dict):
                # Convert dict to JSON string
                tool_args["context"] = json.dumps(content, ensure_ascii=False)
            else:
                tool_args["context"] = str(content)
            tool_args["usermessages"] = str(tool_call)
            tool_args["apply_id"] = str(self.apply_id)
            tool_args["group_id"] = str(self.group_id)
        elif self.tool_name == "Summary_fails":
            # Summary_fails tool requires string type context parameter
            if isinstance(content, dict):
                # Convert dict to JSON string
                tool_args["context"] = json.dumps(content, ensure_ascii=False)
            else:
                tool_args["context"] = str(content)
            tool_args["usermessages"] = str(tool_call)
            tool_args["apply_id"] = str(self.apply_id)
            tool_args["group_id"] = str(self.group_id)
        elif self.tool_name == 'Input_Summary':
            tool_args["context"] = str(last_message)
            tool_args["usermessages"] = str(tool_call)
            tool_args["search_switch"] = str(self.search_switch)
            tool_args["apply_id"] = str(self.apply_id)
            tool_args["group_id"] = str(self.group_id)
            tool_args["storage_type"] = getattr(self, 'storage_type', "")
            tool_args["user_rag_memory_id"] = getattr(self, 'user_rag_memory_id', "")
        elif self.tool_name == 'Retrieve_Summary':
            # Retrieve_Summary expects dict directly, not JSON string
            # content might be a JSON string, try to parse it
            if isinstance(content, str):
                try:
                    parsed_content = json.loads(content)
                    # Check if it has a "context" key
                    if isinstance(parsed_content, dict) and "context" in parsed_content:
                        tool_args["context"] = parsed_content["context"]
                    else:
                        tool_args["context"] = parsed_content
                except json.JSONDecodeError:
                    # If parsing fails, wrap the string
                    tool_args["context"] = {"content": content}
            elif isinstance(content, dict):
                # Check if content has a "context" key that needs unwrapping
                if "context" in content:
                    tool_args["context"] = content["context"]
                else:
                    tool_args["context"] = content
            else:
                tool_args["context"] = {"content": str(content)}

            tool_args["usermessages"] = str(tool_call)
            tool_args["apply_id"] = str(self.apply_id)
            tool_args["group_id"] = str(self.group_id)
        else:
            # Other tools use context parameter
            if isinstance(content, dict):
                tool_args["context"] = content
            else:
                tool_args["context"] = {"content": content}
            tool_args["usermessages"] = str(tool_call)
            tool_args["apply_id"] = str(self.apply_id)
            tool_args["group_id"] = str(self.group_id)


        tool_input = {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{
                        "name": self.tool_name,
                        "args": tool_args,
                        "id": self.id + f"{tool_call}",
                    }]
                )
            ]
        }
        result = await self.tool_node.ainvoke(tool_input)
        result_text = str(result)

        return {"messages": [AIMessage(content=result_text)]}


@asynccontextmanager
async def make_read_graph(namespace, tools, search_switch, apply_id, group_id, memory_config: MemoryConfig, storage_type=None, user_rag_memory_id=None):
    """
    Create a read graph workflow for memory operations.
    
    Args:
        namespace: Namespace identifier
        tools: MCP tools loaded from session
        search_switch: Search mode switch ("0", "1", or "2")
        apply_id: Application identifier
        group_id: Group identifier
        memory_config: MemoryConfig object containing all configuration
        storage_type: Storage type (optional)
        user_rag_memory_id: User RAG memory ID (optional)
    """
    memory = InMemorySaver()
    tool = [i.name for i in tools]
    logger.info(f"Initializing read graph with tools: {tool}")
    logger.info(f"Using memory_config: {memory_config.config_name} (id={memory_config.config_id})")
    
    # Extract tool functions
    Split_The_Problem_ = next((t for t in tools if t.name == "Split_The_Problem"), None)
    Problem_Extension_ = next((t for t in tools if t.name == "Problem_Extension"), None)
    Retrieve_ = next((t for t in tools if t.name == "Retrieve"), None)
    Verify_ = next((t for t in tools if t.name == "Verify"), None)
    Summary_ = next((t for t in tools if t.name == "Summary"), None)
    Summary_fails_ = next((t for t in tools if t.name == "Summary_fails"), None)
    Retrieve_Summary_ = next((t for t in tools if t.name == "Retrieve_Summary"), None)
    Input_Summary_ = next((t for t in tools if t.name == "Input_Summary"), None)
    
    # Instantiate services
    parameter_builder = ParameterBuilder()
    multimodal_processor = MultimodalProcessor()
    
    # Create nodes using new modular components
    Split_The_Problem_node = ToolNode([Split_The_Problem_])
    
    Problem_Extension_node = ToolExecutionNode(
        tool=Problem_Extension_,
        node_id="Problem_Extension_id",
        namespace=namespace,
        search_switch=search_switch,
        apply_id=apply_id,
        group_id=group_id,
        parameter_builder=parameter_builder,
        storage_type=storage_type,
        user_rag_memory_id=user_rag_memory_id,
        memory_config=memory_config,
    )

    Retrieve_node = ToolExecutionNode(
        tool=Retrieve_,
        node_id="Retrieve_id",
        namespace=namespace,
        search_switch=search_switch,
        apply_id=apply_id,
        group_id=group_id,
        parameter_builder=parameter_builder,
        storage_type=storage_type,
        user_rag_memory_id=user_rag_memory_id,
        memory_config=memory_config,
    )

    Verify_node = ToolExecutionNode(
        tool=Verify_,
        node_id="Verify_id",
        namespace=namespace,
        search_switch=search_switch,
        apply_id=apply_id,
        group_id=group_id,
        parameter_builder=parameter_builder,
        storage_type=storage_type,
        user_rag_memory_id=user_rag_memory_id,
        memory_config=memory_config,
    )
    
    Summary_node = ToolExecutionNode(
        tool=Summary_,
        node_id="Summary_id",
        namespace=namespace,
        search_switch=search_switch,
        apply_id=apply_id,
        group_id=group_id,
        parameter_builder=parameter_builder,
        storage_type=storage_type,
        user_rag_memory_id=user_rag_memory_id,
        memory_config=memory_config,
    )

    Summary_fails_node = ToolExecutionNode(
        tool=Summary_fails_,
        node_id="Summary_fails_id",
        namespace=namespace,
        search_switch=search_switch,
        apply_id=apply_id,
        group_id=group_id,
        parameter_builder=parameter_builder,
        storage_type=storage_type,
        user_rag_memory_id=user_rag_memory_id,
        memory_config=memory_config,
    )

    Retrieve_Summary_node = ToolExecutionNode(
        tool=Retrieve_Summary_,
        node_id="Retrieve_Summary_id",
        namespace=namespace,
        search_switch=search_switch,
        apply_id=apply_id,
        group_id=group_id,
        parameter_builder=parameter_builder,
        storage_type=storage_type,
        user_rag_memory_id=user_rag_memory_id,
        memory_config=memory_config,
    )

    Input_Summary_node = ToolExecutionNode(
        tool=Input_Summary_,
        node_id="Input_Summary_id",
        namespace=namespace,
        search_switch=search_switch,
        apply_id=apply_id,
        group_id=group_id,
        parameter_builder=parameter_builder,
        storage_type=storage_type,
        user_rag_memory_id=user_rag_memory_id,
        memory_config=memory_config,
    )

    async def content_input_node(state):
        state_search_switch = state.get("search_switch", search_switch)

        tool_name = "Input_Summary" if state_search_switch == '2' else "Split_The_Problem"
        session_prefix = "input_summary_call_id" if state_search_switch == '2' else "split_call_id"

        return await create_input_message(
            state=state,
            tool_name=tool_name,
            session_id=f"{session_prefix}_{namespace}",
            search_switch=search_switch,
            apply_id=apply_id,
            group_id=group_id,
            multimodal_processor=multimodal_processor,
            memory_config=memory_config,
        )

    
    # Build workflow graph
    workflow = StateGraph(ReadState)
    workflow.add_node("content_input", content_input_node)
    workflow.add_node("Split_The_Problem", Split_The_Problem_node)
    workflow.add_node("Problem_Extension", Problem_Extension_node)
    workflow.add_node("Retrieve", Retrieve_node)
    workflow.add_node("Verify", Verify_node)
    workflow.add_node("Summary", Summary_node)
    workflow.add_node("Summary_fails", Summary_fails_node)
    workflow.add_node("Retrieve_Summary", Retrieve_Summary_node)
    workflow.add_node("Input_Summary", Input_Summary_node)

    # Add edges using imported routers
    workflow.add_edge(START, "content_input")
    workflow.add_conditional_edges("content_input", Split_continue)
    workflow.add_edge("Input_Summary", END)
    workflow.add_edge("Split_The_Problem", "Problem_Extension")
    workflow.add_edge("Problem_Extension", "Retrieve")
    workflow.add_conditional_edges("Retrieve", Retrieve_continue)
    workflow.add_edge("Retrieve_Summary", END)
    workflow.add_conditional_edges("Verify", Verify_continue)
    workflow.add_edge("Summary_fails", END)
    workflow.add_edge("Summary", END)

    graph = workflow.compile(checkpointer=memory)
    yield graph
