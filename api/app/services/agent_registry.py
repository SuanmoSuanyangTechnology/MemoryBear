"""Agent 注册表服务"""
import uuid
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import select, or_, and_

from app.models import AgentConfig, App
from app.core.logging_config import get_business_logger

logger = get_business_logger()


class AgentRegistry:
    """Agent 注册表 - 管理所有可用的 Agent"""
    
    def __init__(self, db: Session):
        self.db = db
        self._cache: Dict[str, Dict[str, Any]] = {}
    
    def register_agent(self, agent: AgentConfig) -> None:
        """注册 Agent 到系统
        
        Args:
            agent: Agent 配置对象
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
        """发现可用的 Agent
        
        Args:
            query: 搜索关键词
            domain: 专业领域
            capabilities: 所需能力列表
            workspace_id: 工作空间ID（权限过滤）
            
        Returns:
            匹配的 Agent 列表
        """
        # 构建查询
        stmt = select(AgentConfig).join(App).where(
            AgentConfig.is_active == True,
            App.is_active == True
        )
        
        # 工作空间过滤（同工作空间或公开）
        if workspace_id:
            stmt = stmt.where(
                or_(
                    App.workspace_id == workspace_id,
                    App.visibility == "public"
                )
            )
        
        # 领域过滤
        if domain:
            stmt = stmt.where(AgentConfig.agent_domain == domain)
        
        # 能力过滤
        if capabilities:
            # PostgreSQL JSON 数组包含查询
            for cap in capabilities:
                stmt = stmt.where(
                    AgentConfig.capabilities.contains([cap])
                )
        
        # 关键词搜索
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
        """获取 Agent 信息
        
        Args:
            agent_id: Agent ID
            
        Returns:
            Agent 信息字典，如果不存在返回 None
        """
        agent_id_str = str(agent_id)
        
        # 先查缓存
        if agent_id_str in self._cache:
            return self._cache[agent_id_str]
        
        # 查数据库
        agent = self.db.get(AgentConfig, agent_id)
        if agent and agent.is_active:
            agent_info = self._to_agent_info(agent)
            self._cache[agent_id_str] = agent_info
            return agent_info
        
        return None
    
    def _to_agent_info(self, agent: AgentConfig) -> Dict[str, Any]:
        """转换为 Agent 信息字典
        
        Args:
            agent: Agent 配置对象
            
        Returns:
            Agent 信息字典
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
        """提取知识库 ID 列表
        
        Args:
            agent: Agent 配置对象
            
        Returns:
            知识库 ID 列表
        """
        if not agent.knowledge_retrieval:
            return []
        
        kb_config = agent.knowledge_retrieval
        knowledge_bases = kb_config.get("knowledge_bases", [])
        return [kb.get("kb_id") for kb in knowledge_bases if kb.get("kb_id")]
    
    def _truncate_prompt(self, prompt: Optional[str], max_length: int = 200) -> Optional[str]:
        """截断提示词
        
        Args:
            prompt: 提示词
            max_length: 最大长度
            
        Returns:
            截断后的提示词
        """
        if not prompt:
            return None
        
        if len(prompt) <= max_length:
            return prompt
        
        return prompt[:max_length] + "..."
    
    def clear_cache(self) -> None:
        """清空缓存"""
        self._cache.clear()
        logger.debug("Agent 注册表缓存已清空")
