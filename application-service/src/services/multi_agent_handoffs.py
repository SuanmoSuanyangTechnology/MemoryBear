"""Handoffs integration for the Multi-Agent service.

Covers only the ``test_handoff_routing`` decision path (debug endpoint) of the
monolith's ``MultiAgentHandoffsService``; the run / stream / history methods
remain in the monolith.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from ..errors import BizCode, BusinessException, ResourceNotFoundException
from .agent_handoff import get_handoff_manager
from .multi_agent_service import MultiAgentService

logger = logging.getLogger(__name__)


class MultiAgentHandoffsService:
    """Multi-agent handoffs service — extends the existing Multi-Agent service."""

    def __init__(self, db: Session, multi_agent_service: MultiAgentService):
        """Initialize the service.

        Args:
            db: Database session
            multi_agent_service: Existing MultiAgentService instance
        """
        self.db = db
        self.multi_agent_service = multi_agent_service
        self.handoff_manager = get_handoff_manager()

        logger.info("Multi-Agent Handoffs 服务初始化完成")

    def test_handoff_routing(
        self,
        app_id: uuid.UUID,
        message: str
    ) -> dict[str, Any]:
        """Test the handoff routing decision (without executing it).

        Args:
            app_id: App ID
            message: Test message

        Returns:
            Routing decision result
        """
        # 1. Fetch the config
        config = self.multi_agent_service.get_config(app_id)
        if not config:
            raise ResourceNotFoundException("多 Agent 配置", str(app_id))

        # 2. Parse the sub-agents
        sub_agents = {}
        for agent_data in config.sub_agents:
            agent_id = agent_data.get("agent_id")
            if agent_id:
                sub_agents[str(agent_id)] = {
                    "info": agent_data
                }

        if not sub_agents:
            raise BusinessException(
                "多 Agent 配置无子 Agent，无法进行路由测试",
                BizCode.AGENT_CONFIG_MISSING
            )

        # 3. Test the routing
        test_conversation_id = f"test-{uuid.uuid4()}"

        # Pick the initial agent
        initial_agent_id = None
        message_lower = message.lower()

        for agent_id, agent_data in sub_agents.items():
            agent_info = agent_data.get("info", {})
            capabilities = agent_info.get("capabilities", [])
            role = agent_info.get("role", "")

            keywords = capabilities + ([role] if role else [])
            for keyword in keywords:
                if keyword.lower() in message_lower:
                    initial_agent_id = agent_id
                    break

            if initial_agent_id:
                break

        if not initial_agent_id:
            initial_agent_id = next(iter(sub_agents.keys()))

        # 4. Generate the handoff tools
        handoff_tools = self.handoff_manager.generate_handoff_tools(
            initial_agent_id,
            sub_agents
        )

        # 5. Check whether a handoff is needed
        handoff_suggestion = self.handoff_manager.should_handoff(
            conversation_id=test_conversation_id,
            current_agent_id=initial_agent_id,
            message=message,
            available_agents=sub_agents
        )

        return {
            "message": message,
            "initial_agent_id": initial_agent_id,
            "initial_agent_name": sub_agents[initial_agent_id]["info"].get("name", ""),
            "available_handoff_tools": [
                {
                    "name": tool.name,
                    "target_agent_id": tool.target_agent_id,
                    "target_agent_name": tool.target_agent_name,
                    "description": tool.description
                }
                for tool in handoff_tools
            ],
            "handoff_suggestion": handoff_suggestion,
            "total_agents": len(sub_agents)
        }
