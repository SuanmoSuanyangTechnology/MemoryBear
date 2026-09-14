"""Lifecycle-managed video understanding without task orchestration."""

from __future__ import annotations

import time

from redbear_model.contracts import ResolvedModelConfig
from redbear_model.media_contracts import (
    MediaCallOptions,
    VideoUnderstandingRequest,
    VideoUnderstandingResult,
)
from redbear_model.providers.dashscope_video_understanding import (
    DashScopeVideoUnderstandingAdapter,
    _isolated_config,
)
from redbear_model.runtime.client_pool import ModelClientPool
from redbear_model.telemetry import (
    ModelTelemetry,
    NoOpModelTelemetry,
    report_failure_safely,
)


class RedBearVideoUnderstanding:
    def __init__(
        self,
        config: ResolvedModelConfig,
        *,
        client_pool: ModelClientPool | None = None,
        telemetry: ModelTelemetry | None = None,
        options: MediaCallOptions | None = None,
    ):
        isolated = _isolated_config(config)
        self._config = isolated
        self._owned = client_pool is None
        self._pool = (
            client_pool
            if client_pool is not None
            else ModelClientPool(isolated.runtime)
        )
        self._telemetry = telemetry or NoOpModelTelemetry()
        self._adapter = DashScopeVideoUnderstandingAdapter(
            isolated, client_pool=self._pool, options=options
        )
        self._closed = False

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError("RedBearVideoUnderstanding is closed")

    def invoke(self, request: VideoUnderstandingRequest) -> VideoUnderstandingResult:
        self._ensure_open()
        started = time.perf_counter()
        try:
            return self._adapter.invoke(request)
        except Exception as exc:
            report_failure_safely(
                self._telemetry,
                self._config,
                operation="video.invoke",
                exc=exc,
                started_at=started,
            )
            raise

    async def ainvoke(
        self, request: VideoUnderstandingRequest
    ) -> VideoUnderstandingResult:
        self._ensure_open()
        started = time.perf_counter()
        try:
            return await self._adapter.ainvoke(request)
        except Exception as exc:
            report_failure_safely(
                self._telemetry,
                self._config,
                operation="video.ainvoke",
                exc=exc,
                started_at=started,
            )
            raise

    def close(self) -> None:
        if not self._closed:
            if self._owned:
                self._pool.close()
            self._closed = True

    async def aclose(self) -> None:
        if not self._closed:
            if self._owned:
                await self._pool.aclose()
            self._closed = True
