from pydantic import BaseModel
from sqlalchemy.orm import Session


async def handle_response(response: type[BaseModel]) -> dict:
    return response.model_dump()


class MemoryClientFactory:
    """
    Factory for creating embedder clients.

    Initialize once with db session, then call methods without passing db each time.

    Example:
        >>> factory = MemoryClientFactory(db)
        >>> embedder_client = factory.get_embedder_client(embedding_id)
    """

    def __init__(self, db: Session, tenant_id=None):
        from app.services.memory_config_service import MemoryConfigService
        self._config_service = MemoryConfigService(db)
        self._tenant_id = tenant_id

    def get_embedder_client(self, embedding_id: str, tenant_id=None):
        """Get embedder client by model ID."""
        from app.core.memory.llm_tools.openai_embedder import OpenAIEmbedderClient

        if not embedding_id:
            raise ValueError("Embedding ID is required")

        try:
            ref = self._config_service.resolve_model_ref(
                embedding_id,
                tenant_id=tenant_id or self._tenant_id,
            )
        except Exception as e:
            raise ValueError(f"Invalid embedding ID '{embedding_id}': {str(e)}") from e

        try:
            return OpenAIEmbedderClient(remote=ref)
        except Exception as e:
            raise ValueError(
                f"Failed to initialize embedder client for model '{embedding_id}': {str(e)}"
            ) from e
