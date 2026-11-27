"""Dependencies used by this context, implemented by infrastructure."""

from typing import Any, Protocol


class ConversationDetailRepository(Protocol):
    def read(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def generation_context(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def save(
        self, payload: dict[str, Any], context: dict[str, Any], data: dict[str, Any]
    ) -> None: ...
