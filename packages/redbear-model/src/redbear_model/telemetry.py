"""Optional telemetry port for model runtime events."""

from __future__ import annotations

from typing import Protocol


class ModelTelemetry(Protocol):
    def report_failure(
        self,
        *,
        provider: str,
        model_name: str,
        operation: str,
        error_type: str,
        latency_ms: float,
    ) -> None: ...


class NoOpModelTelemetry:
    def report_failure(
        self,
        *,
        provider: str,
        model_name: str,
        operation: str,
        error_type: str,
        latency_ms: float,
    ) -> None:
        return None
