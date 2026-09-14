"""Synchronous model resolution for Knowledge worker tasks."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from redbear_model import (
    ModelCapability,
    ModelConfigNotFoundError,
    ModelConfigSnapshot,
    ModelProvider,
    ModelType,
    ResolvedModelConfig,
    UnsupportedMultimodalModelError,
    resolve_from_channel_pool,
    resolve_model,
)

from ...repositories.model_registry import (
    AsyncSQLModelRegistry,
    SyncSQLModelRegistry,
    credential_cipher,
)

if TYPE_CHECKING:
    from redbear_model.runtime import (
        RedBearAudioTranscriber,
        RedBearEmbeddings,
        RedBearLLM,
        RedBearVideoUnderstanding,
    )

    from ...runtime import ProcessRuntime


MEDIA_MODEL_FIELDS = {
    "audio2text_id": ("qwen3-asr-flash-filetrans", {ModelType.ASR}),
    "video2text_id": ("qwen3.5-omni-plus-2026-03-15", {ModelType.LLM, ModelType.CHAT}),
}


def validate_media_model_kind(config: ModelConfigSnapshot, field: str) -> None:
    name, types = MEDIA_MODEL_FIELDS[field]
    if (config.provider is not ModelProvider.DASHSCOPE or config.name != name
            or config.model_type not in types
            or (field == "video2text_id" and ModelCapability.VIDEO not in config.capabilities)):
        raise UnsupportedMultimodalModelError(field)


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

    def _resolve_media(
        self, model_config_id: uuid.UUID, tenant_id: uuid.UUID, field: str,
    ) -> ResolvedModelConfig:
        if model_config_id is None:
            raise ValueError("Media model ID is required")
        with self._runtime.database.sync_session() as session:
            registry = SyncSQLModelRegistry(session)
            config = registry.get_model_config(model_config_id, tenant_id)
            if config is None:
                raise ModelConfigNotFoundError(model_config_id)
            validate_media_model_kind(config, field)
            channels = registry.list_active_channels(tenant_id, config.provider.value)
            return resolve_from_channel_pool(
                config, channels, tenant_id=tenant_id, cipher=credential_cipher(),
            )

    async def _aresolve_media(
        self, model_config_id: uuid.UUID, tenant_id: uuid.UUID, field: str,
    ) -> ResolvedModelConfig:
        if model_config_id is None:
            raise ValueError("Media model ID is required")
        async with self._runtime.database.async_session() as session:
            registry = AsyncSQLModelRegistry(session)
            config = await registry.get_model_config(model_config_id, tenant_id)
            if config is None:
                raise ModelConfigNotFoundError(model_config_id)
            validate_media_model_kind(config, field)
            channels = await registry.list_active_channels(tenant_id, config.provider.value)
            return resolve_from_channel_pool(
                config, channels, tenant_id=tenant_id, cipher=credential_cipher(),
            )

    def create_audio_transcriber(
        self, model_config_id: uuid.UUID, tenant_id: uuid.UUID,
    ) -> RedBearAudioTranscriber:
        from redbear_model.runtime import RedBearAudioTranscriber
        config = self._resolve_media(model_config_id, tenant_id, "audio2text_id")
        return RedBearAudioTranscriber(config, client_pool=self._runtime.model_runtime.pool)

    async def acreate_audio_transcriber(
        self, model_config_id: uuid.UUID, tenant_id: uuid.UUID,
    ) -> RedBearAudioTranscriber:
        from redbear_model.runtime import RedBearAudioTranscriber
        config = await self._aresolve_media(model_config_id, tenant_id, "audio2text_id")
        return RedBearAudioTranscriber(config, client_pool=self._runtime.model_runtime.pool)

    def create_video_understanding(
        self, model_config_id: uuid.UUID, tenant_id: uuid.UUID,
    ) -> RedBearVideoUnderstanding:
        from redbear_model.runtime import RedBearVideoUnderstanding
        config = self._resolve_media(model_config_id, tenant_id, "video2text_id")
        return RedBearVideoUnderstanding(config, client_pool=self._runtime.model_runtime.pool)

    async def acreate_video_understanding(
        self, model_config_id: uuid.UUID, tenant_id: uuid.UUID,
    ) -> RedBearVideoUnderstanding:
        from redbear_model.runtime import RedBearVideoUnderstanding
        config = await self._aresolve_media(model_config_id, tenant_id, "video2text_id")
        return RedBearVideoUnderstanding(config, client_pool=self._runtime.model_runtime.pool)

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
