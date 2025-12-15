import asyncio
import io
import json
import logging
import os
import re
import time
import uuid
import warnings
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

from dotenv import load_dotenv
from langchain_core.messages import AIMessage
from langgraph.constants import START, END
from langgraph.graph import StateGraph
from langgraph.prebuilt import ToolNode
from functools import partial

from app.core.memory.agent.utils.llm_tools import ReadState, COUNTState
from langgraph.checkpoint.memory import InMemorySaver

from app.core.memory.agent.utils.redis_tool import store
from app.core.logging_config import get_agent_logger

# Import new modular components
from app.core.memory.agent.langgraph_graph.nodes import ToolExecutionNode, create_input_message
from app.core.memory.agent.langgraph_graph.routing.routers import (
    Verify_continue,
    Retrieve_continue,
    Split_continue
)
from app.core.memory.agent.mcp_server.services.parameter_builder import ParameterBuilder
from app.core.memory.agent.utils.multimodal import MultimodalProcessor

logger = get_agent_logger(__name__)

warnings.filterwarnings("ignore", category=RuntimeWarning)
load_dotenv()
redishost=os.getenv("REDISHOST")
redisport=os.getenv('REDISPORT')
redisdb=os.getenv('REDISDB')
redispassword=os.getenv('REDISPASSWORD')
counter = COUNTState(limit=3)

# 在工作流中添加循环计数更新
async def update_loop_count(state):
    """更新循环计数器"""
    current_count = state.get("loop_count", 0)
    return {"loop_count": current_count + 1}


def Verify_continue(state: ReadState) -> Literal["Summary", "Summary_fails", "content_input"]:
    messages = state["messages"]

    # 添加边界检查
    if not messages:
        return END
    counter.add(1)  # 累加 1

    loop_count = counter.get_total()
    logger.debug(f"[should_continue] 当前循环次数: {loop_count}")

    last_message = messages[-1]
    last_message_str = str(last_message).replace('\\', '')
    status_tools = re.findall(r'"split_result": "(.*?)"', last_message_str)
    logger.debug(f"Status tools: {status_tools}")

    if "success" in status_tools:
        counter.reset()
        return "Summary"
    elif "failed" in status_tools:
        if loop_count < 2:  # 最大循环次数 3
            return "content_input"
        else:
            counter.reset()
            return "Summary_fails"
    else:
        # 添加默认返回值，避免返回 None
        counter.reset()
        return "Summary"  # 或根据业务需求选择合适的默认值


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

    # 添加默认返回值，避免返回 None
    return 'Retrieve_Summary'  # 或根据业务逻辑选择合适的默认值


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
    return 'Split_The_Problem'  # 默认情况

# 在 input_sentence 函数中修改参数名称
async def input_sentence(state, name, id, search_switch,apply_id,group_id):
    messages = state["messages"]
    last_message = messages[-1].content if messages else ""

    if last_message.endswith('.jpg') or last_message.endswith('.png'):
        last_message=await picture_model_requests(last_message)
    if any(last_message.endswith(ext) for ext in audio_extensions):
        last_message=await Vico_recognition([last_message]).run()
        logger.debug(f"Audio recognition result: {last_message}")


    uuid_str = uuid.uuid4()
    time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    namespace = str(id).split('_id_')[1]
    if 'verified_data' in str(last_message):
        messages_last = str(last_message).replace('\\n', '').replace('\\', '')
        last_message = re.findall(r'"query": "(.*?)",', str(messages_last))[0]

    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[{
                    "name": name,
                    "args": {
                        "sentence": last_message,
                        'sessionid': id,
                        'messages_id': str(uuid_str),
                        "search_switch": search_switch,  # 正确地将 search_switch 放入 args 中
                        "apply_id":apply_id,
                        "group_id":group_id
                    },
                    "id": id + f'_{uuid_str}'
                }]
            )
        ]
    }


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
        logger.debug(f"ProblemExtensionNode {self.id} - 当前时间: {time.time()} - Message: {last_message}")
        if self.tool_name=='Input_Summary':
            tool_call =re.findall("'id': '(.*?)'",str(last_message))[0]
        else:tool_call = str(re.findall(r"tool_call_id=.*?'(.*?)'", str(last_message))[0]).replace('\\', '').split('_id')[1]
        # try:
        #     content = json.loads(last_message.content) if hasattr(last_message, 'content') else last_message
        # except:
        #     content = last_message.content if hasattr(last_message, 'content') else str(last_message)
        # 尝试从上一工具的结果中提取实际的内容载荷（而不是整个对象的字符串表示）
        raw_msg = last_message.content if hasattr(last_message, 'content') else str(last_message)
        extracted_payload = None
        # 捕获 ToolMessage 的 content 字段（支持单/双引号），并避免贪婪匹配
        m = re.search(r"content=(?:\"|\')(.*?)(?:\"|\'),\s*name=", raw_msg, flags=re.S)
        if m:
            extracted_payload = m.group(1)
        else:
            # 回退：直接尝试使用原始字符串
            extracted_payload = raw_msg

        # 优先尝试将内容解析为 JSON
        try:
            content = json.loads(extracted_payload)
        except Exception:
            # 尝试从文本中提取 JSON 片段再解析
            parsed = None
            candidates = re.findall(r"[\[{].*[\]}]", extracted_payload, flags=re.S)
            for cand in candidates:
                try:
                    parsed = json.loads(cand)
                    break
                except Exception:
                    continue
            # 如果仍然失败，则以原始字符串作为内容
            content = parsed if parsed is not None else extracted_payload

        # 根据工具名称构建正确的参数
        tool_args = {}

        if self.tool_name == "Verify":
            # Verify工具需要context和usermessages参数
            if isinstance(content, dict):
                tool_args["context"] = content
            else:
                tool_args["context"] = {"content": content}
            tool_args["usermessages"] = str(tool_call)
            tool_args["apply_id"] = str(self.apply_id)
            tool_args["group_id"] = str(self.group_id)
        elif self.tool_name == "Retrieve":
            # Retrieve工具需要context和usermessages参数
            if isinstance(content, dict):
                tool_args["context"] = content
            else:
                tool_args["context"] = {"content": content}
            tool_args["usermessages"] = str(tool_call)
            tool_args["search_switch"] = str(self.search_switch)
            tool_args["apply_id"] = str(self.apply_id)
            tool_args["group_id"] = str(self.group_id)
        elif self.tool_name == "Summary":
            # Summary工具需要字符串类型的context参数
            if isinstance(content, dict):
                # 将字典转换为JSON字符串
                tool_args["context"] = json.dumps(content, ensure_ascii=False)
            else:
                tool_args["context"] = str(content)
            tool_args["usermessages"] = str(tool_call)
            tool_args["apply_id"] = str(self.apply_id)
            tool_args["group_id"] = str(self.group_id)
        elif self.tool_name == "Summary_fails":
            # Summary工具需要字符串类型的context参数
            if isinstance(content, dict):
                # 将字典转换为JSON字符串
                tool_args["context"] = json.dumps(content, ensure_ascii=False)
            else:
                tool_args["context"] = str(content)
            tool_args["usermessages"] = str(tool_call)
            tool_args["apply_id"] = str(self.apply_id)
            tool_args["group_id"] = str(self.group_id)
        elif self.tool_name=='Input_Summary':
            tool_args["context"] =str(last_message)
            tool_args["usermessages"] = str(tool_call)
            tool_args["search_switch"] = str(self.search_switch)
            tool_args["apply_id"] = str(self.apply_id)
            tool_args["group_id"] = str(self.group_id)
            tool_args["storage_type"] = getattr(self, 'storage_type', "")
            tool_args["user_rag_memory_id"] = getattr(self, 'user_rag_memory_id', "")
        elif self.tool_name=='Retrieve_Summary' :
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
            # 其他工具使用context参数
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
async def make_read_graph(namespace,tools,search_switch,apply_id,group_id,config_id=None,storage_type=None,user_rag_memory_id=None):
    memory = InMemorySaver()
    tool=[i.name for i in tools ]
    logger.info(f"Initializing read graph with tools: {tool}")
    if config_id:
        logger.info(f"使用配置 ID: {config_id}")
    
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
        user_rag_memory_id=user_rag_memory_id
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
        user_rag_memory_id=user_rag_memory_id
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
        user_rag_memory_id=user_rag_memory_id
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
        user_rag_memory_id=user_rag_memory_id
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
        user_rag_memory_id=user_rag_memory_id
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
        user_rag_memory_id=user_rag_memory_id
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
        user_rag_memory_id=user_rag_memory_id
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
            multimodal_processor=multimodal_processor
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


# 添加到文件末尾或创建新的执行脚本
# 在 memory_agent_service.py 文件中添加以下函数

