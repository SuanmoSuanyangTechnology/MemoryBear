from typing import TYPE_CHECKING

from app.core.memory.llm_tools.openai_client import OpenAIClient
from app.core.memory.utils.config.config_utils import get_model_config
from app.core.models.base import RedBearModelConfig
from pydantic import BaseModel

if TYPE_CHECKING:
    from app.schemas.memory_config_schema import MemoryConfig


async def handle_response(response: type[BaseModel]) -> dict:
    return response.model_dump()


def get_llm_client_from_config(memory_config: "MemoryConfig") -> OpenAIClient:
    """
    Get LLM client from MemoryConfig object.
    
    **PREFERRED METHOD**: Use this function in production code when you have a MemoryConfig object.
    This ensures proper configuration management and multi-tenant support.
    
    Args:
        memory_config: MemoryConfig object containing llm_model_id
        
    Returns:
        OpenAIClient: Initialized LLM client
        
    Raises:
        ValueError: If LLM model ID is not configured or client initialization fails
        
    Example:
        >>> llm_client = get_llm_client_from_config(memory_config)
    """
    if not memory_config.llm_model_id:
        raise ValueError(
            f"Configuration {memory_config.config_id} has no LLM model configured"
        )
    return get_llm_client(str(memory_config.llm_model_id))


def get_llm_client(llm_id: str):
    """
    Get LLM client by model ID.
    
    **LEGACY/TEST METHOD**: Use this function only for:
    - Test/evaluation code where you have a model ID directly
    - Legacy code that hasn't been migrated to MemoryConfig yet
    
    For production code with MemoryConfig, use get_llm_client_from_config() instead.
    
    Args:
        llm_id: LLM model ID (required)
        
    Returns:
        OpenAIClient: Initialized LLM client
        
    Raises:
        ValueError: If llm_id is not provided or client initialization fails
        
    Example:
        >>> # For tests/evaluations only
        >>> llm_client = get_llm_client("model-uuid-string")
    """
    if not llm_id:
        raise ValueError("LLM ID is required but was not provided")

    try:
        model_config = get_model_config(llm_id)
    except Exception as e:
        raise ValueError(f"Invalid LLM ID '{llm_id}': {str(e)}") from e

    try:
        llm_client = OpenAIClient(RedBearModelConfig(
                model_name=model_config.get("model_name"),
                provider=model_config.get("provider"),
                api_key=model_config.get("api_key"),
                base_url=model_config.get("base_url")
            ),type_=model_config.get("type"))
        return llm_client
    except Exception as e:
        model_name = model_config.get('model_name', 'unknown')
        raise ValueError(f"Failed to initialize LLM client for model '{model_name}': {str(e)}") from e


def get_reranker_client(rerank_id: str):
    """
    Get an LLM client configured for reranking.
    
    Args:
        rerank_id: Reranker model ID (required)
        
    Returns:
        OpenAIClient: Initialized client for the reranker model
        
    Raises:
        ValueError: If rerank_id is not provided or client initialization fails
    """
    if not rerank_id:
        raise ValueError("Rerank ID is required but was not provided")
    
    try:
        model_config = get_model_config(rerank_id)
    except Exception as e:
        raise ValueError(f"Invalid rerank ID '{rerank_id}': {str(e)}") from e
    
    try:
        reranker_client = OpenAIClient(RedBearModelConfig(
                model_name=model_config.get("model_name"),
                provider=model_config.get("provider"),
                api_key=model_config.get("api_key"),
                base_url=model_config.get("base_url")
            ),type_=model_config.get("type"))
        return reranker_client
    except Exception as e:
        model_name = model_config.get('model_name', 'unknown')
        raise ValueError(f"Failed to initialize reranker client for model '{model_name}': {str(e)}") from e