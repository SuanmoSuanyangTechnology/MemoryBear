"""
App Config Utilities

Utility functions for converting between dict and model objects for different app configurations.
"""

import uuid
from typing import Dict, Any, Optional, Union
from datetime import datetime

from app.db import get_db_read
from app.models import AppRelease, WorkflowConfig
from app.models.agent_app_config_model import AgentConfig
from app.models.multi_agent_model import MultiAgentConfig
from app.repositories.workflow_repository import WorkflowConfigRepository


def model_parameters_to_dict(model_parameters: Any) -> Optional[Dict[str, Any]]:
    """将 ModelParameters 对象转换为字典
    
    Args:
        model_parameters: ModelParameters 对象、字典或 None
        
    Returns:
        字典格式的模型参数，如果输入为 None 则返回 None
    """
    if model_parameters is None:
        return None

    if isinstance(model_parameters, dict):
        return model_parameters

    # Pydantic v2
    if hasattr(model_parameters, 'model_dump'):
        return model_parameters.model_dump()

    # Pydantic v1
    if hasattr(model_parameters, 'dict'):
        return model_parameters.dict()

    # 其他情况尝试转换
    try:
        return dict(model_parameters)
    except (TypeError, ValueError):
        return None


def dict_to_model_parameters(data: Optional[Dict[str, Any]]) -> Optional[Any]:
    """将字典转换为 ModelParameters 对象
    
    Args:
        data: 字典格式的模型参数或 None
        
    Returns:
        ModelParameters 对象，如果输入为 None 则返回 None
    """
    if data is None:
        return None

    from app.schemas import ModelParameters

    if isinstance(data, ModelParameters):
        return data

    if isinstance(data, dict):
        return ModelParameters(**data)

    return None


class AgentConfigProxy:
    """Proxy class for AgentConfig (legacy compatibility)"""

    def __init__(self, release, app, config_data):
        self.id = release.id
        self.app_id = release.app_id
        self.app = app
        self.name = release.name
        self.description = release.description
        self.system_prompt = config_data.get("system_prompt")
        self.default_model_config_id = release.default_model_config_id


def agent_config_4_app_release(release: AppRelease) -> AgentConfig:
    config_dict = release.config

    agent_config = AgentConfig(
        app_id=release.app_id,
        system_prompt=config_dict.get("system_prompt"),
        default_model_config_id=release.default_model_config_id,
        model_parameters=config_dict.get("model_parameters"),
        knowledge_retrieval=config_dict.get("knowledge_retrieval"),
        memory=config_dict.get("memory"),
        variables=config_dict.get("variables", []),
        tools=config_dict.get("tools", {}),
    )

    return agent_config


def multi_agent_config_4_app_release(release: AppRelease) -> MultiAgentConfig:
    config_dict = release.config

    agent_config = MultiAgentConfig(
        app_id=release.app_id,
        default_model_config_id=release.default_model_config_id,
        model_parameters=config_dict.get("model_parameters"),
        master_agent_id=config_dict.get("master_agent_id"),
        master_agent_name=config_dict.get("master_agent_name"),
        orchestration_mode=config_dict.get("orchestration_mode", "supervisor"),
        sub_agents=config_dict.get("sub_agents", []),
        routing_rules=config_dict.get("routing_rules"),
        execution_config=config_dict.get("execution_config", {}),
        aggregation_strategy=config_dict.get("aggregation_strategy", "merge"),

    )

    return agent_config


def workflow_config_4_app_release(release: AppRelease) -> WorkflowConfig:
    config_dict = release.config
    with get_db_read() as db:
        source_config = WorkflowConfigRepository(db).get_by_app_id(release.app_id)
        source_config_id = source_config.id

    config = WorkflowConfig(
        id=source_config_id,
        app_id=release.app_id,
        nodes=config_dict.get("nodes", []),
        edges=config_dict.get("edges", []),
        variables=config_dict.get("variables", []),
        execution_config=config_dict.get("execution_config", {}),
        triggers=config_dict.get("triggers", [])
    )

    return config


def dict_to_multi_agent_config(config_dict: Dict[str, Any], app_id: Optional[uuid.UUID] = None):
    """Convert dict to MultiAgentConfig model object

    Args:
        config_dict: Configuration dictionary
        app_id: Optional app ID (if not provided in dict)

    Returns:
        MultiAgentConfig model instance (not yet persisted to database)

    Example:
        >>> config_dict = {
        ...     "app_id": "uuid-here",
        ...     "master_agent_id": "master-uuid",
        ...     "master_agent_name": "Master Agent",
        ...     "orchestration_mode": "supervisor",
        ...     "sub_agents": [
        ...         {"agent_id": "sub1-uuid", "name": "Sub Agent 1", "role": "specialist", "priority": 1},
        ...         {"agent_id": "sub2-uuid", "name": "Sub Agent 2", "role": "specialist", "priority": 2}
        ...     ],
        ...     "routing_rules": [
        ...         {"condition": "intent == 'technical'", "target_agent_id": "sub1-uuid", "priority": 1}
        ...     ],
        ...     "execution_config": {"max_iterations": 5, "timeout": 60, "parallel_limit": 3},
        ...     "aggregation_strategy": "merge"
        ... }
        >>> multi_agent_config = dict_to_multi_agent_config(config_dict)
    """
    from app.models.multi_agent_model import MultiAgentConfig

    # Extract app_id
    final_app_id = config_dict.get("app_id") or app_id
    if not final_app_id:
        raise ValueError("app_id is required")

    # Convert string UUID to UUID object if needed
    if isinstance(final_app_id, str):
        final_app_id = uuid.UUID(final_app_id)

    # Convert master_agent_id
    master_agent_id = config_dict.get("master_agent_id")
    if not master_agent_id:
        raise ValueError("master_agent_id is required")
    if isinstance(master_agent_id, str):
        master_agent_id = uuid.UUID(master_agent_id)

    # Create MultiAgentConfig instance
    multi_agent_config = MultiAgentConfig(
        id=uuid.UUID(config_dict["id"]) if "id" in config_dict else uuid.uuid4(),
        app_id=final_app_id,
        master_agent_id=master_agent_id,
        master_agent_name=config_dict.get("master_agent_name"),
        orchestration_mode=config_dict.get("orchestration_mode", "supervisor"),
        sub_agents=config_dict.get("sub_agents", []),
        routing_rules=config_dict.get("routing_rules"),
        execution_config=config_dict.get("execution_config", {}),
        aggregation_strategy=config_dict.get("aggregation_strategy", "merge"),
        is_active=config_dict.get("is_active", True),
        created_at=config_dict.get("created_at", datetime.now()),
        updated_at=config_dict.get("updated_at", datetime.now())
    )

    return multi_agent_config


def dict_to_workflow_config(config_dict: Dict[str, Any], app_id: Optional[uuid.UUID] = None):
    """Convert dict to WorkflowConfig model object

    Args:
        config_dict: Configuration dictionary
        app_id: Optional app ID (if not provided in dict)

    Returns:
        WorkflowConfig model instance (not yet persisted to database)

    Example:
        >>> config_dict = {
        ...     "app_id": "uuid-here",
        ...     "nodes": [
        ...         {"id": "start", "type": "start", "config": {}},
        ...         {"id": "llm", "type": "llm", "config": {"model": "gpt-4"}},
        ...         {"id": "end", "type": "end", "config": {"output": "{{llm.output}}"}}
        ...     ],
        ...     "edges": [
        ...         {"source": "start", "target": "llm"},
        ...         {"source": "llm", "target": "end"}
        ...     ],
        ...     "variables": [
        ...         {"name": "user_input", "type": "string", "default": ""}
        ...     ],
        ...     "execution_config": {
        ...         "max_iterations": 10,
        ...         "timeout": 300,
        ...         "enable_streaming": True
        ...     },
        ...     "triggers": [
        ...         {"type": "manual", "enabled": True}
        ...     ]
        ... }
        >>> workflow_config = dict_to_workflow_config(config_dict)
    """
    from app.models.workflow_model import WorkflowConfig

    # Extract app_id
    final_app_id = config_dict.get("app_id") or app_id
    if not final_app_id:
        raise ValueError("app_id is required")

    # Convert string UUID to UUID object if needed
    if isinstance(final_app_id, str):
        final_app_id = uuid.UUID(final_app_id)

    # Create WorkflowConfig instance
    workflow_config = WorkflowConfig(
        id=uuid.UUID(config_dict["id"]) if "id" in config_dict else uuid.uuid4(),
        app_id=final_app_id,
        nodes=config_dict.get("nodes", []),
        edges=config_dict.get("edges", []),
        variables=config_dict.get("variables", []),
        execution_config=config_dict.get("execution_config", {}),
        triggers=config_dict.get("triggers", []),
        is_active=config_dict.get("is_active", True),
        created_at=config_dict.get("created_at", datetime.now()),
        updated_at=config_dict.get("updated_at", datetime.now())
    )

    return workflow_config


def agent_config_to_dict(agent_config) -> Dict[str, Any]:
    """Convert AgentConfig model to dict

    Args:
        agent_config: AgentConfig model instance

    Returns:
        Configuration dictionary
    """
    return {
        "id": str(agent_config.id),
        "app_id": str(agent_config.app_id),
        "system_prompt": agent_config.system_prompt,
        "default_model_config_id": str(
            agent_config.default_model_config_id) if agent_config.default_model_config_id else None,
        "model_parameters": agent_config.model_parameters,
        "knowledge_retrieval": agent_config.knowledge_retrieval,
        "memory": agent_config.memory,
        "variables": agent_config.variables,
        "tools": agent_config.tools,
        "agent_role": agent_config.agent_role,
        "agent_domain": agent_config.agent_domain,
        "parent_agent_id": str(agent_config.parent_agent_id) if agent_config.parent_agent_id else None,
        "capabilities": agent_config.capabilities,
        "is_active": agent_config.is_active,
        "created_at": agent_config.created_at.isoformat() if agent_config.created_at else None,
        "updated_at": agent_config.updated_at.isoformat() if agent_config.updated_at else None
    }


def multi_agent_config_to_dict(multi_agent_config) -> Dict[str, Any]:
    """Convert MultiAgentConfig model to dict

    Args:
        multi_agent_config: MultiAgentConfig model instance

    Returns:
        Configuration dictionary
    """
    return {
        "id": str(multi_agent_config.id),
        "app_id": str(multi_agent_config.app_id),
        "master_agent_id": str(multi_agent_config.master_agent_id),
        "master_agent_name": multi_agent_config.master_agent_name,
        "orchestration_mode": multi_agent_config.orchestration_mode,
        "sub_agents": multi_agent_config.sub_agents,
        "routing_rules": multi_agent_config.routing_rules,
        "execution_config": multi_agent_config.execution_config,
        "aggregation_strategy": multi_agent_config.aggregation_strategy,
        "is_active": multi_agent_config.is_active,
        "created_at": multi_agent_config.created_at.isoformat() if multi_agent_config.created_at else None,
        "updated_at": multi_agent_config.updated_at.isoformat() if multi_agent_config.updated_at else None
    }


def workflow_config_to_dict(workflow_config) -> Dict[str, Any]:
    """Convert WorkflowConfig model to dict

    Args:
        workflow_config: WorkflowConfig model instance

    Returns:
        Configuration dictionary
    """
    return {
        "id": str(workflow_config.id),
        "app_id": str(workflow_config.app_id),
        "nodes": workflow_config.nodes,
        "edges": workflow_config.edges,
        "variables": workflow_config.variables,
        "execution_config": workflow_config.execution_config,
        "triggers": workflow_config.triggers,
        "is_active": workflow_config.is_active,
        "created_at": workflow_config.created_at.isoformat() if workflow_config.created_at else None,
        "updated_at": workflow_config.updated_at.isoformat() if workflow_config.updated_at else None
    }
