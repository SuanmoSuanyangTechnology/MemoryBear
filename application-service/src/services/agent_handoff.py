"""Agent handoff mechanism — dynamic switching and collaboration between agents.

Mirrors the monolith's ``agent_handoff`` module (import paths and the logger
helper adapted to this service; pure in-memory logic otherwise unchanged).
"""
import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..utils.datetime_utils import to_iso_z, utcnow_naive

logger = logging.getLogger(__name__)


class HandoffContext(BaseModel):
    """Handoff context information."""
    from_agent_id: str = Field(..., description="源 Agent ID")
    to_agent_id: str = Field(..., description="目标 Agent ID")
    reason: str = Field(..., description="切换原因")
    timestamp: datetime = Field(default_factory=utcnow_naive)
    user_message: str | None = Field(None, description="触发切换的用户消息")
    context_summary: str | None = Field(None, description="上下文摘要")

    class Config:
        json_encoders = {
            datetime: lambda v: to_iso_z(v)
        }


class AgentHandoffTool(BaseModel):
    """Agent handoff tool definition."""
    name: str = Field(..., description="工具名称，如 transfer_to_math_agent")
    target_agent_id: str = Field(..., description="目标 Agent ID")
    target_agent_name: str = Field(..., description="目标 Agent 名称")
    description: str = Field(..., description="工具描述")
    trigger_keywords: list[str] = Field(default_factory=list, description="触发关键词")

    def to_tool_schema(self) -> dict[str, Any]:
        """Convert to an LLM tool schema."""
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
    """Handoff state management."""
    conversation_id: str = Field(..., description="会话 ID")
    current_agent_id: str = Field(..., description="当前活跃的 Agent ID")
    handoff_history: list[HandoffContext] = Field(default_factory=list, description="切换历史")
    context_data: dict[str, Any] = Field(default_factory=dict, description="共享上下文数据")
    created_at: datetime = Field(default_factory=utcnow_naive)
    updated_at: datetime = Field(default_factory=utcnow_naive)

    def add_handoff(self, context: HandoffContext):
        """Append a handoff record."""
        self.handoff_history.append(context)
        self.current_agent_id = context.to_agent_id
        self.updated_at = utcnow_naive()

        logger.info(
            "Agent handoff 记录",
            extra={
                "conversation_id": self.conversation_id,
                "from_agent": context.from_agent_id,
                "to_agent": context.to_agent_id,
                "reason": context.reason
            }
        )

    def get_recent_handoffs(self, limit: int = 5) -> list[HandoffContext]:
        """Get the most recent handoff records."""
        return self.handoff_history[-limit:] if self.handoff_history else []

    def get_handoff_count(self) -> int:
        """Get the number of handoffs."""
        return len(self.handoff_history)

    class Config:
        json_encoders = {
            datetime: lambda v: to_iso_z(v)
        }


class HandoffManager:
    """Handoff manager — manages switching between agents."""

    def __init__(self):
        """Initialize the handoff manager."""
        self._states: dict[str, HandoffState] = {}
        logger.info("Handoff 管理器初始化完成")

    def create_state(
        self,
        conversation_id: str,
        initial_agent_id: str
    ) -> HandoffState:
        """Create a new handoff state.

        Args:
            conversation_id: Conversation ID
            initial_agent_id: Initial agent ID

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

    def get_state(self, conversation_id: str) -> HandoffState | None:
        """Get the handoff state.

        Args:
            conversation_id: Conversation ID

        Returns:
            HandoffState or None
        """
        return self._states.get(conversation_id)

    def execute_handoff(
        self,
        conversation_id: str,
        from_agent_id: str,
        to_agent_id: str,
        reason: str,
        user_message: str | None = None,
        context_summary: str | None = None
    ) -> HandoffState:
        """Execute an agent switch.

        Args:
            conversation_id: Conversation ID
            from_agent_id: Source agent ID
            to_agent_id: Target agent ID
            reason: Reason for the switch
            user_message: User message
            context_summary: Context summary

        Returns:
            The updated HandoffState
        """
        state = self.get_state(conversation_id)
        if not state:
            # Create a new state if none exists
            state = self.create_state(conversation_id, from_agent_id)

        # Build the handoff context
        context = HandoffContext(
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            reason=reason,
            user_message=user_message,
            context_summary=context_summary
        )

        # Append to the state
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
        available_agents: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Decide whether a handoff is needed.

        Args:
            conversation_id: Conversation ID
            current_agent_id: Current agent ID
            message: User message
            available_agents: Dict of available agents

        Returns:
            Target agent info if a handoff is needed, otherwise None
        """
        state = self.get_state(conversation_id)

        # Simple keyword-matching strategy
        message_lower = message.lower()

        for agent_id, agent_info in available_agents.items():
            if agent_id == current_agent_id:
                continue

            # Check the agent's capability keywords
            capabilities = agent_info.get("info", {}).get("capabilities", [])
            role = agent_info.get("info", {}).get("role", "")

            # Keyword match
            keywords = capabilities + ([role] if role else [])
            for keyword in keywords:
                if keyword.lower() in message_lower:
                    return {
                        "target_agent_id": agent_id,
                        "target_agent_name": agent_info.get("info", {}).get("name", ""),
                        "reason": f"检测到关键词: {keyword}",
                        "confidence": 0.8
                    }

        # Check for frequent switches to the same agent (may warrant sticking with it)
        if state:
            recent_handoffs = state.get_recent_handoffs(3)
            if len(recent_handoffs) >= 2:
                # Check for a repeated target agent
                target_agents = [h.to_agent_id for h in recent_handoffs]
                from collections import Counter
                most_common = Counter(target_agents).most_common(1)
                if most_common and most_common[0][1] >= 2:
                    # Frequent switches to the same agent; keep using it
                    return None

        return None

    def generate_handoff_tools(
        self,
        current_agent_id: str,
        available_agents: dict[str, Any]
    ) -> list[AgentHandoffTool]:
        """Generate the handoff tools available to the current agent.

        Args:
            current_agent_id: Current agent ID
            available_agents: Dict of available agents

        Returns:
            List of AgentHandoffTool
        """
        tools = []

        for agent_id, agent_data in available_agents.items():
            if agent_id == current_agent_id:
                continue

            agent_info = agent_data.get("info", {})
            name = agent_info.get("name", "未命名")
            role = agent_info.get("role", "")
            capabilities = agent_info.get("capabilities", [])

            # Build the tool name
            tool_name = f"transfer_to_{agent_id.replace('-', '_')}"

            # Build the tool description
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
    ) -> str | None:
        """Get the context passed to the target agent.

        Args:
            conversation_id: Conversation ID
            agent_id: Target agent ID

        Returns:
            Context string
        """
        state = self.get_state(conversation_id)
        if not state:
            return None

        recent_handoffs = state.get_recent_handoffs(3)
        if not recent_handoffs:
            return None

        # Build the context summary
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
        """Clear the conversation state.

        Args:
            conversation_id: Conversation ID
        """
        if conversation_id in self._states:
            del self._states[conversation_id]
            logger.info(f"清除 handoff 状态: {conversation_id}")


# Global singleton
_handoff_manager = None


def get_handoff_manager() -> HandoffManager:
    """Get the global handoff manager singleton."""
    global _handoff_manager
    if _handoff_manager is None:
        _handoff_manager = HandoffManager()
    return _handoff_manager
