"""Instance registry.

In-memory for now. The production version is Redis-backed (shared by all
sandbox-service replicas) and must offer the same methods; the manager only talks to
this interface.

``tombstones`` remember users whose workspace was destroyed involuntarily
(eviction, retention / lifetime expiry, lost instance) so that the next call can
report ``workspace_reset`` to the model.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..keys import CHANNEL_CODE

STATE_CREATING = "creating"


@dataclass
class KernelRecord:
    context_id: str
    language: str
    conversation_id: str
    created_ms: int
    channel: str = CHANNEL_CODE
    last_used_ms: int = -1  # last request on this kernel; defaults to created_ms
    reset_pending: bool = False  # rebuilt after an idle reap: next reply reports kernel_reset
    needs_init: bool = False  # fresh/restarted code kernel: chdir into the conversation dir first

    def __post_init__(self) -> None:
        if self.last_used_ms < 0:
            self.last_used_ms = self.created_ms


@dataclass
class InstanceRecord:
    digest: str
    tenant_id: str
    state: str
    created_ms: int
    last_active_ms: int
    instance_id: str | None = None
    paused_at_ms: int | None = None
    busy: int = 0  # in-flight requests holding the instance; never paused / evicted
    kernels: dict[str, KernelRecord] = field(default_factory=dict)
    # Contexts of closed conversations that could not be deleted yet (instance paused
    # or the call failed); they are deleted best-effort on the next acquire.
    stale_contexts: list[str] = field(default_factory=list)
    # Tokens of code kernels deleted for being idle; their next creation reports
    # ``kernel_reset`` because the variables are gone.
    reaped_tokens: set[str] = field(default_factory=set)


class Registry:
    def __init__(self) -> None:
        self._by_digest: dict[str, InstanceRecord] = {}
        self._tombstones: dict[str, int] = {}

    # -- instances ------------------------------------------------------------

    def get(self, digest: str) -> InstanceRecord | None:
        return self._by_digest.get(digest)

    def add(self, rec: InstanceRecord) -> None:
        if rec.digest in self._by_digest:
            raise RuntimeError(f"registry already holds {rec.digest}")
        self._by_digest[rec.digest] = rec

    def remove(self, rec: InstanceRecord) -> bool:
        """Remove ``rec`` only if it is still the registered record (identity check)."""

        if self._by_digest.get(rec.digest) is rec:
            del self._by_digest[rec.digest]
            return True
        return False

    def all(self) -> list[InstanceRecord]:
        return list(self._by_digest.values())

    def count(self) -> int:
        return len(self._by_digest)

    def count_tenant(self, tenant_id: str) -> int:
        return sum(1 for r in self._by_digest.values() if r.tenant_id == tenant_id)

    def counts_by_state(self) -> dict[str, int]:
        return dict(Counter(r.state for r in self._by_digest.values()))

    # -- tombstones -----------------------------------------------------------

    def set_tombstone(self, digest: str, now_ms: int) -> None:
        self._tombstones[digest] = now_ms

    def has_tombstone(self, digest: str) -> bool:
        return digest in self._tombstones

    def clear_tombstone(self, digest: str) -> None:
        self._tombstones.pop(digest, None)

    def prune_tombstones(self, older_than_ms: int) -> int:
        stale = [d for d, ts in self._tombstones.items() if ts < older_than_ms]
        for d in stale:
            del self._tombstones[d]
        return len(stale)
