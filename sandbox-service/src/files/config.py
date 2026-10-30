"""File-flow settings: ``SANDBOX_ARTIFACT_*`` and ``SANDBOX_FILE_*`` environment variables."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FilesConfig:
    # HMAC secret of artifact ids. Unset: the service generates a random one at start-up
    # (fine for one replica; ids then do not survive a restart). Never logged.
    artifact_key: str | None = field(default=None, repr=False)
    artifact_ttl_s: float = 3600.0
    max_artifacts: int = 100  # one listing returns at most this many entries
    max_file_bytes: int = 100 * 1024 * 1024  # single file limit for upload and download
    # Chunk the service reads from the backend / a client when it forwards a file.
    # Not a tunable; kept here so tests can shrink it.
    chunk_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        if self.artifact_key is not None and len(self.artifact_key) < 16:
            raise ValueError("artifact_key must be at least 16 characters")
        if self.artifact_ttl_s <= 0:
            raise ValueError("artifact_ttl_s must be > 0")
        if self.max_artifacts < 1 or self.max_file_bytes < 1 or self.chunk_bytes < 1:
            raise ValueError("max_artifacts, max_file_bytes and chunk_bytes must be >= 1")
