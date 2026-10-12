"""动态 Handoff 工具创建器

在运行时动态创建 Agent 切换工具，并注入到 LLM 的工具列表中
"""
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
