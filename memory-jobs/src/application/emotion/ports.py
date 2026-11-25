"""Dependencies used by this context, implemented by infrastructure."""

from typing import Any, Protocol


class EmotionSuggestionsRepository(Protocol):
    def save(self, context: dict[str, Any], data: dict[str, Any]) -> None: ...


class EmotionSource(Protocol):
    def emotion_context(self, user_id: str) -> dict[str, Any]: ...
