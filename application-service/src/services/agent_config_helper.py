"""
Agent config helper functions.
Enrich AgentConfig objects with parsed fields.
"""
from ..models import AgentConfig
from .agent_config_converter import AgentConfigConverter


def enrich_agent_config(agent_cfg: AgentConfig) -> AgentConfig:
    """
    Enrich an AgentConfig object with parsed config fields.

    Args:
        agent_cfg: AgentConfig ORM object

    Returns:
        The enriched AgentConfig object (with parsed fields attached)
    """
    if not agent_cfg:
        return agent_cfg

    # Parse the database storage format
    parsed = AgentConfigConverter.from_storage_format(
        model_parameters=agent_cfg.model_parameters,
        knowledge_retrieval=agent_cfg.knowledge_retrieval,
        memory=agent_cfg.memory,
        variables=agent_cfg.variables,
        tools=agent_cfg.tools,
        skills=agent_cfg.skills,
        features=agent_cfg.features,
    )

    # Attach the parsed fields to the object (for serialization)
    agent_cfg.model_parameters = parsed["model_parameters"]
    agent_cfg.knowledge_retrieval = parsed["knowledge_retrieval"]
    agent_cfg.memory = parsed["memory"]
    agent_cfg.variables = parsed["variables"]
    agent_cfg.tools = parsed["tools"]
    agent_cfg.skills = parsed["skills"]
    agent_cfg.features = parsed["features"]

    return agent_cfg


__all__ = ["enrich_agent_config"]
