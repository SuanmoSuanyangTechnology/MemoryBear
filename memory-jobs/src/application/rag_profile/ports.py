"""Dependencies used by this context, implemented by infrastructure."""

from typing import Any, Protocol


class RAGProfileRepository(Protocol):
    def save(self, context: dict[str, Any], data: dict[str, Any]) -> None: ...


class RAGProfileSource(Protocol):
    def rag_messages(self, user_id: str, limit: int) -> list[str]: ...
