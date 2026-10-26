"""Backend protocol: what the scheduler needs from any microVM substrate.

One protocol for every substrate (a real microVM platform or the in-memory fake).
A backend knows instances and code contexts; it knows nothing about users, quotas,
TTLs or eviction.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# Metadata written on every instance so orphans can be matched to a user.
META_KEY = "sbx.key"
META_TENANT = "sbx.tenant"

STATE_RUNNING = "running"
STATE_PAUSED = "paused"

KIND_FILE = "file"
KIND_DIR = "dir"
KIND_OTHER = "other"  # symlink, device, ...


class BackendError(Exception):
    """Base class for backend failures."""


class BackendUnavailable(BackendError):
    """The substrate cannot serve the call right now (retryable)."""


class CapacityExhausted(BackendError):
    """The substrate itself has no room for another instance."""


class InstanceGone(BackendError):
    """The instance no longer exists (crashed, removed, never created)."""


class ContextGone(BackendError):
    """The code context (kernel) no longer exists inside a live instance."""


class FileNotFound(BackendError):
    """The path does not exist inside a live instance."""


class IsADirectory(BackendError):
    """A file operation was pointed at a directory."""


@dataclass(frozen=True)
class InstanceSpec:
    key_digest: str
    tenant_id: str
    template: str
    metadata: dict[str, str] = field(default_factory=dict)
    allow_internet_access: bool | None = None
    network: dict[str, object] | None = None


@dataclass(frozen=True)
class InstanceHandle:
    instance_id: str
    created_at_ms: int


@dataclass(frozen=True)
class InstanceInfo:
    instance_id: str
    metadata: dict[str, str]
    state: str
    created_at_ms: int


@dataclass(frozen=True)
class RunResult:
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    duration_ms: int = 0


@dataclass(frozen=True)
class FileEntry:
    name: str
    path: str
    kind: str  # KIND_FILE / KIND_DIR / KIND_OTHER
    size: int
    mtime_ms: int


@runtime_checkable
class SandboxBackend(Protocol):
    name: str

    async def create(self, spec: InstanceSpec) -> InstanceHandle: ...

    async def pause(self, instance_id: str) -> None: ...

    async def resume(self, instance_id: str) -> None: ...

    async def destroy(self, instance_id: str) -> None: ...

    async def list_owned(self) -> list[InstanceInfo]: ...

    async def create_context(self, instance_id: str, language: str) -> str: ...

    async def run_code(
        self, instance_id: str, context_id: str, code: str, timeout_s: float
    ) -> RunResult: ...

    async def delete_context(self, instance_id: str, context_id: str) -> None: ...

    async def restart_context(self, instance_id: str, context_id: str) -> None: ...

    # -- files (absolute, already normalised paths; the caller owns path policy) -----

    async def stat(self, instance_id: str, path: str) -> FileEntry: ...

    async def list_dir(self, instance_id: str, path: str, depth: int = 1) -> list[FileEntry]: ...

    def read_stream(self, instance_id: str, path: str) -> AsyncIterator[bytes]:
        """Async generator of the file's bytes; errors surface on the first iteration."""
        ...

    async def write_stream(
        self, instance_id: str, path: str, chunks: AsyncIterator[bytes]
    ) -> int:
        """Write (overwrite) a file, creating parents; returns the bytes written."""
        ...

    async def make_dir(self, instance_id: str, path: str) -> None: ...

    async def remove(self, instance_id: str, path: str) -> None:
        """Remove a file or a directory tree; a missing path is not an error."""
        ...

    async def health(self) -> bool: ...
