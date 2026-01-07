"""Handoffs 服务 - 基于 LangGraph 的多 Agent 协作"""
import json
import uuid
from typing import List, Dict, Any, Optional, AsyncGenerator
from typing_extensions import TypedDict

from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.core.logging_config import get_business_logger

logger = get_business_logger()


# ==================== 状态定义 ====================

class HandoffState(TypedDict):
    """Handoff 状态"""
    messages: List[BaseMessage]
    active_agent: Optional[str]


# ==================== 工具输入模型 ====================

class TransferInput(BaseModel):
    """转移工具的输入参数"""
    reason: str = Field(description="转移原因")


# ==================== 默认配置 ====================

DEFAULT_AGENT_CONFIGS = {
    "sales_agent": {
        "description": "转移到销售 Agent。当用户询问价格、购买或销售相关问题时使用。",
        "system_prompt": """你是一个销售 Agent。帮助用户解答销售相关问题。
如果用户询问技术问题或需要技术支持，使用 transfer_to_support_agent 工具转移到支持 Agent。""",
        "can_transfer_to": ["support_agent"]
    },
    "support_agent": {
        "description": "转移到支持 Agent。当用户询问技术问题或需要帮助时使用。",
        "system_prompt": """你是一个技术支持 Agent。帮助用户解决技术问题。
如果用户询问价格或购买相关问题，使用 transfer_to_sales_agent 工具转移到销售 Agent。""",
        "can_transfer_to": ["sales_agent"]
    }
}

DEFAULT_LLM_CONFIG = {
    "api_key": "sk-8e9e40cd171749858ce2d3722ea75669",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "model": "qwen-plus",
    "temperature": 0.7
}


# ==================== 工具创建 ====================

def create_transfer_tool(target_agent: str, description: str):
    """动态创建转移工具
    
    Args:
        target_agent: 目标 Agent 名称
        description: 工具描述
    
    Returns:
        转移工具函数
    """
    tool_name = f"transfer_to_{target_agent}"
    
    @tool(tool_name, args_schema=TransferInput)
    def transfer_tool(reason: str) -> Command:
        """动态生成的转移工具"""
        return Command(
            goto=target_agent,
            update={"active_agent": target_agent},
        )
    
    transfer_tool.__doc__ = description
    transfer_tool.description = description
    return transfer_tool


def create_tools_for_agent(agent_name: str, configs: Dict) -> List:
    """根据 Agent 配置动态创建其可用的转移工具
    
    Args:
        agent_name: 当前 Agent 名称
        configs: Agent 配置字典
    
    Returns:
        该 Agent 可用的工具列表
    """
    config = configs.get(agent_name, {})
    can_transfer_to = config.get("can_transfer_to", [])
    
    tools = []
    for target_agent in can_transfer_to:
        target_config = configs.get(target_agent, {})
        description = target_config.get("description", f"转移到 {target_agent}")
        tools.append(create_transfer_tool(target_agent, description))
    
    return tools


# ==================== Agent 节点创建 ====================

def create_agent_node(agent_name: str, system_prompt: str, tools: List,
                      api_key: str, base_url: str, model: str, temperature: float = 0.7):
    """创建 Agent 节点（非流式）
    
    Args:
        agent_name: Agent 名称
        system_prompt: 系统提示词
        tools: 工具列表
        api_key: API Key
        base_url: API Base URL
        model: 模型名称
        temperature: 温度参数
    
    Returns:
        Agent 节点函数
    """
    llm = ChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=api_key,
        base_url=base_url
    ).bind_tools(tools)

    async def agent_node(state: HandoffState) -> Dict[str, Any]:
        """Agent 节点执行函数"""
        logger.debug(f"Agent {agent_name} 执行, active_agent: {state.get('active_agent')}")
        
        messages = state.get("messages", [])
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        
        response = await llm.ainvoke(full_messages)
        
        # 检查工具调用
        if hasattr(response, 'tool_calls') and response.tool_calls:
            tool_call = response.tool_calls[0]
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]
            
            if not tool_args.get("reason"):
                tool_args["reason"] = "用户请求转移"

            for t in tools:
                if t.name == tool_name:
                    logger.info(f"Agent {agent_name} 调用工具: {tool_name}")
                    result = t.invoke(tool_args)
                    if isinstance(result, Command):
                        return result

        return {"messages": [response]}

    return agent_node


def create_streaming_agent_node(agent_name: str, system_prompt: str, tools: List,
                                 api_key: str, base_url: str, model: str, temperature: float = 0.7):
    """创建支持流式输出的 Agent 节点
    
    Args:
        agent_name: Agent 名称
        system_prompt: 系统提示词
        tools: 工具列表
        api_key: API Key
        base_url: API Base URL
        model: 模型名称
        temperature: 温度参数
    
    Returns:
        Agent 节点函数
    """
    llm = ChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=api_key,
        base_url=base_url,
        streaming=True
    ).bind_tools(tools)

    async def agent_node(state: HandoffState):
        """Agent 节点执行函数（流式）"""
        logger.debug(f"Agent {agent_name} 流式执行, active_agent: {state.get('active_agent')}")
        
        messages = state.get("messages", [])
        full_messages = [{"role": "system", "content": system_prompt}] + messages

        full_content = ""
        collected_tool_calls = {}
        
        async for chunk in llm.astream(full_messages):
            if chunk.content:
                full_content += chunk.content
            
            # 收集工具调用
            if hasattr(chunk, 'tool_calls') and chunk.tool_calls:
                for tc in chunk.tool_calls:
                    tc_id = tc.get("id") or "0"
                    if tc_id not in collected_tool_calls:
                        collected_tool_calls[tc_id] = {"id": tc_id, "name": "", "args": ""}
                    if tc.get("name"):
                        collected_tool_calls[tc_id]["name"] = tc["name"]
                    if tc.get("args"):
                        if isinstance(tc["args"], dict):
                            collected_tool_calls[tc_id]["args"] = tc["args"]
                        elif isinstance(tc["args"], str):
                            if isinstance(collected_tool_calls[tc_id]["args"], str):
                                collected_tool_calls[tc_id]["args"] += tc["args"]
            
            # 处理 tool_call_chunks
            if hasattr(chunk, 'tool_call_chunks') and chunk.tool_call_chunks:
                for tc_chunk in chunk.tool_call_chunks:
                    idx = str(tc_chunk.get("index", 0))
                    if idx not in collected_tool_calls:
                        collected_tool_calls[idx] = {"id": tc_chunk.get("id", idx), "name": "", "args": ""}
                    if tc_chunk.get("id"):
                        collected_tool_calls[idx]["id"] = tc_chunk["id"]
                    if tc_chunk.get("name"):
                        collected_tool_calls[idx]["name"] = tc_chunk["name"]
                    if tc_chunk.get("args"):
                        if isinstance(collected_tool_calls[idx]["args"], str):
                            collected_tool_calls[idx]["args"] += tc_chunk["args"]

        # 解析工具调用
        tool_calls_list = list(collected_tool_calls.values())
        for tc in tool_calls_list:
            if isinstance(tc.get("args"), str) and tc["args"]:
                try:
                    tc["args"] = json.loads(tc["args"])
                except (json.JSONDecodeError, ValueError):
                    tc["args"] = {}
            elif not tc.get("args"):
                tc["args"] = {}

        # 执行工具调用
        if tool_calls_list and tool_calls_list[0].get("name"):
            tool_call = tool_calls_list[0]
            tool_name = tool_call.get("name", "")
            tool_args = tool_call.get("args", {})
            
            if not tool_args.get("reason"):
                tool_args["reason"] = "用户请求转移"

            for t in tools:
                if t.name == tool_name:
                    logger.info(f"Agent {agent_name} 调用工具: {tool_name}")
                    result = t.invoke(tool_args)
                    if isinstance(result, Command):
                        return result

        return {"messages": [AIMessage(content=full_content)]}

    return agent_node


# ==================== 路由函数 ====================

def create_route_initial(default_agent: str = "sales_agent"):
    """创建初始路由函数"""
    def route_initial(state: HandoffState) -> str:
        active = state.get("active_agent")
        if active:
            return active
        return default_agent
    return route_initial


def route_after_agent(state: HandoffState) -> str:
    """Agent 执行后的路由"""
    messages = state.get("messages", [])
    if messages:
        last_msg = messages[-1]
        if isinstance(last_msg, AIMessage) and not getattr(last_msg, 'tool_calls', None):
            return END
    return state.get("active_agent", "sales_agent")


# ==================== Handoffs 服务类 ====================

class HandoffsService:
    """Handoffs 服务 - 管理多 Agent 协作"""
    
    def __init__(
        self,
        agent_configs: Dict[str, Dict] = None,
        llm_config: Dict[str, Any] = None,
        streaming: bool = True
    ):
        """初始化 Handoffs 服务
        
        Args:
            agent_configs: Agent 配置字典
            llm_config: LLM 配置
            streaming: 是否启用流式输出
        """
        self.agent_configs = agent_configs or DEFAULT_AGENT_CONFIGS
        self.llm_config = llm_config or DEFAULT_LLM_CONFIG
        self.streaming = streaming
        self._graph = None
        
        logger.info(f"HandoffsService 初始化, agents: {list(self.agent_configs.keys())}")
    
    def _build_graph(self):
        """构建 LangGraph 图"""
        builder = StateGraph(HandoffState)
        agent_names = list(self.agent_configs.keys())
        
        for agent_name in agent_names:
            config = self.agent_configs[agent_name]
            tools = create_tools_for_agent(agent_name, self.agent_configs)
            
            if self.streaming:
                agent_node = create_streaming_agent_node(
                    agent_name=agent_name,
                    system_prompt=config.get("system_prompt", f"你是 {agent_name}"),
                    tools=tools,
                    api_key=self.llm_config.get("api_key"),
                    base_url=self.llm_config.get("base_url"),
                    model=self.llm_config.get("model"),
                    temperature=self.llm_config.get("temperature", 0.7)
                )
            else:
                agent_node = create_agent_node(
                    agent_name=agent_name,
                    system_prompt=config.get("system_prompt", f"你是 {agent_name}"),
                    tools=tools,
                    api_key=self.llm_config.get("api_key"),
                    base_url=self.llm_config.get("base_url"),
                    model=self.llm_config.get("model"),
                    temperature=self.llm_config.get("temperature", 0.7)
                )
            builder.add_node(agent_name, agent_node)

        # 添加边
        default_agent = agent_names[0] if agent_names else "sales_agent"
        builder.add_conditional_edges(START, create_route_initial(default_agent), agent_names)
        
        for agent_name in agent_names:
            builder.add_conditional_edges(agent_name, route_after_agent, agent_names + [END])

        memory = MemorySaver()
        return builder.compile(checkpointer=memory)
    
    @property
    def graph(self):
        """获取图实例（懒加载）"""
        if self._graph is None:
            self._graph = self._build_graph()
        return self._graph
    
    def reset(self):
        """重置图实例"""
        self._graph = None
        logger.info("HandoffsService 图已重置")
    
    async def chat(
        self,
        message: str,
        conversation_id: str = None
    ) -> Dict[str, Any]:
        """非流式聊天
        
        Args:
            message: 用户消息
            conversation_id: 会话 ID
        
        Returns:
            聊天结果
        """
        conversation_id = conversation_id or f"conv-{uuid.uuid4().hex[:8]}"
        config = {"configurable": {"thread_id": str(conversation_id)}}
        
        logger.info(f"Handoffs chat: conversation_id={conversation_id}, message={message[:50]}...")
        
        result = await self.graph.ainvoke({
            "messages": [HumanMessage(content=message)]
        }, config=config)
        
        # 提取响应
        response_content = ""
        for msg in result.get("messages", []):
            if isinstance(msg, AIMessage):
                response_content = msg.content
                break
        
        return {
            "conversation_id": str(conversation_id),
            "active_agent": result.get("active_agent"),
            "response": response_content,
            "message_count": len(result.get("messages", []))
        }
    
    async def chat_stream(
        self,
        message: str,
        conversation_id: str = None
    ) -> AsyncGenerator[str, None]:
        """流式聊天
        
        Args:
            message: 用户消息
            conversation_id: 会话 ID
        
        Yields:
            SSE 格式的事件
        """
        conversation_id = conversation_id or f"conv-{uuid.uuid4().hex[:8]}"
        config = {"configurable": {"thread_id": str(conversation_id)}}
        
        logger.info(f"Handoffs stream chat: conversation_id={conversation_id}, message={message[:50]}...")
        
        # 发送开始事件
        yield f"event: start\ndata: {json.dumps({'conversation_id': str(conversation_id)}, ensure_ascii=False)}\n\n"
        
        current_agent = None
        
        try:
            async for event in self.graph.astream_events(
                {"messages": [HumanMessage(content=message)]},
                config=config,
                version="v2"
            ):
                kind = event["event"]
                
                # 捕获节点开始（Agent 切换）
                if kind == "on_chain_start":
                    node_name = event.get("name", "")
                    if node_name in self.agent_configs:
                        if current_agent != node_name:
                            current_agent = node_name
                            yield f"event: agent\ndata: {json.dumps({'agent': node_name}, ensure_ascii=False)}\n\n"
                
                # 捕获 LLM 流式输出
                elif kind == "on_chat_model_stream":
                    content = event["data"]["chunk"].content
                    if content:
                        yield f"event: message\ndata: {json.dumps({'content': content}, ensure_ascii=False)}\n\n"
                
                # 捕获工具调用（Handoff）
                elif kind == "on_tool_start":
                    tool_name = event.get("name", "")
                    if tool_name.startswith("transfer_to_"):
                        target_agent = tool_name.replace("transfer_to_", "")
                        yield f"event: handoff\ndata: {json.dumps({'from': current_agent, 'to': target_agent}, ensure_ascii=False)}\n\n"
            
            # 发送结束事件
            yield f"event: end\ndata: {json.dumps({'conversation_id': str(conversation_id), 'final_agent': current_agent}, ensure_ascii=False)}\n\n"
            
        except Exception as e:
            logger.error(f"Handoffs stream error: {str(e)}")
            yield f"event: error\ndata: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
    
    def get_agents(self) -> List[Dict[str, Any]]:
        """获取可用的 Agent 列表
        
        Returns:
            Agent 列表
        """
        agents = []
        for name, config in self.agent_configs.items():
            agents.append({
                "name": name,
                "description": config.get("description", ""),
                "can_transfer_to": config.get("can_transfer_to", [])
            })
        return agents


# ==================== 全局实例 ====================

_default_service: Optional[HandoffsService] = None


def get_handoffs_service(
    agent_configs: Dict[str, Dict] = None,
    llm_config: Dict[str, Any] = None,
    streaming: bool = True
) -> HandoffsService:
    """获取 Handoffs 服务实例
    
    Args:
        agent_configs: Agent 配置（可选）
        llm_config: LLM 配置（可选）
        streaming: 是否流式
    
    Returns:
        HandoffsService 实例
    """
    global _default_service
    
    # 如果有自定义配置，创建新实例
    if agent_configs or llm_config:
        return HandoffsService(agent_configs, llm_config, streaming)
    
    # 否则使用默认实例
    if _default_service is None:
        _default_service = HandoffsService(streaming=streaming)
    
    return _default_service


def reset_default_service():
    """重置默认服务实例"""
    global _default_service
    if _default_service:
        _default_service.reset()
    _default_service = None
