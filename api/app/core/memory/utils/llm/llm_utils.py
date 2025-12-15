import os
from pydantic import BaseModel

from app.core.memory.llm_tools.openai_client import OpenAIClient
from app.core.memory.utils.config.config_utils import get_model_config
from app.core.memory.utils.config import definitions as config_defs
from app.core.models.base import RedBearModelConfig

async def handle_response(response: type[BaseModel]) -> dict:
    return response.model_dump()


def get_llm_client(llm_id: str | None = None):
    llm_id = llm_id or config_defs.SELECTED_LLM_ID

    # Validate LLM ID exists before attempting to get config
    if not llm_id:
        raise ValueError("LLM ID is required but was not provided")

    try:
        model_config = get_model_config(llm_id)
    except Exception as e:
        # Re-raise with clear error message about invalid LLM ID
        raise ValueError(f"Invalid LLM ID '{llm_id}': {str(e)}") from e

    try:
        # 移除调试打印，避免污染终端输出
        # print(model_config)
        llm_client = OpenAIClient(RedBearModelConfig(
                model_name=model_config.get("model_name"),
                provider=model_config.get("provider"),
                api_key=model_config.get("api_key"),
                base_url=model_config.get("base_url")
            ),type_=model_config.get("type"))
        # print(llm.dict())
        return llm_client
    except Exception as e:
        model_name = model_config.get('model_name', 'unknown')
        raise ValueError(f"Failed to initialize LLM client for model '{model_name}': {str(e)}") from e


def get_reranker_client(rerank_id: str | None = None):
    """
    Get an LLM client configured for reranking.
    
    Args:
        rerank_id: Optional reranker model ID. If None, uses SELECTED_RERANK_ID.
        
    Returns:
        OpenAIClient: Initialized client for the reranker model
        
    Raises:
        ValueError: If rerank_id is invalid or client initialization fails
    """
    rerank_id = rerank_id or config_defs.SELECTED_RERANK_ID
    
    # Validate rerank ID exists before attempting to get config
    if not rerank_id:
        raise ValueError("Rerank ID is required but was not provided")
    
    try:
        model_config = get_model_config(rerank_id)
    except Exception as e:
        # Re-raise with clear error message about invalid rerank ID
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