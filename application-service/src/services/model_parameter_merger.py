"""
Model parameter merger.

Merges model parameters from ModelConfig and AgentConfig; AgentConfig
parameters take precedence and can override ModelConfig defaults.
"""
import logging

from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class ModelParameterMerger:
    """Model parameter merger."""
    
    @staticmethod
    def merge_parameters(
        model_config_params: Optional[Dict[str, Any]],
        agent_config_params: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Merge model config parameters with Agent config parameters.
        
        Precedence: agent_config_params > model_config_params > defaults.
        
        Args:
            model_config_params: Parameters from ModelConfig.config
            agent_config_params: Parameters from AgentConfig.model_parameters
            
        Returns:
            The merged parameter dictionary.
            
        Example:
            >>> model_params = {"temperature": 0.5, "max_tokens": 1000}
            >>> agent_params = {"temperature": 0.8}
            >>> merged = ModelParameterMerger.merge_parameters(model_params, agent_params)
            >>> merged
            {"temperature": 0.8, "max_tokens": 1000}
        """
        # Default parameters
        default_params = {
            "temperature": 0.7,
            "max_tokens": 2000,
            "top_p": 1.0,
            "frequency_penalty": 0.0,
            "presence_penalty": 0.0,
            "n": 1,
            "stop": None,
            "deep_thinking": False,
            "thinking_budget_tokens": None,
            "json_output": False
        }
        
        # Merge order: defaults -> model config -> Agent config
        merged = default_params.copy()
        
        # Convert Pydantic objects to dicts
        if model_config_params and hasattr(model_config_params, 'model_dump'):
            model_config_params = model_config_params.model_dump()
        if agent_config_params and hasattr(agent_config_params, 'model_dump'):
            agent_config_params = agent_config_params.model_dump()
        
        # Apply model config parameters
        if model_config_params:
            for key in default_params:
                if key in model_config_params:
                    merged[key] = model_config_params[key]
        
        # Apply Agent config parameters (highest precedence)
        if agent_config_params:
            for key in default_params:
                if key in agent_config_params and agent_config_params[key] is not None:
                    merged[key] = agent_config_params[key]
        
        # Drop None values
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
        Get the effective model parameters (extract from ORM objects and merge).
        
        Args:
            model_config: ModelConfig ORM object
            agent_config: AgentConfig ORM object
            
        Returns:
            The merged parameter dictionary.
        """
        # Extract model config parameters
        model_params = None
        if model_config and hasattr(model_config, 'config'):
            model_params = model_config.config
        
        # Extract Agent config parameters
        agent_params = None
        if agent_config and hasattr(agent_config, 'model_parameters'):
            agent_params = agent_config.model_parameters
        
        return ModelParameterMerger.merge_parameters(model_params, agent_params)
    
    @staticmethod
    def format_for_llm_call(parameters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format parameters for LLM API calls.
        
        Different LLM providers may expect different parameter formats;
        this method can convert them as needed.
        
        Args:
            parameters: The merged parameter dictionary
            
        Returns:
            The formatted parameter dictionary.
        """
        # Basic formatting (can be extended per provider)
        formatted = parameters.copy()
        
        # Clamp parameters into their valid ranges
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
    Convenience function for merging model parameters.
    
    Args:
        model_config_params: Parameters from ModelConfig.config
        agent_config_params: Parameters from AgentConfig.model_parameters
        
    Returns:
        The merged parameter dictionary.
    """
    return ModelParameterMerger.merge_parameters(model_config_params, agent_config_params)
