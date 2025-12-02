from abc import ABC, abstractmethod
from typing import List, Dict, Any
from pydantic import BaseModel
from app.core.memory.models.config_models import LLMConfig

"""
    model_name: str
    provider: str
    api_key: str
    base_url: Optional[str] = None
    timeout: float = 30.0        # 请求超时时间（秒）
    max_retries: int = 3         # 最大重试次数
    concurrency: int = 5         # 并发限流
    extra_params: Dict[str, Any] = {}
"""
from app.core.models.base import RedBearModelConfig
class LLMClient(ABC):
    def __init__(self, model_config: RedBearModelConfig):
        self.config = model_config

        self.model_name = self.config.model_name
        self.provider = self.config.provider
        self.api_key = self.config.api_key
        self.base_url = self.config.base_url
        self.max_retries = self.config.max_retries

    @abstractmethod
    def chat(self, messages: List[Dict[str, str]]) -> Any:
        pass

    @abstractmethod
    async def response_structured(
        self,
        messages: List[Dict[str, str]],
        response_model: type[BaseModel],
    ) -> type[BaseModel]:
        pass
