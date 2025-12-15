"""
模型参数合并器

用于合并 ModelConfig 和 AgentConfig 中的模型参数，
AgentConfig 中的参数优先级更高，可以覆盖 ModelConfig 的默认参数。
"""
from typing import Dict, Any, Optional
from app.core.logging_config import get_business_logger

logger = get_business_logger()


class ModelParameterMerger:
    """模型参数合并器"""
    
    @staticmethod
    def merge_parameters(
        model_config_params: Optional[Dict[str, Any]],
        agent_config_params: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        合并模型配置参数和 Agent 配置参数
        
        优先级：agent_config_params > model_config_params > 默认值
        
        Args:
            model_config_params: ModelConfig.config 中的参数
            agent_config_params: AgentConfig.model_parameters 中的参数
            
        Returns:
            合并后的参数字典
            
        Example:
            >>> model_params = {"temperature": 0.5, "max_tokens": 1000}
            >>> agent_params = {"temperature": 0.8}
            >>> merged = ModelParameterMerger.merge_parameters(model_params, agent_params)
            >>> merged
            {"temperature": 0.8, "max_tokens": 1000}
        """
        # 默认参数
        default_params = {
            "temperature": 0.7,
            "max_tokens": 2000,
            "top_p": 1.0,
            "frequency_penalty": 0.0,
            "presence_penalty": 0.0,
            "n": 1,
            "stop": None
        }
        
        # 合并参数：默认值 -> 模型配置 -> Agent 配置
        merged = default_params.copy()
        
        # 应用模型配置参数
        if model_config_params:
            for key in default_params:
                if key in model_config_params:
                    merged[key] = model_config_params[key]
        
        # 应用 Agent 配置参数（优先级最高）
        if agent_config_params:
            for key in default_params:
                if key in agent_config_params and agent_config_params[key] is not None:
                    merged[key] = agent_config_params[key]
        
        # 移除 None 值
        merged = {k: v for k, v in merged.items() if v is not None}
        
        logger.debug(
            "参数合并完成",
            extra={
                "model_params": model_config_params,
                "agent_params": agent_config_params,
                "merged": merged
            }
        )
        
        return merged
    
    @staticmethod
    def get_effective_parameters(
        model_config: Optional[Any],
        agent_config: Optional[Any]
    ) -> Dict[str, Any]:
        """
        获取有效的模型参数（从 ORM 对象中提取并合并）
        
        Args:
            model_config: ModelConfig ORM 对象
            agent_config: AgentConfig ORM 对象
            
        Returns:
            合并后的参数字典
        """
        # 提取模型配置参数
        model_params = None
        if model_config and hasattr(model_config, 'config'):
            model_params = model_config.config
        
        # 提取 Agent 配置参数
        agent_params = None
        if agent_config and hasattr(agent_config, 'model_parameters'):
            agent_params = agent_config.model_parameters
        
        return ModelParameterMerger.merge_parameters(model_params, agent_params)
    
    @staticmethod
    def format_for_llm_call(parameters: Dict[str, Any]) -> Dict[str, Any]:
        """
        格式化参数用于 LLM API 调用
        
        不同的 LLM 提供商可能需要不同的参数格式，
        这个方法可以根据需要进行转换。
        
        Args:
            parameters: 合并后的参数字典
            
        Returns:
            格式化后的参数字典
        """
        # 基本格式化（可以根据不同提供商扩展）
        formatted = parameters.copy()
        
        # 确保参数在有效范围内
        if "temperature" in formatted:
            formatted["temperature"] = max(0.0, min(2.0, formatted["temperature"]))
        
        if "max_tokens" in formatted:
            formatted["max_tokens"] = max(1, min(32000, formatted["max_tokens"]))
        
        if "top_p" in formatted:
            formatted["top_p"] = max(0.0, min(1.0, formatted["top_p"]))
        
        if "frequency_penalty" in formatted:
            formatted["frequency_penalty"] = max(-2.0, min(2.0, formatted["frequency_penalty"]))
        
        if "presence_penalty" in formatted:
            formatted["presence_penalty"] = max(-2.0, min(2.0, formatted["presence_penalty"]))
        
        if "n" in formatted:
            formatted["n"] = max(1, min(10, formatted["n"]))
        
        return formatted


def merge_model_parameters(
    model_config_params: Optional[Dict[str, Any]],
    agent_config_params: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    合并模型参数的便捷函数
    
    Args:
        model_config_params: ModelConfig.config 中的参数
        agent_config_params: AgentConfig.model_parameters 中的参数
        
    Returns:
        合并后的参数字典
    """
    return ModelParameterMerger.merge_parameters(model_config_params, agent_config_params)
