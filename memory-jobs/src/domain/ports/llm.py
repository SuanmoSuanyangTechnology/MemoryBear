"""Chat-model port.

The domain service depends on the two calls it makes, not on the provider
client. ``SyncChatModel`` in the infrastructure layer satisfies this protocol
structurally, so no adapter class is needed for the model itself.
"""

from __future__ import annotations

from typing import Any, Protocol


class ChatResponse(Protocol):
    """The part of a provider response the domain reads."""

    text: str


class ChatModel(Protocol):
    """Blocking chat calls used by the generation domain services."""

    def invoke(self, input: Any, **kwargs: Any) -> ChatResponse: ...

    def call_structured(
        self, input: Any, schema: dict[str, Any] | type, **kwargs: Any
    ) -> Any: ...
