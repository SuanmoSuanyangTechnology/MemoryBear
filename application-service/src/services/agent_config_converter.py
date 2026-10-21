"""
Agent config format converter.
Converts Pydantic models to and from the database storage format.

Mirrors the monolith's ``agent_config_converter`` (storage-format round trip
must stay byte-compatible).
"""
from typing import Any

from ..schemas.app_schema import (
    AgentConfigCreate,
    AppFeatures,
    KnowledgeRetrievalConfig,
    MemoryConfig,
    SkillConfig,
    ToolConfig,
    ToolOldConfig,
    VariableDefinition,
)


class AgentConfigConverter:
    """Agent config format converter."""

    @staticmethod
    def to_storage_format(config: AgentConfigCreate) -> dict[str, Any]:
        """
        Convert a config object to the database storage format.

        Args:
            config: AgentConfigCreate object

        Returns:
            Dict containing the database fields
        """
        result = {}

        # 1. Model parameters
        if hasattr(config, 'model_parameters') and config.model_parameters:
            result["model_parameters"] = config.model_parameters.model_dump()

        # 2. Knowledge-base retrieval config
        if config.knowledge_retrieval:
            result["knowledge_retrieval"] = config.knowledge_retrieval.model_dump()

        # 3. Memory config
        if hasattr(config, 'memory') and config.memory:
            result["memory"] = config.memory.model_dump()

        # 4. Variable config
        if hasattr(config, 'variables') and config.variables:
            result["variables"] = [var.model_dump() for var in config.variables]

        # 5. Tool config
        if hasattr(config, 'tools') and config.tools:
            result["tools"] = [tool.model_dump() for tool in config.tools]

        if hasattr(config, "skills") and config.skills:
            result["skills"] = (
                config.skills.model_dump()
                if hasattr(config.skills, "model_dump")
                else config.skills
            )

        if hasattr(config, "features") and config.features:
            result["features"] = (
                config.features.model_dump()
                if hasattr(config.features, "model_dump")
                else config.features
            )

        return result

    @staticmethod
    def from_storage_format(
        model_parameters: dict[str, Any] | None,
        knowledge_retrieval: dict[str, Any] | None,
        memory: dict[str, Any] | None,
        variables: list | None,
        tools: list | dict[str, Any] | None,
        skills: dict | None,
        features: dict | None = None,
    ) -> dict[str, Any]:
        """
        Convert the database storage format to Pydantic objects.

        Args:
            model_parameters: Model parameters
            knowledge_retrieval: Knowledge-base retrieval config
            memory: Memory config
            variables: Variable config
            tools: Tool config
            skills: Skill list
            features: Feature config

        Returns:
            Dict containing Pydantic objects
        """
        result = {
            "model_parameters": None,
            "knowledge_retrieval": None,
            "memory": MemoryConfig(enabled=True),
            "variables": [],
            "tools": [],
            "skills": SkillConfig(enabled=False, all_skills=False, skill_ids=[]),
            "features": AppFeatures(),
        }

        # 1. Parse model parameters
        if model_parameters:
            from ..schemas.app_schema import ModelParameters
            if isinstance(model_parameters, ModelParameters):
                result["model_parameters"] = model_parameters
            elif isinstance(model_parameters, dict):
                result["model_parameters"] = ModelParameters(**model_parameters)
            else:
                result["model_parameters"] = ModelParameters()

        # 2. Parse knowledge-base retrieval config
        if knowledge_retrieval:
            result["knowledge_retrieval"] = KnowledgeRetrievalConfig(**knowledge_retrieval)
        else:
            # Provide a default knowledge-base config (empty list)
            result["knowledge_retrieval"] = KnowledgeRetrievalConfig(
                knowledge_bases=[],
                merge_strategy="weighted"
            )

        # 3. Parse memory config
        if memory:
            result["memory"] = MemoryConfig(**memory)

        # 4. Parse variable config
        if variables:
            result["variables"] = [VariableDefinition(**var) for var in variables]

        # 5. Parse tool config
        if tools:
            if isinstance(tools, list):
                result["tools"] = [ToolConfig(**tool_config) for tool_config in tools]
            else:
                result["tools"] = {
                    name: ToolOldConfig(**tool_data)
                    for name, tool_data in tools.items()
                }

        if skills:
            result["skills"] = SkillConfig(**skills)
        else:
            result["skills"] = SkillConfig(enabled=False, all_skills=False, skill_ids=[])

        if features:
            result["features"] = AppFeatures(**features)

        return result


__all__ = ["AgentConfigConverter"]
