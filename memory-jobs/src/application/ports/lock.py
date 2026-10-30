"""Port for the per-user in-flight lock.

The token identifies *one attempt*, not one execution: a retried attempt that
reuses its token re-enters the lock it already holds, while any other token is
refused for as long as the key lives. The port knows nothing about the transport
that produced the token.
"""

from __future__ import annotations

from typing import Protocol


class InflightLock(Protocol):
    def acquire(self, end_user_id: str, token: str) -> bool: ...

    def release(self, end_user_id: str, token: str) -> None: ...

    def is_locked(self, end_user_id: str) -> bool: ...
