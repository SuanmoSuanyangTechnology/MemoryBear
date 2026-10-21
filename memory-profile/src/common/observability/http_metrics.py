"""In-process HTTP request aggregates for the runtime monitor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_STATUS_CLASSES = ("1xx", "2xx", "3xx", "4xx", "5xx")


@dataclass(slots=True)
class RouteMetrics:
    requests: int = 0
    errors: int = 0
    total_duration_ns: int = 0
    max_duration_ns: int = 0


class HttpMetrics:
    """Per-process counters updated from one event loop, so no lock is needed."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.total_requests = 0
        self.status_classes = dict.fromkeys(_STATUS_CLASSES, 0)
        self.routes: dict[str, RouteMetrics] = {}

    def begin(self) -> None:
        self.in_flight += 1

    def finish(self, route: str, status_code: int | None, duration_ns: int) -> None:
        self.in_flight = max(0, self.in_flight - 1)
        self.total_requests += 1

        status = status_code if status_code is not None else 500
        status_class = f"{status // 100}xx"
        if status_class in self.status_classes:
            self.status_classes[status_class] += 1

        aggregate = self.routes.setdefault(route or "<unresolved>", RouteMetrics())
        aggregate.requests += 1
        if status >= 500:
            aggregate.errors += 1
        if duration_ns > 0:
            aggregate.total_duration_ns += duration_ns
            aggregate.max_duration_ns = max(aggregate.max_duration_ns, duration_ns)

    def snapshot(self) -> dict[str, Any]:
        routes = [
            {
                "route": route,
                "requests": aggregate.requests,
                "errors": aggregate.errors,
                "avg_ms": (
                    round(aggregate.total_duration_ns / aggregate.requests / 1_000_000, 3)
                    if aggregate.requests
                    else 0.0
                ),
                "max_ms": round(aggregate.max_duration_ns / 1_000_000, 3),
            }
            for route, aggregate in sorted(
                self.routes.items(), key=lambda item: (-item[1].requests, item[0])
            )
        ]
        return {
            "total_requests": self.total_requests,
            "in_flight": self.in_flight,
            "status_classes": dict(self.status_classes),
            "routes": routes,
        }
