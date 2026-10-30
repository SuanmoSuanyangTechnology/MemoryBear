"""A request-shaped view over one gRPC call.

The HTTP middleware and the shared header parsers only read ``headers``,
``method`` and ``url.path``. Wrapping invocation metadata in the same shape
keeps exactly one identity parser instead of a second gRPC-only variant.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from types import SimpleNamespace

from .metadata import TRACE_ID_METADATA_KEY


class MetadataHeaders(Mapping[str, str]):
    """Case-insensitive, first-value-wins metadata view (``-bin`` keys excluded)."""

    def __init__(self, metadata: Iterable[tuple[str, str | bytes]]) -> None:
        values: dict[str, str] = {}
        for key, value in metadata:
            lowered = key.lower()
            if lowered.endswith("-bin") or lowered in values:
                continue
            values[lowered] = value.decode() if isinstance(value, bytes) else value
        self._values = values

    def __getitem__(self, key: str) -> str:
        return self._values[key.lower()]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


class MetadataRequest:
    """The subset of the HTTP request surface that shared auth/attribution reads."""

    def __init__(
        self,
        metadata: Iterable[tuple[str, str | bytes]],
        *,
        method: str,
        path: str,
        trace_id: str | None = None,
    ) -> None:
        self.headers = MetadataHeaders(metadata)
        self.method = method
        self.url = SimpleNamespace(path=path)
        self.state = SimpleNamespace(
            trace_id=trace_id or self.headers.get(TRACE_ID_METADATA_KEY)
        )


__all__ = ["MetadataHeaders", "MetadataRequest"]
