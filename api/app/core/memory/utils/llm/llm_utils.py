from typing import TYPE_CHECKING

from app.core.memory.llm_tools.openai_client import OpenAIClient
from app.core.models.base import RedBearModelConfig
from pydantic import BaseModel
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.schemas.memory_config_schema import MemoryConfig


async def handle_response(response: type[BaseModel]) -> dict:
    return response.model_dump()


class MemoryClientFactory:
    """
    Factory for creating LLM, embedder, and reranker clients.
    
    Initialize once with db session, then call methods without passing db each time.
    
    Example:
        >>> factory = MemoryClientFactory(db)
        >>> llm_client = factory.get_llm_client(model_id)
        >>> embedder_client = factory.get_embedder_client(embedding_id)
    """
    
    def __init__(self, db: Session):
        from app.services.memory_config_service import MemoryConfigService
        self._config_service = MemoryConfigService(db)
    
    def get_llm_client(self, llm_id: str) -> OpenAIClient:
        """Get LLM client by model ID."""
        if not llm_id:
            raise ValueError("LLM ID is required")
        
        try:
            model_config = self._config_service.get_model_config(llm_id)
        except Exception as e:
            raise ValueError(f"Invalid LLM ID '{llm_id}': {str(e)}") from e
        
        try:
            return OpenAIClient(
                RedBearModelConfig(
                    model_name=model_config.get("model_name"),
                    provider=model_config.get("provider"),
                    api_key=model_config.get("api_key"),
                    base_url=model_config.get("base_url")
                ),
                type_=model_config.get("type")
            )
        except Exception as e:
            model_name = model_config.get('model_name', 'unknown')
            raise ValueError(f"Failed to initialize LLM client for model '{model_name}': {str(e)}") from e
    
    def get_embedder_client(self, embedding_id: str):
        """Get embedder client by model ID."""
        from app.core.memory.llm_tools.openai_embedder import OpenAIEmbedderClient
        
        if not embedding_id:
            raise ValueError("Embedding ID is required")
        
        try:
            embedder_config = self._config_service.get_embedder_config(embedding_id)
        except Exception as e:
            raise ValueError(f"Invalid embedding ID '{embedding_id}': {str(e)}") from e
        
        try:
            return OpenAIEmbedderClient(
                RedBearModelConfig(
                    model_name=embedder_config.get("model_name"),
                    provider=embedder_config.get("provider"),
                    api_key=embedder_config.get("api_key"),
                    base_url=embedder_config.get("base_url")
                )
            )
        except Exception as e:
            model_name = embedder_config.get('model_name', 'unknown')
            raise ValueError(f"Failed to initialize embedder client for model '{model_name}': {str(e)}") from e
    
    def get_reranker_client(self, rerank_id: str) -> OpenAIClient:
        """Get reranker client by model ID."""
        if not rerank_id:
            raise ValueError("Rerank ID is required")
        
        try:
            model_config = self._config_service.get_model_config(rerank_id)
        except Exception as e:
            raise ValueError(f"Invalid rerank ID '{rerank_id}': {str(e)}") from e
        
        try:
            return OpenAIClient(
                RedBearModelConfig(
                    model_name=model_config.get("model_name"),
                    provider=model_config.get("provider"),
                    api_key=model_config.get("api_key"),
                    base_url=model_config.get("base_url")
                ),
                type_=model_config.get("type")
            )
        except Exception as e:
            model_name = model_config.get('model_name', 'unknown')
            raise ValueError(f"Failed to initialize reranker client for model '{model_name}': {str(e)}") from e

    def get_llm_client_from_config(self, memory_config: "MemoryConfig") -> OpenAIClient:
        """Get LLM client from MemoryConfig object.
        
        Args:
            memory_config: Configuration containing llm_model_id
            
        Returns:
            OpenAIClient configured for the LLM model
            
        Raises:
            ValueError: If memory_config has no LLM model configured
        """
        if not memory_config.llm_model_id:
            raise ValueError(
                f"Configuration {memory_config.config_id} has no LLM model configured"
            )
        return self.get_llm_client(str(memory_config.llm_model_id))

    def get_embedder_client_from_config(self, memory_config: "MemoryConfig"):
        """Get embedder client from MemoryConfig object.
        
        Args:
            memory_config: Configuration containing embedding_model_id
            
        Returns:
            OpenAIEmbedderClient configured for the embedding model
            
        Raises:
            ValueError: If memory_config has no embedding model configured
        """
        if not memory_config.embedding_model_id:
            raise ValueError(
                f"Configuration {memory_config.config_id} has no embedding model configured"
            )
        return self.get_embedder_client(str(memory_config.embedding_model_id))

    def get_reranker_client_from_config(self, memory_config: "MemoryConfig") -> OpenAIClient:
        """Get reranker client from MemoryConfig object.
        
        Args:
            memory_config: Configuration containing rerank_model_id
            
        Returns:
            OpenAIClient configured for the reranker model
            
        Raises:
            ValueError: If memory_config has no rerank model configured
        """
        if not memory_config.rerank_model_id:
            raise ValueError(
                f"Configuration {memory_config.config_id} has no rerank model configured"
            )
        return self.get_reranker_client(str(memory_config.rerank_model_id))


# Legacy functions for backward compatibility
def get_llm_client_from_config(memory_config: "MemoryConfig", db: Session) -> OpenAIClient:
    """Get LLM client from MemoryConfig object.
    
    DEPRECATED: Use MemoryClientFactory(db).get_llm_client_from_config(memory_config) instead.
    
    This function is maintained for backward compatibility during migration to the
    factory pattern. New code should create a MemoryClientFactory instance and use
    its get_llm_client_from_config method directly.
    
    Args:
        memory_config: Configuration containing llm_model_id
        db: Database session
        
    Returns:
        OpenAIClient configured for the LLM model
        
    Raises:
        ValueError: If memory_config has no LLM model configured
    """
    return MemoryClientFactory(db).get_llm_client_from_config(memory_config)


def get_llm_client(llm_id: str, db: Session) -> OpenAIClient:
    """Get LLM client by model ID.
    
    DEPRECATED: Use MemoryClientFactory(db).get_llm_client(llm_id) instead.
    
    This function is maintained for backward compatibility during migration to the
    factory pattern. New code should create a MemoryClientFactory instance and use
    its get_llm_client method directly.
    
    Args:
        llm_id: LLM model ID
        db: Database session
        
    Returns:
        OpenAIClient configured for the LLM model
    """
    return MemoryClientFactory(db).get_llm_client(llm_id)


def get_embedder_client(embedding_id: str, db: Session):
    """Get embedder client by model ID.
    
    DEPRECATED: Use MemoryClientFactory(db).get_embedder_client(embedding_id) instead.
    
    This function is maintained for backward compatibility during migration to the
    factory pattern. New code should create a MemoryClientFactory instance and use
    its get_embedder_client method directly.
    
    Args:
        embedding_id: Embedding model ID
        db: Database session
        
    Returns:
        OpenAIEmbedderClient configured for the embedding model
    """
    return MemoryClientFactory(db).get_embedder_client(embedding_id)


def get_reranker_client(rerank_id: str, db: Session) -> OpenAIClient:
    """Get reranker client by model ID.
    
    DEPRECATED: Use MemoryClientFactory(db).get_reranker_client(rerank_id) instead.
    
    This function is maintained for backward compatibility during migration to the
    factory pattern. New code should create a MemoryClientFactory instance and use
    its get_reranker_client method directly.
    
    Args:
        rerank_id: Reranker model ID
        db: Database session
        
    Returns:
        OpenAIClient configured for the reranker model
    """
    return MemoryClientFactory(db).get_reranker_client(rerank_id)
