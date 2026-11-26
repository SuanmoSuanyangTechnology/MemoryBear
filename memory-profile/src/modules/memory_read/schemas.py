"""Transport schemas for the migrated memory-read endpoints.

Three request shapes, matching the call surfaces on the API side:

* ``ReadSyncBody`` — the API-key (``/v1``) face: single or multiple hosts, JSON
  response;
* ``ReadInternalBody`` — the API-key internal face: one host plus retrieval
  options (``limit`` / ``includes`` / ``skip_summary`` / ``enable_rerank`` /
  ``include_metadata``), JSON response;
* ``ReadStreamBody`` — the management (``/api``) face: one host plus the chat
  session used for history, SSE response.

Response payloads stay loose (``dict[str, Any]``) because all faces return the
legacy V1 shapes: ``{answer, intermediate_outputs}`` for one host and a map keyed
by the requested end-user id for several.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from src.core.domain.enums import MemorySource, StorageType


class ReadSyncBody(BaseModel):
    """Body of ``POST /internal/v1/memory/read/sync``."""

    message: str
    search_switch: str
    end_user_id: str | None = None
    end_user_ids: list[str] | None = None
    enable_rerank: bool = Field(default=False)

    @model_validator(mode="after")
    def check_exactly_one_id_key(self) -> ReadSyncBody:
        if not self.end_user_id and not self.end_user_ids:
            raise ValueError("At least one of end_user_id or end_user_ids must be provided")
        if self.end_user_id and self.end_user_ids:
            raise ValueError("Provide exactly one of end_user_id or end_user_ids, not both")
        return self

    def resolved_end_user_ids(self) -> tuple[str, ...]:
        if self.end_user_ids:
            return tuple(self.end_user_ids)
        return (self.end_user_id or "",)


class ReadInternalBody(BaseModel):
    """Body of ``POST /internal/v1/memory/read/internal``.

    Mirrors the API-side ``InternalReadInput``: one host plus the retrieval
    options the internal call surface exposes. ``includes`` carries the node
    families (``MemorySource`` values such as ``Chunk``/``Statement``); the
    transport validates them here so a bad value fails before any adapter runs.
    """

    message: str
    search_switch: str
    end_user_id: str
    #: Present in the API-side ``InternalReadInput`` for wire compatibility; the
    #: API handler does not act on either value (config is resolved from the end
    #: user), so they stay accepted-and-ignored here as well.
    session_id: UUID = Field(default_factory=uuid4)
    config_id: str | None = None
    includes: list[str] | None = Field(default=None)
    limit: int = Field(default=10, ge=1, le=100)
    skip_summary: bool = Field(default=False)
    enable_rerank: bool = Field(default=False)
    include_metadata: bool = Field(default=True)

    @field_validator("includes")
    @classmethod
    def check_includes(cls, values: list[str] | None) -> list[str] | None:
        return _validate_includes(values)


def _validate_includes(values: list[str] | None) -> list[str] | None:
    """Reject node families the retrieval pipeline cannot filter on."""

    if values is None:
        return None
    # ``Rag`` is the public source value of a RAG hit, not a node family the
    # pipeline can filter on; ``RagMemory`` is the storage label.
    known = {
        member.value for member in MemorySource if member is not MemorySource.RAG
    }
    unknown = [value for value in values if value not in known]
    if unknown:
        raise ValueError(f"unknown memory node type(s): {', '.join(unknown)}")
    return values


class AppServiceReadBody(BaseModel):
    """Body of ``POST /api/internal/app-service/memory/read``.

    Mirrors the host ``MemoryReadRequest`` the application service already
    sends, field for field, so moving the endpoint here changes no caller: the
    query is ``query`` (not ``message``), ``config_id`` names the configuration
    explicitly (the host honours it instead of resolving the workspace's
    active one), and ``draft`` / ``record_display`` / ``history`` /
    ``storage_type`` are the caller's, not the face's.
    """

    end_user_id: str
    query: str
    search_switch: str
    config_id: UUID | None = None
    history: list[Any] = Field(default_factory=list)
    limit: int = Field(default=10, ge=1, le=100)
    includes: list[str] | None = None
    skip_summary: bool = False
    enable_rerank: bool = False
    record_display: bool = False
    storage_type: str = "neo4j"
    user_rag_memory_id: str = ""
    conversation_id: str | None = None
    language: str = "zh"
    draft: bool = False
    app_id: UUID | None = None

    @field_validator("includes")
    @classmethod
    def check_includes(cls, values: list[str] | None) -> list[str] | None:
        return _validate_includes(values)

    @field_validator("storage_type")
    @classmethod
    def check_storage_type(cls, value: str) -> str:
        known = {member.value for member in StorageType}
        if value not in known:
            raise ValueError(
                f"unsupported storage type: {value} (expected one of {', '.join(sorted(known))})"
            )
        return value


class ReadStreamBody(BaseModel):
    """Body of ``POST /internal/v1/memory/read/stream``."""

    message: str
    search_switch: str
    end_user_id: str
    session_id: UUID = Field(default_factory=uuid4)


__all__ = [
    "AppServiceReadBody",
    "ReadInternalBody",
    "ReadStreamBody",
    "ReadSyncBody",
]