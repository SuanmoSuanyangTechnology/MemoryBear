"""Adapt the model service manager to the application's chat-model factory port."""

from __future__ import annotations

from src.application.ports.chat_model import ChatModel
from src.domain.shared.ids import ModelConfigId, TenantId
from src.infrastructure.model_service import ModelServiceManager


class ModelServiceChatModelFactory:
    """Turn a resolved configuration into a blocking chat model."""

    def __init__(self, manager: ModelServiceManager) -> None:
        self._manager = manager

    def chat_model(
        self,
        config_id: ModelConfigId,
        tenant_id: TenantId,
        *,
        timeout_seconds: float | None = None,
    ) -> ChatModel:
        return self._manager.chat_model(
            config_id.value,
            tenant_id.value,
            timeout_seconds=timeout_seconds,
        )
