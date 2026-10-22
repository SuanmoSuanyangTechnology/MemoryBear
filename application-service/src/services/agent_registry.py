"""Agent registry service."""
import logging

import uuid
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import select, or_, and_

from ..models import AgentConfig, App

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Agent registry - manages all available Agents."""
    
    def __init__(self, db: Session):
        self.db = db
        self._cache: Dict[str, Dict[str, Any]] = {}
    
    def register_agent(self, agent: AgentConfig) -> None:
        """Register an Agent in the system.
        
        Args:
            agent: Agent config object
        """
        agent_info = self._to_agent_info(agent)
        self._cache[str(agent.id)] = agent_info
        
        logger.info(
            "Agent 注册成功",
            extra={
                "agent_id": str(agent.id),
                "name": agent.app.name,
                "domain": agent.agent_domain
            }
        )
    
    def discover_agents(
        self,
        query: Optional[str] = None,
        domain: Optional[str] = None,
        capabilities: Optional[List[str]] = None,
        workspace_id: Optional[uuid.UUID] = None
    ) -> List[Dict[str, Any]]:
        """Discover available Agents.
        
        Args:
            query: search keyword
            domain: domain name
            capabilities: list of required capabilities
            workspace_id: workspace ID (permission filter)
            
        Returns:
            Matching Agent list
        """
        # Build the query
        stmt = select(AgentConfig).join(App).where(
            AgentConfig.is_active.is_(True),
            App.is_active.is_(True)
        )
        
        # Workspace filter (same workspace or public)
        if workspace_id:
            stmt = stmt.where(
                or_(
                    App.workspace_id == workspace_id,
                    App.visibility == "public"
                )
            )
        
        # Domain filter
        if domain:
            stmt = stmt.where(AgentConfig.agent_domain == domain)
        
        # Capability filter
        if capabilities:
            # PostgreSQL JSON array containment query
            for cap in capabilities:
                stmt = stmt.where(
                    AgentConfig.capabilities.contains([cap])
                )
        
        # Keyword search
        if query:
            stmt = stmt.where(
                or_(
                    App.name.ilike(f"%{query}%"),
                    App.description.ilike(f"%{query}%")
                )
            )
        
        agents = self.db.scalars(stmt).all()
        
        logger.debug(
            "Agent 发现",
            extra={
                "query": query,
                "domain": domain,
                "capabilities": capabilities,
                "found_count": len(agents)
            }
        )
        
        return [self._to_agent_info(agent) for agent in agents]
    
    def get_agent(self, agent_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        """Get Agent info.
        
        Args:
            agent_id: Agent ID
            
        Returns:
            Agent info dict, or None if it does not exist
        """
        agent_id_str = str(agent_id)
        
        # Check the cache first
        if agent_id_str in self._cache:
            return self._cache[agent_id_str]
        
        # Query the database
        agent = self.db.get(AgentConfig, agent_id)
        if agent and agent.is_active:
            agent_info = self._to_agent_info(agent)
            self._cache[agent_id_str] = agent_info
            return agent_info
        
        return None
    
    def _to_agent_info(self, agent: AgentConfig) -> Dict[str, Any]:
        """Convert to an Agent info dict.
        
        Args:
            agent: Agent config object
            
        Returns:
            Agent info dict
        """
        return {
            "id": str(agent.id),
            "name": agent.app.name,
            "description": agent.app.description,
            "domain": agent.agent_domain,
            "role": agent.agent_role,
            "capabilities": agent.capabilities or [],
            "tools": list(agent.tools.keys()) if agent.tools else [],
            "knowledge_bases": self._extract_kb_ids(agent),
            "system_prompt": self._truncate_prompt(agent.system_prompt),
            "status": "active" if agent.is_active else "inactive",
            "workspace_id": str(agent.app.workspace_id),
            "visibility": agent.app.visibility
        }
    
    def _extract_kb_ids(self, agent: AgentConfig) -> List[str]:
        """Extract the knowledge-base ID list.
        
        Args:
            agent: Agent config object
            
        Returns:
            Knowledge-base ID list
        """
        if not agent.knowledge_retrieval:
            return []
        
        kb_config = agent.knowledge_retrieval
        knowledge_bases = kb_config.get("knowledge_bases", [])
        return [kb.get("kb_id") for kb in knowledge_bases if kb.get("kb_id")]
    
    def _truncate_prompt(self, prompt: Optional[str], max_length: int = 200) -> Optional[str]:
        """Truncate the prompt.
        
        Args:
            prompt: prompt text
            max_length: maximum length
            
        Returns:
            Truncated prompt
        """
        if not prompt:
            return None
        
        if len(prompt) <= max_length:
            return prompt
        
        return prompt[:max_length] + "..."
    
    def clear_cache(self) -> None:
        """Clear the cache."""
        self._cache.clear()
        logger.debug("Agent 注册表缓存已清空")
