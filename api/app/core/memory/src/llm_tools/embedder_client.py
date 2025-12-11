from abc import ABC, abstractmethod
from typing import List

from app.core.models.base import RedBearModelConfig
class EmbedderClient(ABC):
    def __init__(self, model_config: RedBearModelConfig):
        self.config = model_config

        self.model_name = model_config.model_name
        self.provider = model_config.provider
        self.api_key = model_config.api_key
        self.base_url = model_config.base_url
        self.max_retries = model_config.max_retries
        # self.dimension = model_config.dimension


    @abstractmethod
    async def response(
        self,
        messages: List[str],
        ) -> List[str]:
        pass
