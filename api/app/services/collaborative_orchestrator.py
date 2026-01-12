"""协作编排器 - 支持 Agent 之间的动态切换和协作

基于 LangChain handoffs 模式，实现：
1. Agent 之间的动态切换（tool-based handoffs）
2. 会话上下文的保持
3. 智能路由决策
4. 协作历史追踪
"""
import json
import uuid
import time
from typing import Dict, Any, Optional, List, AsyncGenerator
from sqlalchemy.orm import Session

from app.services.agent_handoff import (
    get_handoff_manager,
    HandoffManager,
    AgentHandoffTool
)
from app.services.dynamic_handoff_tools import DynamicHandoffToolCreator
from app.core.logging_config import get_business_logger
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode
from app.core.models import RedBearLLM
from app.core.models.base import RedBearModelConfig
from app.models import ModelType

logger = get_business_logger()


class CollaborativeOrchestrator:
    """协作编排器 - 管理多 Agent 协作和切换"""
    
    def __init__(
        self,
        db: Session,
        config: Any,
        handoff_manager: Optional[HandoffManager] = None
    ):
        """初始化协作编排器
        
        Args:
            db: 数据库会话
            config: 多 Agent 配置
            handoff_manager: Handoff 管理器（可选）
        """
        self.db = db
        self.config = config
        self.handoff_manager = handoff_manager or get_handoff_manager()
        
        # 解析配置
        self.sub_agents = self._parse_sub_agents(config.sub_agents)
        self.execution_config = config.execution_config or {}
        
        # 协作模式
        self.enable_handoffs = self.execution_config.get("enable_handoffs", True)
        self.max_handoffs = self.execution_config.get("max_handoffs", 5)
        
        logger.info(
            "协作编排器初始化",
            extra={
                "sub_agent_count": len(self.sub_agents),
                "enable_handoffs": self.enable_handoffs,
                "max_handoffs": self.max_handoffs
            }
        )
    
    def _parse_sub_agents(self, sub_agents_data: List[Dict]) -> Dict[str, Any]:
        """解析子 Agent 配置
        
        Args:
            sub_agents_data: 子 Agent 配置列表
            
        Returns:
            Agent ID 到配置的映射
        """
        agents = {}
        for agent_data in sub_agents_data:
            agent_id = agent_data.get("agent_id")
            if agent_id:
                agents[str(agent_id)] = {
                    "info": agent_data,
                    "config": None  # 稍后加载
                }
        return agents
    
    async def execute_with_handoffs(
        self,
        message: str,
        conversation_id: Optional[str] = None,
        user_id: Optional[str] = None,
        variables: Optional[Dict[str, Any]] = None,
        initial_agent_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """执行支持 handoffs 的多 Agent 协作
        
        Args:
            message: 用户消息
            conversation_id: 会话 ID
            user_id: 用户 ID
            variables: 变量参数
            initial_agent_id: 初始 Agent ID（可选）
            
        Returns:
            执行结果
        """
        if not conversation_id:
            conversation_id = str(uuid.uuid4())
        
        # 1. 确定初始 Agent
        if not initial_agent_id:
            initial_agent_id = await self._select_initial_agent(message, conversation_id)
        
        # 2. 创建或获取 handoff 状态
        state = self.handoff_manager.get_state(conversation_id)
        if not state:
            state = self.handoff_manager.create_state(conversation_id, initial_agent_id)
        
        current_agent_id = state.current_agent_id
        handoff_count = 0
        conversation_history = []
        
        # 3. 执行循环（支持多次 handoff）
        while handoff_count < self.max_handoffs:
            logger.info(
                f"执行 Agent: {current_agent_id}",
                extra={
                    "conversation_id": conversation_id,
                    "handoff_count": handoff_count,
                    "message_length": len(message)
                }
            )
            
            # 3.1 生成当前 Agent 的 handoff 工具
            handoff_tools = self.handoff_manager.generate_handoff_tools(
                current_agent_id,
                self.sub_agents
            )
            
            # 3.2 执行当前 Agent
            result = await self._execute_agent(
                agent_id=current_agent_id,
                message=message,
                conversation_id=conversation_id,
                user_id=user_id,
                variables=variables,
                handoff_tools=handoff_tools,
                conversation_history=conversation_history
            )
            
            # 3.3 检查是否有 handoff 请求
            handoff_request = result.get("handoff_request")
            if not handoff_request:
                # 没有 handoff，返回结果
                return {
                    "message": result.get("message", ""),
                    "conversation_id": conversation_id,
                    "final_agent_id": current_agent_id,
                    "handoff_count": handoff_count,
                    "handoff_history": [
                        {
                            "from_agent": h.from_agent_id,
                            "to_agent": h.to_agent_id,
                            "reason": h.reason
                        }
                        for h in state.handoff_history
                    ],
                    "elapsed_time": result.get("elapsed_time", 0),
                    "usage": result.get("usage")
                }
            
            # 3.4 执行 handoff
            target_agent_id = handoff_request.get("target_agent_id")
            reason = handoff_request.get("reason", "Agent 请求切换")
            context_summary = handoff_request.get("context_summary")
            
            if target_agent_id not in self.sub_agents:
                logger.warning(f"目标 Agent 不存在: {target_agent_id}")
                # 返回当前结果
                return {
                    "message": result.get("message", ""),
                    "conversation_id": conversation_id,
                    "final_agent_id": current_agent_id,
                    "handoff_count": handoff_count,
                    "error": f"目标 Agent 不存在: {target_agent_id}",
                    "elapsed_time": result.get("elapsed_time", 0)
                }
            
            # 执行 handoff
            state = self.handoff_manager.execute_handoff(
                conversation_id=conversation_id,
                from_agent_id=current_agent_id,
                to_agent_id=target_agent_id,
                reason=reason,
                user_message=message,
                context_summary=context_summary
            )
            
            # 更新当前 Agent
            current_agent_id = target_agent_id
            handoff_count += 1
            
            # 添加到会话历史
            conversation_history.append({
                "agent_id": state.handoff_history[-1].from_agent_id,
                "message": result.get("message", ""),
                "handoff_to": target_agent_id,
                "reason": reason
            })
            
            # 如果 Agent 返回了最终答案，结束循环
            if result.get("is_final_answer"):
                return {
                    "message": result.get("message", ""),
                    "conversation_id": conversation_id,
                    "final_agent_id": current_agent_id,
                    "handoff_count": handoff_count,
                    "handoff_history": [
                        {
                            "from_agent": h.from_agent_id,
                            "to_agent": h.to_agent_id,
                            "reason": h.reason
                        }
                        for h in state.handoff_history
                    ],
                    "elapsed_time": result.get("elapsed_time", 0),
                    "usage": result.get("usage")
                }
        
        # 达到最大 handoff 次数
        logger.warning(
            f"达到最大 handoff 次数: {self.max_handoffs}",
            extra={"conversation_id": conversation_id}
        )
        
        return {
            "message": "已达到最大协作次数限制，请重新提问。",
            "conversation_id": conversation_id,
            "final_agent_id": current_agent_id,
            "handoff_count": handoff_count,
            "error": "达到最大 handoff 次数",
            "elapsed_time": 0
        }
    
    async def _select_initial_agent(
        self,
        message: str,
        conversation_id: str
    ) -> str:
        """选择初始 Agent
        
        Args:
            message: 用户消息
            conversation_id: 会话 ID
            
        Returns:
            Agent ID
        """
        # 检查是否有历史状态
        state = self.handoff_manager.get_state(conversation_id)
        if state:
            # 继续使用当前 Agent
            return state.current_agent_id
        
        # 简单的关键词匹配
        message_lower = message.lower()
        
        for agent_id, agent_data in self.sub_agents.items():
            agent_info = agent_data.get("info", {})
            capabilities = agent_info.get("capabilities", [])
            role = agent_info.get("role", "")
            
            # 检查关键词
            keywords = capabilities + ([role] if role else [])
            for keyword in keywords:
                if keyword.lower() in message_lower:
                    logger.info(
                        f"根据关键词选择初始 Agent: {agent_id}",
                        extra={"keyword": keyword}
                    )
                    return agent_id
        
        # 默认使用第一个 Agent
        default_agent_id = next(iter(self.sub_agents.keys()))
        logger.info(f"使用默认初始 Agent: {default_agent_id}")
        return default_agent_id
    
    async def _execute_agent(
        self,
        agent_id: str,
        message: str,
        conversation_id: str,
        user_id: Optional[str],
        variables: Optional[Dict[str, Any]],
        handoff_tools: List[AgentHandoffTool],
        conversation_history: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """执行单个 Agent
        
        Args:
            agent_id: Agent ID
            message: 用户消息
            conversation_id: 会话 ID
            user_id: 用户 ID
            variables: 变量参数
            handoff_tools: Handoff 工具列表
            conversation_history: 会话历史
            
        Returns:
            执行结果
        """
        start_time = time.time()
        
        try:
            # 获取 Agent 配置
            agent_data = self.sub_agents.get(agent_id)
            if not agent_data:
                raise BusinessException(
                    f"Agent 不存在: {agent_id}",
                    BizCode.RESOURCE_NOT_FOUND
                )
            
            # 加载 Agent 的完整配置
            agent_config = await self._load_agent_config(agent_id, agent_data)
            
            # 构建增强的 prompt（包含 handoff 上下文）
            enhanced_message = self._build_enhanced_message(
                message,
                conversation_id,
                agent_id,
                conversation_history
            )
            
            # 创建动态工具创建器
            tool_creator = DynamicHandoffToolCreator(agent_id, self.sub_agents)
            
            # 获取动态创建的工具
            dynamic_tools = tool_creator.get_tools_for_llm()
            
            logger.info(
                f"为 Agent {agent_id} 创建了 {len(dynamic_tools)} 个 handoff 工具",
                extra={"tool_names": tool_creator.get_tool_names()}
            )
            
            # 调用 Agent 的 LLM（注入动态工具）
            response = await self._call_agent_llm(
                agent_config=agent_config,
                message=enhanced_message,
                tools=dynamic_tools,
                conversation_history=conversation_history
            )
            
            # 构建结果
            result = {
                "message": response.get("content", ""),
                "elapsed_time": time.time() - start_time,
                "usage": response.get("usage", {"total_tokens": 0}),
                "is_final_answer": True
            }
            
            # 检查是否有工具调用（handoff）
            tool_calls = response.get("tool_calls", [])
            if tool_calls:
                for tool_call in tool_calls:
                    tool_name = tool_call.get("name")
                    tool_args = tool_call.get("arguments", {})
                    
                    # 检查是否是 handoff 工具
                    if tool_name in tool_creator.get_tool_names():
                        # 处理 handoff
                        handoff_request = tool_creator.handle_tool_call(tool_name, tool_args)
                        
                        if handoff_request:
                            result["handoff_request"] = handoff_request
                            result["is_final_answer"] = False
                            
                            logger.info(
                                f"检测到 handoff 请求: {agent_id} → {handoff_request['target_agent_id']}",
                                extra={"reason": handoff_request.get("reason")}
                            )
                            break
            
            return result
            
        except Exception as e:
            logger.error(f"Agent 执行失败: {str(e)}", exc_info=True)
            return {
                "message": f"Agent 执行出错: {str(e)}",
                "elapsed_time": time.time() - start_time,
                "error": str(e),
                "is_final_answer": True
            }
    
    async def _load_agent_config(self, agent_id: str, agent_data: Dict[str, Any]) -> Dict[str, Any]:
        """加载 Agent 的完整配置
        
        Args:
            agent_id: Agent ID
            agent_data: Agent 数据
            
        Returns:
            Agent 配置
        """
        from app.models import AppRelease
        from app.services.model_service import ModelApiKeyService
        
        # 从数据库加载 Agent Release
        try:
            agent_uuid = uuid.UUID(agent_id)
            release = self.db.get(AppRelease, agent_uuid)
            
            if not release:
                raise BusinessException(
                    f"Agent Release 不存在: {agent_id}",
                    BizCode.RESOURCE_NOT_FOUND
                )
            
            # 获取配置
            config_data = release.config or {}
            
            # 获取模型配置
            model_config_id = release.default_model_config_id
            if not model_config_id:
                raise BusinessException(
                    f"Agent 未配置模型: {agent_id}",
                    BizCode.AGENT_CONFIG_MISSING
                )
            
            # 获取 API Key
            api_key_config = ModelApiKeyService.get_a_api_key(self.db, model_config_id)
            if not api_key_config:
                raise BusinessException(
                    f"Agent 模型没有可用的 API Key: {agent_id}",
                    BizCode.API_KEY_NOT_FOUND
                )
            
            return {
                "agent_id": agent_id,
                "name": release.name,
                "system_prompt": config_data.get("system_prompt", ""),
                "model_name": api_key_config.model_name,
                "provider": api_key_config.provider,
                "api_key": api_key_config.api_key,
                "api_base": api_key_config.api_base,
                "model_parameters": config_data.get("model_parameters", {})
            }
            
        except ValueError:
            raise BusinessException(
                f"无效的 Agent ID: {agent_id}",
                BizCode.INVALID_PARAMETER
            )
    
    async def _call_agent_llm(
        self,
        agent_config: Dict[str, Any],
        message: str,
        tools: List[Dict[str, Any]],
        conversation_history: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """调用 Agent 的 LLM
        
        Args:
            agent_config: Agent 配置
            message: 消息
            tools: 工具列表（包含 handoff 工具）
            conversation_history: 会话历史
            
        Returns:
            LLM 响应
        """
        try:
            # 构建系统提示（包含工具说明）
            system_prompt = self._build_system_prompt_with_tools(
                agent_config.get("system_prompt", ""),
                tools
            )
            
            # 构建消息列表
            messages = [{"role": "system", "content": system_prompt}]
            
            # 添加历史消息（最近 5 轮）
            if conversation_history:
                for item in conversation_history[-5:]:
                    messages.append({
                        "role": "assistant",
                        "content": f"[Agent {item['agent_id']}] {item['message']}"
                    })
            
            # 添加当前消息
            messages.append({"role": "user", "content": message})
            
            # 配置 LLM
            model_params = agent_config.get("model_parameters", {})
            extra_params = {
                "temperature": model_params.get("temperature", 0.7),
                "max_tokens": model_params.get("max_tokens", 2000)
            }
            
            # 如果有工具，添加到配置中
            if tools:
                extra_params["tools"] = tools
                extra_params["tool_choice"] = "auto"
            
            model_config = RedBearModelConfig(
                model_name=agent_config["model_name"],
                provider=agent_config["provider"],
                api_key=agent_config["api_key"],
                base_url=agent_config.get("api_base"),
                extra_params=extra_params
            )
            
            # 创建 LLM 实例
            llm = RedBearLLM(model_config, type=ModelType.CHAT)
            
            # 调用 LLM
            response = await llm.ainvoke(messages)
            
            # 解析响应
            result = {
                "content": "",
                "tool_calls": [],
                "usage": {}
            }
            
            if hasattr(response, 'content'):
                result["content"] = response.content
            else:
                result["content"] = str(response)
            
            # 提取工具调用
            if hasattr(response, 'tool_calls') and response.tool_calls:
                for tool_call in response.tool_calls:
                    result["tool_calls"].append({
                        "name": tool_call.function.name if hasattr(tool_call, 'function') else tool_call.name,
                        "arguments": json.loads(tool_call.function.arguments) if hasattr(tool_call, 'function') else tool_call.arguments
                    })
            
            # 提取 usage
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                result["usage"] = {
                    "prompt_tokens": response.usage_metadata.get("input_tokens", 0),
                    "completion_tokens": response.usage_metadata.get("output_tokens", 0),
                    "total_tokens": response.usage_metadata.get("total_tokens", 0)
                }
            
            return result
            
        except Exception as e:
            logger.error(f"LLM 调用失败: {str(e)}", exc_info=True)
            raise
    
    def _build_system_prompt_with_tools(self, base_prompt: str, tools: List[Dict[str, Any]]) -> str:
        """构建包含工具说明的系统提示
        
        Args:
            base_prompt: 基础提示词
            tools: 工具列表
            
        Returns:
            增强的系统提示
        """
        if not tools:
            return base_prompt
        
        tools_desc = "\n\n## 可用的协作工具\n\n"
        tools_desc += "当你发现用户的问题超出你的专业领域时，可以使用以下工具切换到专业的 Agent：\n\n"
        
        for tool in tools:
            func = tool.get("function", {})
            tools_desc += f"- **{func.get('name')}**: {func.get('description')}\n"
        
        tools_desc += "\n请根据用户问题的性质，判断是否需要切换到其他专业 Agent。"
        tools_desc += "如果需要切换，请调用相应的工具并说明原因。"
        
        return base_prompt + tools_desc
    
    def _build_enhanced_message(
        self,
        message: str,
        conversation_id: str,
        agent_id: str,
        conversation_history: List[Dict[str, Any]]
    ) -> str:
        """构建增强的消息（包含 handoff 上下文）
        
        Args:
            message: 原始消息
            conversation_id: 会话 ID
            agent_id: 当前 Agent ID
            conversation_history: 会话历史
            
        Returns:
            增强后的消息
        """
        # 获取 handoff 上下文
        handoff_context = self.handoff_manager.get_handoff_context_for_agent(
            conversation_id,
            agent_id
        )
        
        if not handoff_context and not conversation_history:
            return message
        
        # 构建上下文前缀
        context_parts = []
        
        if handoff_context:
            context_parts.append(f"[协作上下文] {handoff_context}")
        
        if conversation_history:
            context_parts.append("[之前的对话]")
            for item in conversation_history[-3:]:  # 只保留最近3轮
                context_parts.append(
                    f"- Agent {item['agent_id']}: {item['message'][:100]}"
                )
        
        context_parts.append(f"\n[当前问题] {message}")
        
        return "\n".join(context_parts)
    
    
    def _build_tools_with_handoffs(
        self,
        handoff_tools: List[AgentHandoffTool]
    ) -> List[Dict[str, Any]]:
        """构建包含 handoff 工具的工具列表（已废弃，使用动态工具创建）
        
        Args:
            handoff_tools: Handoff 工具列表
            
        Returns:
            工具 schema 列表
        """
        # 这个方法已被 DynamicHandoffToolCreator 替代
        # 保留用于向后兼容
        tools = []
        for tool in handoff_tools:
            tools.append(tool.to_tool_schema())
        return tools
    
    async def execute_stream_with_handoffs(
        self,
        message: str,
        conversation_id: Optional[str] = None,
        user_id: Optional[str] = None,
        variables: Optional[Dict[str, Any]] = None,
        initial_agent_id: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """流式执行支持 handoffs 的多 Agent 协作
        
        Args:
            message: 用户消息
            conversation_id: 会话 ID
            user_id: 用户 ID
            variables: 变量参数
            initial_agent_id: 初始 Agent ID
            
        Yields:
            SSE 格式的事件流
        """
        if not conversation_id:
            conversation_id = str(uuid.uuid4())
        
        # 发送开始事件
        yield f"data: {json.dumps({'event': 'start', 'conversation_id': conversation_id})}\n\n"
        
        try:
            # 执行协作
            result = await self.execute_with_handoffs(
                message=message,
                conversation_id=conversation_id,
                user_id=user_id,
                variables=variables,
                initial_agent_id=initial_agent_id
            )
            
            # 发送结果事件
            yield f"data: {json.dumps({'event': 'message', 'data': result})}\n\n"
            
            # 发送结束事件
            yield f"data: {json.dumps({'event': 'end'})}\n\n"
            
        except Exception as e:
            logger.error(f"流式执行失败: {str(e)}")
            yield f"data: {json.dumps({'event': 'error', 'error': str(e)})}\n\n"
