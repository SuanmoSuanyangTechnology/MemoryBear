"""Agent Handoff 机制 - 实现 Agent 之间的动态切换和协作

基于 LangChain 的 handoffs 模式，支持：
1. Agent 之间的动态切换（transfer）
2. 工具驱动的状态转换
3. 会话上下文的保持
4. 协作历史的追踪
"""
import uuid
from typing import Dict, Any, Optional, List
from datetime import datetime
from pydantic import BaseModel, Field

from app.core.logging_config import get_business_logger

logger = get_business_logger()


class HandoffContext(BaseModel):
    """Handoff 上下文信息"""
    from_agent_id: str = Field(..., description="源 Agent ID")
    to_agent_id: str = Field(..., description="目标 Agent ID")
    reason: str = Field(..., description="切换原因")
    timestamp: datetime = Field(default_factory=datetime.now)
    user_message: Optional[str] = Field(None, description="触发切换的用户消息")
    context_summary: Optional[str] = Field(None, description="上下文摘要")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class AgentHandoffTool(BaseModel):
    """Agent Handoff 工具定义"""
    name: str = Field(..., description="工具名称，如 transfer_to_math_agent")
    target_agent_id: str = Field(..., description="目标 Agent ID")
    target_agent_name: str = Field(..., description="目标 Agent 名称")
    description: str = Field(..., description="工具描述")
    trigger_keywords: List[str] = Field(default_factory=list, description="触发关键词")
    
    def to_tool_schema(self) -> Dict[str, Any]:
        """转换为 LLM 工具 schema"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "切换到该 Agent 的原因"
                        },
                        "context_summary": {
                            "type": "string",
                            "description": "需要传递给目标 Agent 的上下文摘要（可选）"
                        }
                    },
                    "required": ["reason"]
                }
            }
        }


class HandoffState(BaseModel):
    """Handoff 状态管理"""
    conversation_id: str = Field(..., description="会话 ID")
    current_agent_id: str = Field(..., description="当前活跃的 Agent ID")
    handoff_history: List[HandoffContext] = Field(default_factory=list, description="切换历史")
    context_data: Dict[str, Any] = Field(default_factory=dict, description="共享上下文数据")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    
    def add_handoff(self, context: HandoffContext):
        """添加 handoff 记录"""
        self.handoff_history.append(context)
        self.current_agent_id = context.to_agent_id
        self.updated_at = datetime.now()
        
        logger.info(
            "Agent handoff 记录",
            extra={
                "conversation_id": self.conversation_id,
                "from_agent": context.from_agent_id,
                "to_agent": context.to_agent_id,
                "reason": context.reason
            }
        )
    
    def get_recent_handoffs(self, limit: int = 5) -> List[HandoffContext]:
        """获取最近的 handoff 记录"""
        return self.handoff_history[-limit:] if self.handoff_history else []
    
    def get_handoff_count(self) -> int:
        """获取 handoff 次数"""
        return len(self.handoff_history)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class HandoffManager:
    """Handoff 管理器 - 管理 Agent 之间的切换"""
    
    def __init__(self):
        """初始化 Handoff 管理器"""
        self._states: Dict[str, HandoffState] = {}
        logger.info("Handoff 管理器初始化完成")
    
    def create_state(
        self,
        conversation_id: str,
        initial_agent_id: str
    ) -> HandoffState:
        """创建新的 handoff 状态
        
        Args:
            conversation_id: 会话 ID
            initial_agent_id: 初始 Agent ID
            
        Returns:
            HandoffState
        """
        state = HandoffState(
            conversation_id=conversation_id,
            current_agent_id=initial_agent_id
        )
        self._states[conversation_id] = state
        
        logger.info(
            "创建 handoff 状态",
            extra={
                "conversation_id": conversation_id,
                "initial_agent": initial_agent_id
            }
        )
        
        return state
    
    def get_state(self, conversation_id: str) -> Optional[HandoffState]:
        """获取 handoff 状态
        
        Args:
            conversation_id: 会话 ID
            
        Returns:
            HandoffState 或 None
        """
        return self._states.get(conversation_id)
    
    def execute_handoff(
        self,
        conversation_id: str,
        from_agent_id: str,
        to_agent_id: str,
        reason: str,
        user_message: Optional[str] = None,
        context_summary: Optional[str] = None
    ) -> HandoffState:
        """执行 Agent 切换
        
        Args:
            conversation_id: 会话 ID
            from_agent_id: 源 Agent ID
            to_agent_id: 目标 Agent ID
            reason: 切换原因
            user_message: 用户消息
            context_summary: 上下文摘要
            
        Returns:
            更新后的 HandoffState
        """
        state = self.get_state(conversation_id)
        if not state:
            # 如果状态不存在，创建新状态
            state = self.create_state(conversation_id, from_agent_id)
        
        # 创建 handoff 上下文
        context = HandoffContext(
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            reason=reason,
            user_message=user_message,
            context_summary=context_summary
        )
        
        # 添加到状态
        state.add_handoff(context)
        
        logger.info(
            "执行 Agent handoff",
            extra={
                "conversation_id": conversation_id,
                "from_agent": from_agent_id,
                "to_agent": to_agent_id,
                "handoff_count": state.get_handoff_count()
            }
        )
        
        return state
    
    def should_handoff(
        self,
        conversation_id: str,
        current_agent_id: str,
        message: str,
        available_agents: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """判断是否需要 handoff
        
        Args:
            conversation_id: 会话 ID
            current_agent_id: 当前 Agent ID
            message: 用户消息
            available_agents: 可用的 Agent 字典
            
        Returns:
            如果需要 handoff，返回目标 Agent 信息，否则返回 None
        """
        state = self.get_state(conversation_id)
        
        # 简单的关键词匹配策略
        message_lower = message.lower()
        
        for agent_id, agent_info in available_agents.items():
            if agent_id == current_agent_id:
                continue
            
            # 检查 Agent 的能力关键词
            capabilities = agent_info.get("info", {}).get("capabilities", [])
            role = agent_info.get("info", {}).get("role", "")
            
            # 关键词匹配
            keywords = capabilities + ([role] if role else [])
            for keyword in keywords:
                if keyword.lower() in message_lower:
                    return {
                        "target_agent_id": agent_id,
                        "target_agent_name": agent_info.get("info", {}).get("name", ""),
                        "reason": f"检测到关键词: {keyword}",
                        "confidence": 0.8
                    }
        
        # 检查是否频繁切换到同一个 Agent（可能需要固定使用）
        if state:
            recent_handoffs = state.get_recent_handoffs(3)
            if len(recent_handoffs) >= 2:
                # 检查是否有重复的目标 Agent
                target_agents = [h.to_agent_id for h in recent_handoffs]
                from collections import Counter
                most_common = Counter(target_agents).most_common(1)
                if most_common and most_common[0][1] >= 2:
                    # 频繁切换到同一个 Agent，建议继续使用
                    return None
        
        return None
    
    def generate_handoff_tools(
        self,
        current_agent_id: str,
        available_agents: Dict[str, Any]
    ) -> List[AgentHandoffTool]:
        """为当前 Agent 生成可用的 handoff 工具
        
        Args:
            current_agent_id: 当前 Agent ID
            available_agents: 可用的 Agent 字典
            
        Returns:
            AgentHandoffTool 列表
        """
        tools = []
        
        for agent_id, agent_data in available_agents.items():
            if agent_id == current_agent_id:
                continue
            
            agent_info = agent_data.get("info", {})
            name = agent_info.get("name", "未命名")
            role = agent_info.get("role", "")
            capabilities = agent_info.get("capabilities", [])
            
            # 生成工具名称
            tool_name = f"transfer_to_{agent_id.replace('-', '_')}"
            
            # 生成工具描述
            description = f"切换到 {name}"
            if role:
                description += f"（{role}）"
            if capabilities:
                description += f"。擅长: {', '.join(capabilities[:3])}"
            description += "。当用户的问题更适合该 Agent 处理时使用此工具。"
            
            tool = AgentHandoffTool(
                name=tool_name,
                target_agent_id=agent_id,
                target_agent_name=name,
                description=description,
                trigger_keywords=capabilities + ([role] if role else [])
            )
            
            tools.append(tool)
        
        logger.info(
            "生成 handoff 工具",
            extra={
                "current_agent": current_agent_id,
                "tool_count": len(tools)
            }
        )
        
        return tools
    
    def get_handoff_context_for_agent(
        self,
        conversation_id: str,
        agent_id: str
    ) -> Optional[str]:
        """获取传递给目标 Agent 的上下文信息
        
        Args:
            conversation_id: 会话 ID
            agent_id: 目标 Agent ID
            
        Returns:
            上下文字符串
        """
        state = self.get_state(conversation_id)
        if not state:
            return None
        
        recent_handoffs = state.get_recent_handoffs(3)
        if not recent_handoffs:
            return None
        
        # 构建上下文摘要
        context_parts = []
        for handoff in recent_handoffs:
            if handoff.to_agent_id == agent_id:
                context_parts.append(
                    f"从 {handoff.from_agent_id} 切换而来，原因: {handoff.reason}"
                )
                if handoff.context_summary:
                    context_parts.append(f"上下文: {handoff.context_summary}")
        
        if context_parts:
            return "\n".join(context_parts)
        
        return None
    
    def clear_state(self, conversation_id: str):
        """清除会话状态
        
        Args:
            conversation_id: 会话 ID
        """
        if conversation_id in self._states:
            del self._states[conversation_id]
            logger.info(f"清除 handoff 状态: {conversation_id}")


# 全局单例
_handoff_manager = None


def get_handoff_manager() -> HandoffManager:
    """获取全局 Handoff 管理器单例"""
    global _handoff_manager
    if _handoff_manager is None:
        _handoff_manager = HandoffManager()
    return _handoff_manager
