"""Handoffs 服务 - 基于 LangGraph 的多 Agent 协作"""
import json
import uuid
from typing import List, Dict, Any, Optional, AsyncGenerator
from typing_extensions import TypedDict

from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.logging_config import get_business_logger
from app.core.models import RedBearLLM, RedBearModelConfig
from app.models.models_model import ModelType
from app.services.model_service import ModelApiKeyService

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
                      model_config: RedBearModelConfig):
    """创建 Agent 节点（非流式）"""
    llm = RedBearLLM(model_config, type=ModelType.CHAT)
    
    # 绑定工具
    if tools:
        llm = llm.bind_tools(tools)

    async def agent_node(state: HandoffState) -> Dict[str, Any]:
        """Agent 节点执行函数"""
        logger.debug(f"Agent {agent_name} 执行, active_agent: {state.get('active_agent')}")
        
        messages = state.get("messages", [])
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        
        response = await llm.ainvoke(full_messages)
        
        # 检查工具调用
        if hasattr(response, 'tool_calls') and response.tool_calls:
            tool_call = response.tool_calls[0]
            tool_name = tool_call["name"] if isinstance(tool_call, dict) else tool_call.name
            tool_args = tool_call["args"] if isinstance(tool_call, dict) else tool_call.args
            
            if isinstance(tool_args, str):
                try:
                    tool_args = json.loads(tool_args)
                except (json.JSONDecodeError, ValueError):
                    tool_args = {}
            
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
                                 model_config: RedBearModelConfig):
    """创建支持流式输出的 Agent 节点"""
    llm = RedBearLLM(model_config, type=ModelType.CHAT)
    
    # 绑定工具
    if tools:
        llm = llm.bind_tools(tools)

    async def agent_node(state: HandoffState):
        """Agent 节点执行函数（流式）"""
        logger.debug(f"Agent {agent_name} 流式执行, active_agent: {state.get('active_agent')}")
        
        messages = state.get("messages", [])
        full_messages = [{"role": "system", "content": system_prompt}] + messages

        full_content = ""
        collected_tool_calls = {}
        
        async for chunk in llm.astream(full_messages):
            if hasattr(chunk, 'content') and chunk.content:
                full_content += chunk.content
            
            # 收集工具调用
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
            
            # 处理 tool_call_chunks
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

def create_route_initial(default_agent: str):
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
    return state.get("active_agent", END)


# ==================== 配置转换 ====================

def convert_multi_agent_config_to_handoffs(
    multi_agent_config: Dict,
    db: Session
) -> Dict[str, Dict]:
    """将 multi_agent_config 转换为 handoffs 配置格式
    
    Args:
        multi_agent_config: 数据库中的多 Agent 配置
        db: 数据库会话
    
    Returns:
        agent_configs 字典，每个 Agent 包含自己的 model_config
    """
    from app.models import AppRelease, App
    
    sub_agents = multi_agent_config.get("sub_agents", [])
    agent_configs = {}
    agent_names = []
    
    # 遍历子 Agent，构建配置
    for sub_agent in sub_agents:
        agent_id = sub_agent.get("agent_id")  # 可能是 release_id 或 app_id
        agent_name = sub_agent.get("name", f"agent_{agent_id[:8] if agent_id else 'unknown'}")
        # 使用安全的 agent name（去除特殊字符）
        safe_name = agent_name.replace(" ", "_").replace("-", "_").lower()
        agent_names.append(safe_name)
        
        # 从 AppRelease 获取 Agent 的系统提示词和模型配置
        system_prompt = f"你是 {agent_name}。"
        capabilities = sub_agent.get("capabilities", [])
        model_config = None
        release = None
        
        if agent_id:
            try:
                agent_id_uuid = uuid.UUID(agent_id) if isinstance(agent_id, str) else agent_id
                
                # 先尝试作为 release_id 查询
                release = db.get(AppRelease, agent_id_uuid)
                
                # 如果找不到，尝试作为 app_id 查询，获取 current_release
                if not release:
                    app = db.get(App, agent_id_uuid)
                    if app and app.current_release_id:
                        release = db.get(AppRelease, app.current_release_id)
                
                if release:
                    # 从 release.config 获取 system_prompt
                    if release.config:
                        config_data = release.config
                        release_system_prompt = config_data.get("system_prompt")
                        if release_system_prompt:
                            system_prompt = release_system_prompt
                    
                    # 获取该 Agent 的模型配置
                    if release.default_model_config_id:
                        model_api_key = ModelApiKeyService.get_a_api_key(db, release.default_model_config_id)
                        if model_api_key:
                            model_config = RedBearModelConfig(
                                model_name=model_api_key.model_name,
                                provider=model_api_key.provider,
                                api_key=model_api_key.api_key,
                                base_url=model_api_key.api_base,
                                extra_params={
                                    "temperature": 0.7,
                                    "max_tokens": 2000,
                                    "streaming": True
                                }
                            )
                            logger.debug(f"Agent {agent_name} 使用模型: {model_api_key.model_name}")
                        else:
                            logger.warning(f"Agent {agent_name} 模型配置无效: {release.default_model_config_id}")
                    else:
                        logger.warning(f"Agent {agent_name} 没有配置 default_model_config_id")
                else:
                    logger.warning(f"Agent {agent_name} 找不到发布版本: agent_id={agent_id}")
            except Exception as e:
                logger.warning(f"获取 Agent {agent_name} 配置失败: {str(e)}")
        
        # 如果有 capabilities，添加到系统提示词
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
            "model_config": model_config,  # 每个 Agent 自己的模型配置
            "can_transfer_to": []  # 稍后填充
        }
    
    # 设置每个 Agent 可以转移到的其他 Agent
    for safe_name in agent_names:
        agent_configs[safe_name]["can_transfer_to"] = [
            name for name in agent_names if name != safe_name
        ]
        # 更新系统提示词，添加转移说明
        other_agents = agent_configs[safe_name]["can_transfer_to"]
        if other_agents:
            transfer_instructions = "\n如果用户的问题不在你的专长范围内，可以使用以下工具转移到其他 Agent："
            for other_name in other_agents:
                other_config = agent_configs[other_name]
                transfer_instructions += f"\n- transfer_to_{other_name}: {other_config['description']}"
            agent_configs[safe_name]["system_prompt"] += transfer_instructions
    
    return agent_configs


# ==================== Handoffs 服务类 ====================

class HandoffsService:
    """Handoffs 服务 - 管理多 Agent 协作"""
    
    def __init__(
        self,
        agent_configs: Dict[str, Dict],
        streaming: bool = True
    ):
        """初始化 Handoffs 服务
        
        Args:
            agent_configs: Agent 配置字典，每个 Agent 包含自己的 model_config
            streaming: 是否启用流式输出
        """
        self.agent_configs = agent_configs
        self.streaming = streaming
        self._graph = None
        
        # 验证每个 Agent 都有模型配置
        for agent_name, config in agent_configs.items():
            if not config.get("model_config"):
                raise ValueError(f"Agent {agent_name} 没有配置模型")
        
        logger.info(f"HandoffsService 初始化, agents: {list(self.agent_configs.keys())}")
    
    def _build_graph(self):
        """构建 LangGraph 图"""
        builder = StateGraph(HandoffState)
        agent_names = list(self.agent_configs.keys())
        
        if not agent_names:
            
            raise ValueError("至少需要一个 Agent 配置")
        
        for agent_name in agent_names:
            config = self.agent_configs[agent_name]
            tools = create_tools_for_agent(agent_name, self.agent_configs)
            
            # 使用每个 Agent 自己的模型配置
            agent_model_config = config.get("model_config")
            
            if self.streaming:
                agent_node = create_streaming_agent_node(
                    agent_name=agent_name,
                    system_prompt=config.get("system_prompt", f"你是 {agent_name}"),
                    tools=tools,
                    model_config=agent_model_config
                )
            else:
                agent_node = create_agent_node(
                    agent_name=agent_name,
                    system_prompt=config.get("system_prompt", f"你是 {agent_name}"),
                    tools=tools,
                    model_config=agent_model_config
                )
            builder.add_node(agent_name, agent_node)

        # 添加边
        default_agent = agent_names[0]
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
        """非流式聊天"""
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
        """流式聊天"""
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
                            agent_display_name = self.agent_configs[node_name].get("name", node_name)
                            yield f"event: agent\ndata: {json.dumps({'agent': node_name, 'agent_name': agent_display_name}, ensure_ascii=False)}\n\n"
                
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
                        target_name = self.agent_configs.get(target_agent, {}).get("name", target_agent)
                        yield f"event: handoff\ndata: {json.dumps({'from': current_agent, 'to': target_agent, 'to_name': target_name}, ensure_ascii=False)}\n\n"
            
            # 发送结束事件
            yield f"event: end\ndata: {json.dumps({'conversation_id': str(conversation_id), 'final_agent': current_agent}, ensure_ascii=False)}\n\n"
            
        except Exception as e:
            logger.error(f"Handoffs stream error: {str(e)}")
            yield f"event: error\ndata: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
    
    def get_agents(self) -> List[Dict[str, Any]]:
        """获取可用的 Agent 列表"""
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


# ==================== 服务工厂 ====================

# 缓存服务实例（按 app_id）
_service_cache: Dict[str, HandoffsService] = {}


def get_handoffs_service_for_app(
    app_id: uuid.UUID,
    db: Session,
    streaming: bool = True
) -> HandoffsService:
    """根据 app_id 获取 Handoffs 服务实例
    
    Args:
        app_id: 应用 ID
        db: 数据库会话
        streaming: 是否流式
    
    Returns:
        HandoffsService 实例
    """
    from app.services.multi_agent_service import MultiAgentService
    
    cache_key = f"{app_id}_{streaming}"
    
    # 检查缓存
    if cache_key in _service_cache:
        return _service_cache[cache_key]
    
    # 获取多 Agent 配置
    multi_agent_service = MultiAgentService(db)
    multi_agent_config = multi_agent_service.get_multi_agent_configs(app_id)
    
    if not multi_agent_config:
        raise ValueError(f"应用 {app_id} 没有多 Agent 配置")
    
    # 转换配置（每个 Agent 包含自己的 model_config）
    agent_configs = convert_multi_agent_config_to_handoffs(multi_agent_config, db)
    
    if not agent_configs:
        raise ValueError(f"应用 {app_id} 没有配置子 Agent")
    
    # 创建服务
    service = HandoffsService(agent_configs, streaming)
    
    # 缓存
    _service_cache[cache_key] = service
    
    return service


def reset_handoffs_service_cache(app_id: uuid.UUID = None):
    """重置服务缓存
    
    Args:
        app_id: 应用 ID，如果为 None 则清除所有缓存
    """
    global _service_cache
    
    if app_id:
        keys_to_remove = [k for k in _service_cache if k.startswith(str(app_id))]
        for key in keys_to_remove:
            del _service_cache[key]
    else:
        _service_cache = {}
    
    logger.info(f"Handoffs 服务缓存已重置: app_id={app_id}")
