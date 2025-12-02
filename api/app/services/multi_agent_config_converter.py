"""
多智能体配置格式转换器
用于将 Pydantic 模型转换为数据库存储格式
"""
from typing import Dict, Any, Optional, List
import uuid
from app.schemas.multi_agent_schema import (
    SubAgentConfig,
    RoutingRule,
    ExecutionConfig,
    MultiAgentConfigCreate,
    MultiAgentConfigUpdate,
)


class MultiAgentConfigConverter:
    """多智能体配置格式转换器"""
    
    @staticmethod
    def to_storage_format(config: MultiAgentConfigCreate | MultiAgentConfigUpdate) -> Dict[str, Any]:
        """
        将配置对象转换为数据库存储格式
        
        Args:
            config: MultiAgentConfigCreate 或 MultiAgentConfigUpdate 对象
            
        Returns:
            包含数据库字段的字典
        """
        result = {}
        
        # 1. 子 Agent 配置
        if hasattr(config, 'sub_agents') and config.sub_agents:
            result["sub_agents"] = [
                MultiAgentConfigConverter._convert_uuid_to_str(agent.model_dump())
                for agent in config.sub_agents
            ]
        
        # 2. 路由规则配置
        if hasattr(config, 'routing_rules') and config.routing_rules:
            result["routing_rules"] = [
                MultiAgentConfigConverter._convert_uuid_to_str(rule.model_dump())
                for rule in config.routing_rules
            ]
        
        # 3. 执行配置
        if hasattr(config, 'execution_config') and config.execution_config:
            result["execution_config"] = MultiAgentConfigConverter._convert_uuid_to_str(
                config.execution_config.model_dump()
            )
        
        return result
    
    @staticmethod
    def from_storage_format(
        sub_agents: Optional[List[Dict[str, Any]]],
        routing_rules: Optional[List[Dict[str, Any]]],
        execution_config: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        将数据库存储格式转换为 Pydantic 对象
        
        Args:
            sub_agents: 子 Agent 配置列表
            routing_rules: 路由规则配置列表
            execution_config: 执行配置
            
        Returns:
            包含 Pydantic 对象的字典
        """
        result = {
            "sub_agents": [],
            "routing_rules": [],
            "execution_config": None,
        }
        
        # 1. 解析子 Agent 配置
        if sub_agents:
            result["sub_agents"] = [
                SubAgentConfig(**agent_data)
                for agent_data in sub_agents
            ]
        
        # 2. 解析路由规则配置
        if routing_rules:
            result["routing_rules"] = [
                RoutingRule(**rule_data)
                for rule_data in routing_rules
            ]
        else:
            # 提供默认的空路由规则
            result["routing_rules"] = []
        
        # 3. 解析执行配置
        if execution_config:
            result["execution_config"] = ExecutionConfig(**execution_config)
        else:
            # 提供默认的执行配置
            result["execution_config"] = ExecutionConfig(
                max_iterations=10,
                timeout=300,
                enable_parallel=False,
                error_handling="stop"
            )
        
        return result
    
    @staticmethod
    def _convert_uuid_to_str(obj: Any) -> Any:
        """
        递归转换对象中的所有 UUID 为字符串
        
        Args:
            obj: 要转换的对象（dict, list, UUID 等）
            
        Returns:
            转换后的对象
        """
        if isinstance(obj, uuid.UUID):
            return str(obj)
        elif isinstance(obj, dict):
            return {k: MultiAgentConfigConverter._convert_uuid_to_str(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [MultiAgentConfigConverter._convert_uuid_to_str(item) for item in obj]
        else:
            return obj
    
    @staticmethod
    def enrich_with_published_configs(
        sub_agents: List[Dict[str, Any]],
        get_published_config_func
    ) -> List[Dict[str, Any]]:
        """
        为子 Agent 配置添加发布的 config_id
        
        Args:
            sub_agents: 子 Agent 配置列表
            get_published_config_func: 获取发布配置的函数
            
        Returns:
            增强后的子 Agent 配置列表
        """
        enriched_agents = []
        
        for agent in sub_agents:
            agent_copy = agent.copy()
            
            # 获取该 Agent 当前发布的配置
            if 'agent_id' in agent:
                try:
                    agent_id = uuid.UUID(agent['agent_id']) if isinstance(agent['agent_id'], str) else agent['agent_id']
                    published_config = get_published_config_func(agent_id)
                    
                    if published_config:
                        agent_copy['published_config_id'] = str(published_config.get('id')) if isinstance(published_config, dict) else None
                except Exception as e:
                    # 如果获取失败，记录但不中断
                    from app.core.logging_config import get_business_logger
                    logger = get_business_logger()
                    logger.warning(f"获取 Agent {agent.get('agent_id')} 的发布配置失败: {e}")
            
            enriched_agents.append(agent_copy)
        
        return enriched_agents
    
    @staticmethod
    def create_default_template(app_id: uuid.UUID) -> Dict[str, Any]:
        """
        创建默认的多智能体配置模板
        
        Args:
            app_id: 应用 ID
            
        Returns:
            默认配置模板
        """
        return {
            "app_id": str(app_id),
            "master_agent_id": None,
            "orchestration_mode": "sequential",
            "sub_agents": [],
            "routing_rules": [],
            "execution_config": {
                "max_iterations": 10,
                "timeout": 300,
                "enable_parallel": False,
                "error_handling": "stop"
            },
            "aggregation_strategy": "last",
            "is_active": False
        }
