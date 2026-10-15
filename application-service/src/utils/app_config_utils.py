"""App config conversion helpers; mirrors the host ``app_config_utils``
subset used by the release snapshot builder."""

from __future__ import annotations

import json
from typing import Any

from ..models.agent_app_config_model import AgentConfig


def model_parameters_to_dict(model_parameters: Any) -> dict[str, Any] | None:
    """Convert a ModelParameters object to a dictionary.

    Args:
        model_parameters: ModelParameters object, dict, or None

    Returns:
        Model parameters as a dictionary; None if the input is None
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

    # Fall back to a generic conversion for other cases
    try:
        return dict(model_parameters)
    except (TypeError, ValueError):
        return None


def agent_config_4_app_release(release: Any) -> AgentConfig:
    """Build a transient AgentConfig view over a release snapshot config.

    Args:
        release: AppRelease snapshot holding a dict/JSON-string config

    Returns:
        AgentConfig: Unpersisted view carrying the snapshot's agent fields
    """
    config_dict = release.config or {}
    if isinstance(config_dict, str):
        try:
            config_dict = json.loads(config_dict)
        except json.JSONDecodeError:
            config_dict = {}
    if not isinstance(config_dict, dict):
        config_dict = {}

    return AgentConfig(
        app_id=release.app_id,
        system_prompt=config_dict.get("system_prompt"),
        default_model_config_id=release.default_model_config_id,
        model_parameters=config_dict.get("model_parameters"),
        knowledge_retrieval=config_dict.get("knowledge_retrieval"),
        memory=config_dict.get("memory"),
        variables=config_dict.get("variables", []),
        tools=config_dict.get("tools", []),
        skills=config_dict.get("skills", {}),
        features=config_dict.get("features", {}),
    )


__all__ = ["agent_config_4_app_release", "model_parameters_to_dict"]
