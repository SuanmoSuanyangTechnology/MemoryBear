"""Chat-model factory port.

The factory turns a resolved configuration into a callable model. The domain
only sees ``ChatModel``; the application uses this port to build one per call,
which is what lets interest distribution give each LLM call its own timeout.
"""

from __future__ import annotations

from typing import Protocol

from src.application.ports.chat_model import ChatModel
from src.domain.shared.ids import ModelConfigId, TenantId


class ChatModelFactory(Protocol):
    def chat_model(
        self,
        config_id: ModelConfigId,
        tenant_id: TenantId,
        *,
        timeout_seconds: float | None = None,
    ) -> ChatModel: ...
