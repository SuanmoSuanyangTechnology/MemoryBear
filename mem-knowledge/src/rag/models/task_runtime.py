"""Synchronous model resolution for Knowledge worker tasks."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from redbear_model import ResolvedModelConfig, resolve_model

from ...repositories.model_registry import SyncSQLModelRegistry

if TYPE_CHECKING:
    from redbear_model.runtime import RedBearEmbeddings, RedBearLLM

    from ...runtime import ProcessRuntime


class TaskModelFactory:
    """Resolve credential snapshots in short sessions before model construction."""

    def __init__(self, runtime: ProcessRuntime):
        self._runtime = runtime

    def resolve_config(
        self,
        model_config_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> ResolvedModelConfig:
        with self._runtime.database.sync_session() as session:
            return resolve_model(
                SyncSQLModelRegistry(session),
                model_config_id=model_config_id,
                tenant_id=tenant_id,
            )

    def resolve_embedding(
        self,
        model_config_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> ResolvedModelConfig:
        return self.resolve_config(model_config_id, tenant_id)

    def resolve_chat(
        self,
        model_config_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> ResolvedModelConfig:
        return self.resolve_config(model_config_id, tenant_id)

    def resolve_image(
        self,
        model_config_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> ResolvedModelConfig:
        return self.resolve_config(model_config_id, tenant_id)

    def create_embeddings(
        self,
        model_config_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> RedBearEmbeddings:
        from redbear_model.runtime import RedBearEmbeddings

        config = self.resolve_embedding(model_config_id, tenant_id)
        try:
            return RedBearEmbeddings(
                config,
                client_pool=self._runtime.model_runtime.pool,
            )
        except Exception:
            raise RuntimeError("Failed to initialize embedding model") from None

    def create_llm(
        self,
        model_config_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> RedBearLLM:
        from redbear_model.runtime import RedBearLLM

        config = self.resolve_chat(model_config_id, tenant_id)
        try:
            return RedBearLLM(config, client_pool=self._runtime.model_runtime.pool)
        except Exception:
            raise RuntimeError("Failed to initialize chat model") from None


__all__ = ["TaskModelFactory"]
