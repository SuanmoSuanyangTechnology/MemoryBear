"""
Agent 配置辅助函数
用于增强 AgentConfig 对象，添加解析后的字段
"""
from app.models import AgentConfig
from app.services.agent_config_converter import AgentConfigConverter


def enrich_agent_config(agent_cfg: AgentConfig) -> AgentConfig:
    """
    增强 AgentConfig 对象，添加解析后的配置字段
    
    Args:
        agent_cfg: AgentConfig ORM 对象
        
    Returns:
        增强后的 AgentConfig 对象（添加了解析字段）
    """
    if not agent_cfg:
        return agent_cfg
    
    # 解析数据库存储格式
    parsed = AgentConfigConverter.from_storage_format(
        model_parameters=agent_cfg.model_parameters,
        knowledge_retrieval=agent_cfg.knowledge_retrieval,
        memory=agent_cfg.memory,
        variables=agent_cfg.variables,
        tools=agent_cfg.tools,
    )
    
    # 将解析后的字段添加到对象上（用于序列化）
    agent_cfg.model_parameters = parsed["model_parameters"]
    agent_cfg.knowledge_retrieval = parsed["knowledge_retrieval"]
    agent_cfg.memory = parsed["memory"]
    agent_cfg.variables = parsed["variables"]
    agent_cfg.tools = parsed["tools"]
    
    return agent_cfg
