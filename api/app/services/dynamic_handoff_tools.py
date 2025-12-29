"""动态 Handoff 工具创建器

展示如何在运行时动态创建 Agent 切换工具，并将其注入到 LLM 的工具列表中
"""
import json
from typing import Dict, Any, List, Optional, Callable
from pydantic import BaseModel, Field

from app.core.logging_config import get_business_logger

logger = get_business_logger()


class DynamicHandoffToolCreator:
    """动态 Handoff 工具创建器
    
    核心功能：
    1. 根据可用 Agent 动态生成工具定义
    2. 将工具转换为 LLM 可理解的 schema
    3. 处理工具调用并执行 handoff
    """
    
    def __init__(self, current_agent_id: str, available_agents: Dict[str, Any]):
        """初始化工具创建器
        
        Args:
            current_agent_id: 当前 Agent ID
            available_agents: 可用的 Agent 字典
        """
        self.current_agent_id = current_agent_id
        self.available_agents = available_agents
        self.tools = []
        self.tool_handlers = {}
        
        # 动态创建工具
        self._create_handoff_tools()
    
    def _create_handoff_tools(self):
        """动态创建所有 handoff 工具"""
        for agent_id, agent_data in self.available_agents.items():
            if agent_id == self.current_agent_id:
                continue  # 不创建切换到自己的工具
            
            # 创建工具
            tool_def = self._create_single_tool(agent_id, agent_data)
            self.tools.append(tool_def)
            
            # 创建工具处理器
            handler = self._create_tool_handler(agent_id, agent_data)
            self.tool_handlers[tool_def["function"]["name"]] = handler
        
        logger.info(
            f"为 Agent {self.current_agent_id} 创建了 {len(self.tools)} 个 handoff 工具"
        )
    
    def _create_single_tool(self, target_agent_id: str, agent_data: Dict[str, Any]) -> Dict[str, Any]:
        """创建单个 handoff 工具定义
        
        Args:
            target_agent_id: 目标 Agent ID
            agent_data: Agent 数据
            
        Returns:
            工具定义（OpenAI function calling 格式）
        """
        agent_info = agent_data.get("info", {})
        name = agent_info.get("name", "未命名")
        role = agent_info.get("role", "")
        capabilities = agent_info.get("capabilities", [])
        
        # 生成工具名称（符合函数命名规范）
        tool_name = f"transfer_to_{self._sanitize_name(target_agent_id)}"
        
        # 生成描述
        description = f"切换到 {name}"
        if role:
            description += f"（{role}）"
        if capabilities:
            cap_str = "、".join(capabilities[:3])
            description += f"。擅长: {cap_str}"
        description += "。当用户的问题更适合该 Agent 处理时调用此工具。"
        
        # 构建工具定义（OpenAI function calling 格式）
        tool_def = {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "为什么要切换到该 Agent？请简要说明原因。"
                        },
                        "context_summary": {
                            "type": "string",
                            "description": "需要传递给目标 Agent 的上下文摘要（可选）。例如：之前的计算结果、用户的具体需求等。"
                        }
                    },
                    "required": ["reason"]
                }
            }
        }
        
        return tool_def
    
    def _create_tool_handler(
        self,
        target_agent_id: str,
        agent_data: Dict[str, Any]
    ) -> Callable:
        """创建工具处理器函数
        
        Args:
            target_agent_id: 目标 Agent ID
            agent_data: Agent 数据
            
        Returns:
            工具处理器函数
        """
        def handler(reason: str, context_summary: Optional[str] = None) -> Dict[str, Any]:
            """处理 handoff 工具调用
            
            Args:
                reason: 切换原因
                context_summary: 上下文摘要
                
            Returns:
                Handoff 请求
            """
            agent_info = agent_data.get("info", {})
            
            logger.info(
                f"Handoff 工具被调用: {self.current_agent_id} → {target_agent_id}",
                extra={
                    "reason": reason,
                    "has_context": bool(context_summary)
                }
            )
            
            return {
                "type": "handoff",
                "target_agent_id": target_agent_id,
                "target_agent_name": agent_info.get("name", ""),
                "reason": reason,
                "context_summary": context_summary,
                "from_agent_id": self.current_agent_id
            }
        
        return handler
    
    def _sanitize_name(self, name: str) -> str:
        """清理名称，使其符合函数命名规范
        
        Args:
            name: 原始名称
            
        Returns:
            清理后的名称
        """
        # 替换特殊字符为下划线
        sanitized = name.replace("-", "_").replace(" ", "_")
        # 移除其他非法字符
        sanitized = "".join(c for c in sanitized if c.isalnum() or c == "_")
        return sanitized.lower()
    
    def get_tools_for_llm(self) -> List[Dict[str, Any]]:
        """获取用于 LLM 的工具列表
        
        Returns:
            工具定义列表（OpenAI function calling 格式）
        """
        return self.tools
    
    def handle_tool_call(self, tool_name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """处理 LLM 的工具调用
        
        Args:
            tool_name: 工具名称
            arguments: 工具参数
            
        Returns:
            Handoff 请求或 None
        """
        handler = self.tool_handlers.get(tool_name)
        if not handler:
            logger.warning(f"未找到工具处理器: {tool_name}")
            return None
        
        try:
            return handler(**arguments)
        except Exception as e:
            logger.error(f"工具调用失败: {tool_name}, 错误: {str(e)}")
            return None
    
    def get_tool_names(self) -> List[str]:
        """获取所有工具名称
        
        Returns:
            工具名称列表
        """
        return [tool["function"]["name"] for tool in self.tools]


# ==================== 使用示例 ====================

def example_usage():
    """展示如何使用动态工具创建器"""
    
    # 1. 准备可用的 Agent 信息
    available_agents = {
        "math-agent-uuid": {
            "info": {
                "name": "数学助手",
                "role": "数学专家",
                "capabilities": ["数学计算", "方程求解", "几何问题"]
            }
        },
        "creative-agent-uuid": {
            "info": {
                "name": "创意助手",
                "role": "创意专家",
                "capabilities": ["写作", "诗歌", "故事创作"]
            }
        },
        "code-agent-uuid": {
            "info": {
                "name": "代码助手",
                "role": "编程专家",
                "capabilities": ["代码编写", "调试", "代码审查"]
            }
        }
    }
    
    # 2. 为当前 Agent 创建工具
    current_agent_id = "general-agent-uuid"
    tool_creator = DynamicHandoffToolCreator(current_agent_id, available_agents)
    
    # 3. 获取工具定义（用于 LLM）
    tools = tool_creator.get_tools_for_llm()
    
    print("=" * 60)
    print("动态创建的 Handoff 工具:")
    print("=" * 60)
    for tool in tools:
        print(f"\n工具名称: {tool['function']['name']}")
        print(f"描述: {tool['function']['description']}")
        print(f"参数: {json.dumps(tool['function']['parameters'], indent=2, ensure_ascii=False)}")
    
    # 4. 模拟 LLM 调用工具
    print("\n" + "=" * 60)
    print("模拟 LLM 工具调用:")
    print("=" * 60)
    
    # LLM 决定切换到数学 Agent
    tool_call = {
        "name": "transfer_to_math_agent_uuid",
        "arguments": {
            "reason": "用户问题涉及数学计算",
            "context_summary": "用户想解方程 x^2 + 5x + 6 = 0"
        }
    }
    
    print(f"\nLLM 调用: {tool_call['name']}")
    print(f"参数: {json.dumps(tool_call['arguments'], indent=2, ensure_ascii=False)}")
    
    # 5. 处理工具调用
    handoff_request = tool_creator.handle_tool_call(
        tool_call["name"],
        tool_call["arguments"]
    )
    
    if handoff_request:
        print(f"\n✓ Handoff 请求已创建:")
        print(f"  类型: {handoff_request['type']}")
        print(f"  从: {handoff_request['from_agent_id']}")
        print(f"  到: {handoff_request['target_agent_id']} ({handoff_request['target_agent_name']})")
        print(f"  原因: {handoff_request['reason']}")
        print(f"  上下文: {handoff_request['context_summary']}")


# ==================== 与 LLM 集成示例 ====================

async def integrate_with_llm_example():
    """展示如何将动态工具集成到 LLM 调用中"""
    from app.core.models import RedBearLLM
    from app.core.models.base import RedBearModelConfig
    
    # 1. 准备 Agent 信息
    available_agents = {
        "math-agent": {
            "info": {
                "name": "数学助手",
                "role": "数学专家",
                "capabilities": ["计算", "方程"]
            }
        }
    }
    
    # 2. 创建工具
    tool_creator = DynamicHandoffToolCreator("general-agent", available_agents)
    tools = tool_creator.get_tools_for_llm()
    
    # 3. 构建 LLM 配置
    model_config = RedBearModelConfig(
        model_name="gpt-4",
        provider="openai",
        api_key="your-api-key",
        extra_params={
            "temperature": 0.7,
            "tools": tools,  # 注入动态创建的工具
            "tool_choice": "auto"  # 让 LLM 自动决定是否调用工具
        }
    )
    
    # 4. 创建 LLM 实例
    llm = RedBearLLM(model_config)
    
    # 5. 构建消息（包含系统提示）
    system_prompt = """你是一个智能助手。当用户的问题超出你的能力范围时，你可以使用工具切换到专业的 Agent。

可用的切换工具：
- transfer_to_math_agent: 当遇到数学问题时使用

请根据用户问题判断是否需要切换。"""
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "帮我计算 3*6+15 的结果"}
    ]
    
    # 6. 调用 LLM
    response = await llm.ainvoke(messages)
    
    # 7. 检查是否有工具调用
    if hasattr(response, 'tool_calls') and response.tool_calls:
        for tool_call in response.tool_calls:
            print(f"\n✓ LLM 调用了工具: {tool_call.name}")
            print(f"  参数: {tool_call.arguments}")
            
            # 处理工具调用
            handoff_request = tool_creator.handle_tool_call(
                tool_call.name,
                tool_call.arguments
            )
            
            if handoff_request:
                print(f"\n✓ 执行 Handoff:")
                print(f"  切换到: {handoff_request['target_agent_name']}")
                print(f"  原因: {handoff_request['reason']}")
                
                # 这里可以执行实际的 Agent 切换逻辑
                # await execute_handoff(handoff_request)
    else:
        print(f"\n✓ LLM 直接回复: {response.content}")


# ==================== 完整的 Agent 执行流程 ====================

class AgentExecutorWithHandoffs:
    """支持 Handoffs 的 Agent 执行器"""
    
    def __init__(self, agent_id: str, agent_config: Any, available_agents: Dict[str, Any]):
        """初始化执行器
        
        Args:
            agent_id: 当前 Agent ID
            agent_config: Agent 配置
            available_agents: 可用的其他 Agent
        """
        self.agent_id = agent_id
        self.agent_config = agent_config
        self.available_agents = available_agents
        
        # 创建工具创建器
        self.tool_creator = DynamicHandoffToolCreator(agent_id, available_agents)
    
    async def execute(
        self,
        message: str,
        conversation_history: List[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """执行 Agent（支持 handoff）
        
        Args:
            message: 用户消息
            conversation_history: 会话历史
            
        Returns:
            执行结果，可能包含 handoff_request
        """
        from app.core.models import RedBearLLM
        from app.core.models.base import RedBearModelConfig
        
        # 1. 获取动态工具
        tools = self.tool_creator.get_tools_for_llm()
        
        # 2. 构建系统提示（包含工具说明）
        system_prompt = self._build_system_prompt_with_tools()
        
        # 3. 构建消息
        messages = [{"role": "system", "content": system_prompt}]
        
        # 添加历史消息
        if conversation_history:
            messages.extend(conversation_history)
        
        # 添加当前消息
        messages.append({"role": "user", "content": message})
        
        # 4. 配置 LLM（注入工具）
        model_config = RedBearModelConfig(
            model_name=self.agent_config.model_name,
            provider=self.agent_config.provider,
            api_key=self.agent_config.api_key,
            extra_params={
                "temperature": 0.7,
                "tools": tools,  # 动态工具
                "tool_choice": "auto"
            }
        )
        
        llm = RedBearLLM(model_config)
        
        # 5. 调用 LLM
        response = await llm.ainvoke(messages)
        
        # 6. 处理响应
        result = {
            "message": response.content if hasattr(response, 'content') else str(response),
            "agent_id": self.agent_id
        }
        
        # 7. 检查工具调用
        if hasattr(response, 'tool_calls') and response.tool_calls:
            for tool_call in response.tool_calls:
                # 检查是否是 handoff 工具
                if tool_call.name in self.tool_creator.get_tool_names():
                    # 处理 handoff
                    handoff_request = self.tool_creator.handle_tool_call(
                        tool_call.name,
                        tool_call.arguments
                    )
                    
                    if handoff_request:
                        result["handoff_request"] = handoff_request
                        result["is_final_answer"] = False
                        break
                else:
                    # 处理其他业务工具
                    pass
        
        return result
    
    def _build_system_prompt_with_tools(self) -> str:
        """构建包含工具说明的系统提示"""
        base_prompt = self.agent_config.system_prompt or "你是一个智能助手。"
        
        # 添加工具说明
        tool_names = self.tool_creator.get_tool_names()
        if tool_names:
            tools_desc = "\n\n你可以使用以下工具切换到专业的 Agent：\n"
            for tool in self.tool_creator.get_tools_for_llm():
                tools_desc += f"- {tool['function']['name']}: {tool['function']['description']}\n"
            
            tools_desc += "\n当用户的问题超出你的能力范围时，请使用相应的工具切换到专业 Agent。"
            
            return base_prompt + tools_desc
        
        return base_prompt


if __name__ == "__main__":
    # 运行示例
    print("\n🚀 动态 Handoff 工具创建示例\n")
    example_usage()
    
    print("\n\n" + "=" * 60)
    print("完成！查看上面的输出了解工具创建流程。")
    print("=" * 60)
