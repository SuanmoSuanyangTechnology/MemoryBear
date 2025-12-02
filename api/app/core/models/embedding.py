
from typing import Any, Dict, List, Optional, TypeVar, Callable
from langchain_core.embeddings import Embeddings

from app.core.models.base import RedBearModelConfig,get_provider_embedding_class,RedBearModelFactory

class RedBearEmbeddings(Embeddings):
    """Embedding → 完全符合 LangChain Embeddings"""
    def __init__(self, config: RedBearModelConfig):
        self._model = self._create_model(config)
        self._config = config

    def _create_model(self, config: RedBearModelConfig) -> Embeddings:
        """根据配置创建模型"""
        embedding_class = get_provider_embedding_class(config.provider)
        model_params = RedBearModelFactory.get_model_params(config)
        return embedding_class(**model_params)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._model.embed_documents(texts)

    def embed_query(self, text: str) -> List[float]:
        return self._model.embed_query(text)
