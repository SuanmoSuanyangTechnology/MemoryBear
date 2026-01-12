"""Handoffs 服务 - 基于 LangGraph 的多 Agent 协作"""
import json
import uuid
from typing import List, Dict, Any, Optional, AsyncGenerator, Annotated
from typing_extensions import TypedDict

from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
import operator

from app.core.logging_config import get_business_logger
from app.core.models import RedBearLLM, RedBearModelConfig
from app.models.models_model import ModelType
from app.services.model_service import ModelApiKeyService

logger = get_business_logger()


# ==================== Reducer 函数 ====================

def replace_value(current, new):
    """替换值的 reducer - 总是使用新值"""
    return new


# ==================== 状态定义 ====================

class HandoffState(TypedDict):
    """Handoff 状态"""
    messages: Annotated[List[BaseMessage], operator.add]  # 消息列表追加
    active_agent: Annotated[Optional[str], replace_value]
    handoff_count: Annotated[int, replace_value]
    handoff_history: Annotated[List[str], replace_value]
    pending_question: Annotated[Optional[str], replace_value]
    previous_answer: Annotated[Optional[str], replace_value]


# ==================== 常量 ====================

MAX_HANDOFFS = 5  # 最大 handoff 次数


# ==================== 工具输入模型 ====================

class TransferInput(BaseModel):
    """转移工具的输入参数"""
    reason: str = Field(description="转移原因，说明为什么需要转交")
    unhandled_question: str = Field(
        description="需要转交给其他专家处理的具体问题。注意：只转交你无法回答的部分，不要转交整个原始问题"
    )
    your_answer: str = Field(
        default="",
        description="你已经回答的内容摘要（如果有的话）。如果你已经回答了部分问题，在这里简要说明"
    )


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
    def transfer_tool(reason: str, unhandled_question: str, your_answer: str = "") -> Command:
        """动态生成的转移工具
        
        Args:
            reason: 转移原因
            unhandled_question: 需要转交的具体问题（只转交未处理的部分）
            your_answer: 你已经回答的内容摘要
        """
        return Command(
            goto=target_agent,
            update={
                "active_agent": target_agent,
                "pending_question": unhandled_question,  # 存储要转交的具体问题
                "previous_answer": your_answer,  # 存储之前的回答
                # handoff_count 和 handoff_history 在 agent_node 中更新
            },
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
        
        # 获取当前 handoff 状态
        handoff_count = state.get("handoff_count", 0)
        handoff_history = state.get("handoff_history", [])
        pending_question = state.get("pending_question")
        previous_answer = state.get("previous_answer", "")
        
        # 检查是否达到最大 handoff 次数
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
        
        # 如果有 pending_question，构建新的消息上下文
        if pending_question and handoff_count > 0:
            # 构建包含上下文的消息
            context_msg = f"【来自其他专家的转交】\n"
            if previous_answer:
                context_msg += f"之前的专家已经回答了: {previous_answer}\n\n"
            context_msg += f"现在需要你回答的问题是: {pending_question}\n\n"
            if handoff_history:
                context_msg += f"【注意】以下专家已经处理过这个问题，不能再转交给他们: {', '.join(handoff_history)}"
            
            # 使用转交的具体问题，而不是原始消息
            effective_messages = [HumanMessage(content=context_msg)]
            logger.info(f"Agent {agent_name} 收到转交问题（非流式）: {pending_question[:100]}...")
        else:
            effective_messages = messages
        
        full_messages = [{"role": "system", "content": system_prompt}] + effective_messages
        
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
            
            # 确保必要的参数存在
            if not tool_args.get("reason"):
                tool_args["reason"] = "用户请求转移"
            
            # 获取 LLM 提供的 unhandled_question
            llm_unhandled_question = tool_args.get("unhandled_question", "")
            
            # 提取目标 agent
            target_agent = tool_name.replace("transfer_to_", "")
            
            # 检查是否会形成循环：目标 Agent 是否已经在 handoff_history 中
            if target_agent in handoff_history:
                logger.warning(f"Agent {agent_name} 尝试移交给已处理过的 {target_agent}，强制直接回复")
                return {
                    "messages": [AIMessage(content="抱歉，这个问题超出了我的专业范围，我无法回答。")],
                    "handoff_count": handoff_count,
                    "handoff_history": handoff_history,
                    "pending_question": None,
                    "previous_answer": ""
                }
            
            # 第一次转交，检查是否提供了 unhandled_question
            if not llm_unhandled_question:
                # 使用原始消息
                last_human_msg = next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)
                llm_unhandled_question = last_human_msg.content if last_human_msg else ""
            
            tool_args["unhandled_question"] = llm_unhandled_question

            for t in tools:
                if t.name == tool_name:
                    # 提取目标 agent
                    target_agent = tool_name.replace("transfer_to_", "")
                    new_history = handoff_history + [agent_name]
                    
                    logger.info(f"Agent {agent_name} handoff 到 {target_agent} (count: {handoff_count + 1}), 转交问题: {tool_args.get('unhandled_question', '')[:50]}...")
                    
                    # 返回 Command 并更新 handoff 状态
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
            "pending_question": None,  # 清除 pending_question
            "previous_answer": ""
        }

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
        
        # 获取当前 handoff 状态
        handoff_count = state.get("handoff_count", 0)
        handoff_history = state.get("handoff_history", [])
        pending_question = state.get("pending_question")
        previous_answer = state.get("previous_answer", "")
        
        logger.info(f"Agent {agent_name} 状态: handoff_count={handoff_count}, pending_question={pending_question}, previous_answer={previous_answer[:50] if previous_answer else ''}")
        
        # 检查是否达到最大 handoff 次数
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
        
        # 如果有 pending_question，构建新的消息上下文
        if pending_question and handoff_count > 0:
            # 构建包含上下文的消息
            context_msg = f"【来自其他专家的转交】\n"
            if previous_answer:
                context_msg += f"之前的专家已经回答了: {previous_answer}\n\n"
            context_msg += f"现在需要你回答的问题是: {pending_question}\n\n"
            if handoff_history:
                context_msg += f"【注意】以下专家已经处理过这个问题，不能再转交给他们: {', '.join(handoff_history)}"
            
            # 使用转交的具体问题，而不是原始消息
            effective_messages = [HumanMessage(content=context_msg)]
            logger.info(f"Agent {agent_name} 收到转交问题（流式）: {pending_question[:100]}...")
        else:
            effective_messages = messages
        
        full_messages = [{"role": "system", "content": system_prompt}] + effective_messages

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

        # 执行工具调用 - 选择参数最完整的工具调用
        if tool_calls_list:
            # 找到参数最完整的 transfer 工具调用
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
                    # 计算参数完整度
                    args_len = len(str(tc_args.get("unhandled_question", ""))) + len(str(tc_args.get("reason", "")))
                    if args_len > best_args_len:
                        best_args_len = args_len
                        best_tool_call = (tc_name, tc_args)
            
            if best_tool_call:
                tool_name, tool_args = best_tool_call
                
                # 确保必要的参数存在
                if not tool_args.get("reason"):
                    tool_args["reason"] = "用户请求转移"
                
                # 获取 LLM 提供的 unhandled_question
                llm_unhandled_question = tool_args.get("unhandled_question", "")
                
                # 提取目标 agent
                target_agent = tool_name.replace("transfer_to_", "")
                
                # 检查是否会形成循环：目标 Agent 是否已经在 handoff_history 中
                if target_agent in handoff_history:
                    logger.warning(f"Agent {agent_name} 尝试移交给已处理过的 {target_agent}，强制直接回复")
                    return {
                        "messages": [AIMessage(content=full_content if full_content else "抱歉，这个问题超出了我的专业范围，我无法回答。")],
                        "handoff_count": handoff_count,
                        "handoff_history": handoff_history,
                        "pending_question": None,
                        "previous_answer": ""
                    }
                
                # 检查是否提供了 unhandled_question
                if not llm_unhandled_question:
                    # 使用原始消息
                    last_human_msg = next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)
                    llm_unhandled_question = last_human_msg.content if last_human_msg else ""
                
                new_history = handoff_history + [agent_name]
                
                logger.info(f"Agent {agent_name} handoff 到 {target_agent} (count: {handoff_count + 1}), 转交问题: {llm_unhandled_question[:100]}...")
                
                # 返回 Command 并更新 handoff 状态
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
            "pending_question": None,  # 清除 pending_question
            "previous_answer": ""
        }

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
    """Agent 执行后的路由
    
    检查：
    1. 是否达到最大 handoff 次数
    2. 是否形成循环（连续在两个 Agent 之间切换）
    3. 最后一条消息是否有 tool_calls
    """
    messages = state.get("messages", [])
    handoff_count = state.get("handoff_count", 0)
    handoff_history = state.get("handoff_history", [])
    
    # 检查是否达到最大 handoff 次数
    if handoff_count >= MAX_HANDOFFS:
        logger.warning(f"达到最大 handoff 次数 ({MAX_HANDOFFS})，强制结束")
        return END
    
    # 检查是否形成循环（A -> B -> A -> B 模式）
    if len(handoff_history) >= 4:
        # 检查最近 4 次是否形成 A-B-A-B 循环
        recent = handoff_history[-4:]
        if recent[0] == recent[2] and recent[1] == recent[3] and recent[0] != recent[1]:
            logger.warning(f"检测到循环 handoff: {recent}，强制结束")
            return END
    
    # 检查最后一条消息
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
        current_capabilities = agent_configs[safe_name].get("capabilities", [])
        
        if other_agents:
            # 构建其他 Agent 的专长信息
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
            "messages": [HumanMessage(content=message)],
            "handoff_count": 0,
            "handoff_history": [],
            "pending_question": None,
            "previous_answer": ""
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
            "message_count": len(result.get("messages", [])),
            "handoff_count": result.get("handoff_count", 0)
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
        handoff_count = 0
        collected_tool_calls = {}  # 收集工具调用信息
        
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
                    chunk = event["data"]["chunk"]
                    content = chunk.content if hasattr(chunk, 'content') else ""
                    if content:
                        yield f"event: message\ndata: {json.dumps({'content': content, 'agent': current_agent}, ensure_ascii=False)}\n\n"
                    
                    # 收集工具调用信息
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
                
                # 捕获 LLM 结束事件，输出收集到的工具调用
                elif kind == "on_chat_model_end":
                    if collected_tool_calls:
                        # 找到参数最完整的 transfer 工具调用
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
                                # 计算参数完整度（有 unhandled_question 的优先）
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
                        collected_tool_calls = {}  # 清空，准备收集下一个 Agent 的工具调用
            
            # 发送结束事件
            yield f"event: end\ndata: {json.dumps({'conversation_id': str(conversation_id), 'final_agent': current_agent, 'total_handoffs': handoff_count}, ensure_ascii=False)}\n\n"
            
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
