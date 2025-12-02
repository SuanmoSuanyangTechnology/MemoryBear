from typing import List

from app.core.memory.src.llm_tools.embedder_client import EmbedderClient
from app.core.models.base import RedBearModelConfig
# from app.models.models_model import ModelType
from app.core.models.embedding import RedBearEmbeddings


class OpenAIEmbedderClient(EmbedderClient):
    def __init__(self, model_config: RedBearModelConfig):
        super().__init__(model_config)

    async def response(
        self,
        messages: List[str],
    ) -> List[List[float]]:
        texts: List[str] = [str(m) for m in messages if m is not None]

        model = RedBearEmbeddings(RedBearModelConfig(
                model_name=self.model_name,
                provider=self.provider,
                api_key=self.api_key,
                base_url=self.base_url,
            ))
        embeddings = await model.aembed_documents(texts)
        return embeddings
