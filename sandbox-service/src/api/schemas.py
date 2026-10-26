"""Request bodies of the internal HTTP API.

Only shape is checked here (types, no unknown fields). Value rules (non-empty ids,
length, supported languages, positive timeout) stay in ``keys.py`` and the manager so
there is a single place that decides them.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SandboxRef(_Body):
    """The already verified principal; the caller (main app) is trusted to fill it."""

    tenant_id: str
    workspace_id: str
    end_user_id: str


class ExecuteRequest(SandboxRef):
    conversation_id: str
    code: str
    language: str = "python3"
    timeout_s: float | None = None


class CommandRequest(SandboxRef):
    conversation_id: str
    cmd: str
    cwd: str | None = None
    env: dict[str, str] | None = None
    timeout_s: float | None = None


class FilePathRequest(SandboxRef):
    path: str


class FileListRequest(FilePathRequest):
    depth: int = 1


class ArtifactListRequest(SandboxRef):
    conversation_id: str


class CloseConversationRequest(SandboxRef):
    conversation_id: str


class DestroyUserRequest(SandboxRef):
    pass
