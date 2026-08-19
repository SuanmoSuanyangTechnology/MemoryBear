"""Lifecycle boundary for the shared RedBear model client pool."""

from __future__ import annotations

from redbear_model import ModelRuntimeOptions
from redbear_model.runtime.client_pool import ModelClientPool

from ..config import KnowledgeSettings


class ModelRuntimeManager:
    """Lazily own model HTTP clients without resolving model credentials."""

    def __init__(self, settings: KnowledgeSettings):
        self._settings = settings
        self._pool: ModelClientPool | None = None

    @property
    def initialized(self) -> bool:
        return self._pool is not None

    @property
    def pool(self) -> ModelClientPool:
        if self._pool is None:
            self._pool = ModelClientPool(
                ModelRuntimeOptions(
                    timeout_s=self._settings.llm_timeout,
                    max_retries=self._settings.llm_max_retries,
                    concurrency=self._settings.model_concurrency,
                    http_max_connections=(
                        self._settings.model_http_max_connections
                    ),
                    http_max_keepalive_connections=(
                        self._settings.model_http_max_keepalive_connections
                    ),
                    http_trust_env=self._settings.model_http_trust_env,
                    bedrock_max_pool_connections=(
                        self._settings.bedrock_max_pool_connections
                    ),
                    bedrock_max_retries=self._settings.bedrock_max_retries,
                    embedding_batch_size=self._settings.embedding_batch_size,
                )
            )
        return self._pool

    async def aclose(self) -> None:
        pool = self._pool
        self._pool = None
        if pool is not None:
            await pool.aclose()

    def reset_after_fork(self) -> None:
        self._pool = None
