"""
Agent 配置格式转换器
用于将 Pydantic 模型转换为数据库存储格式
"""
from typing import Dict, Any, Optional, Union
from app.schemas.app_schema import (
    KnowledgeRetrievalConfig,
    MemoryConfig,
    VariableDefinition,
    ToolConfig,
    AgentConfigCreate,
    AgentConfigUpdate, ToolOldConfig, SkillConfig,
)


class AgentConfigConverter:
    """Agent 配置格式转换器"""
    
    @staticmethod
    def to_storage_format(config: AgentConfigCreate | AgentConfigUpdate) -> Dict[str, Any]:
        """
        将配置对象转换为数据库存储格式
        
        Args:
            config: AgentConfigCreate 或 AgentConfigUpdate 对象
            
        Returns:
            包含数据库字段的字典
        """
        result = {}
        
        # 1. 模型参数配置
        if hasattr(config, 'model_parameters') and config.model_parameters:
            result["model_parameters"] = config.model_parameters.model_dump()
        
        # 2. 知识库检索配置
        if config.knowledge_retrieval:
            result["knowledge_retrieval"] = config.knowledge_retrieval.model_dump()
        
        # 3. 记忆配置
        if hasattr(config, 'memory') and config.memory:
            result["memory"] = config.memory.model_dump()
        
        # 4. 变量配置
        if hasattr(config, 'variables') and config.variables:
            result["variables"] = [var.model_dump() for var in config.variables]
        
        # 5. 工具配置
        if hasattr(config, 'tools') and config.tools:
            result["tools"] = [tool.model_dump() for tool in config.tools]

        if hasattr(config, "skills") and config.skills:
            result["skills"] = config.skills.model_dump()
        
        return result
    
    @staticmethod
    def from_storage_format(
        model_parameters: Optional[Dict[str, Any]],
        knowledge_retrieval: Optional[Dict[str, Any]],
        memory: Optional[Dict[str, Any]],
        variables: Optional[list],
        tools: Optional[Union[list, Dict[str, Any]]],
        skills: Optional[dict]
    ) -> Dict[str, Any]:
        """
        将数据库存储格式转换为 Pydantic 对象
        
        Args:
            model_parameters: 模型参数配置
            knowledge_retrieval: 知识库检索配置
            memory: 记忆配置
            variables: 变量配置
            tools: 工具配置
            skills: 技能列表
            
        Returns:
            包含 Pydantic 对象的字典
        """
        result = {
            "model_parameters": None,
            "knowledge_retrieval": None,
            "memory": MemoryConfig(enabled=True),
            "variables": [],
            "tools": [],
            "skills": {}
        }
        
        # 1. 解析模型参数配置
        if model_parameters:
            from app.schemas.app_schema import ModelParameters
            if isinstance(model_parameters, ModelParameters):
                result["model_parameters"] = model_parameters
            elif isinstance(model_parameters, dict):
                result["model_parameters"] = ModelParameters(**model_parameters)
            else:
                result["model_parameters"] = ModelParameters()
        
        # 2. 解析知识库检索配置
        if knowledge_retrieval:
            result["knowledge_retrieval"] = KnowledgeRetrievalConfig(**knowledge_retrieval)
        else:
            # 提供默认的知识库配置（空列表）
            result["knowledge_retrieval"] = KnowledgeRetrievalConfig(
                knowledge_bases=[],
                merge_strategy="weighted"
            )
        
        # 3. 解析记忆配置
        if memory:
            result["memory"] = MemoryConfig(**memory)
        
        # 4. 解析变量配置
        if variables:
            result["variables"] = [VariableDefinition(**var) for var in variables]
        
        # 5. 解析工具配置
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
        
        return result
