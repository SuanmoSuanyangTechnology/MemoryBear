"""Bound synchronous provider work without owning the caller's thread."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from queue import Empty, Queue
from threading import Thread
from typing import TypeVar, cast

_T = TypeVar("_T")


def run_with_timeout(
    operation: Callable[[], _T],
    timeout_s: float,
    timeout_error: Callable[[], BaseException],
    on_timeout: Callable[[], None] | None = None,
) -> _T:
    """Return at the total deadline even when a sync transport read is blocked.

    The daemon owns the transport operation and its resources. A timed-out caller
    never waits for executor shutdown; the provider's per-read timeout remains the
    upper bound for eventual daemon cleanup.
    """
    if timeout_s <= 0:
        raise timeout_error()

    outcome: Queue[tuple[bool, object]] = Queue(maxsize=1)

    def run() -> None:
        try:
            outcome.put((True, operation()))
        except BaseException as exc:  # noqa: BLE001 - preserve provider exception type
            outcome.put((False, exc))

    Thread(target=run, name="redbear-model-deadline", daemon=True).start()
    try:
        succeeded, value = outcome.get(timeout=timeout_s)
    except Empty:
        if on_timeout is not None:
            with suppress(Exception):
                on_timeout()
        raise timeout_error() from None
    if succeeded:
        return cast(_T, value)
    if isinstance(value, BaseException):
        raise value
    raise RuntimeError("deadline worker returned an invalid outcome")
