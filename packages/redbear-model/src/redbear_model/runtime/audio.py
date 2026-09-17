"""Audio transcription runtime; task scheduling belongs to the caller."""

from __future__ import annotations

import time

from redbear_model.contracts import ResolvedModelConfig
from redbear_model.errors import MediaProviderError, ModelSubmissionUncertainError
from redbear_model.media_contracts import (
    AudioTask,
    AudioTaskRef,
    AudioTranscriptionRequest,
    AudioTranscriptionResult,
    MediaCallOptions,
)
from redbear_model.providers.dashscope_asr import DashScopeASRAdapter
from redbear_model.telemetry import (
    ModelTelemetry,
    NoOpModelTelemetry,
    report_failure_safely,
)

from .client_pool import ModelClientPool


class RedBearAudioTranscriber:
    """Submit, inspect, or fetch one task. Never polls or resubmits implicitly."""

    def __init__(
        self,
        config: ResolvedModelConfig,
        *,
        client_pool: ModelClientPool | None = None,
        telemetry: ModelTelemetry | None = None,
        options: MediaCallOptions | None = None,
    ):
        self._config = config
        self._owns_pool = client_pool is None
        self._pool = (
            client_pool if client_pool is not None else ModelClientPool(config.runtime)
        )
        self._telemetry = telemetry or NoOpModelTelemetry()
        self._adapter = DashScopeASRAdapter(
            config, client_pool=self._pool, options=options or MediaCallOptions()
        )
        self._closed = False

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("Audio transcription runtime is closed")

    def _failure(self, operation: str, exc: Exception, started: float) -> None:
        # Do not retain a provider transport exception containing signed URLs.
        observed = (
            ConnectionError("Media transport failed")
            if (type(exc) is MediaProviderError and exc.status_code is None)
            or isinstance(exc, ModelSubmissionUncertainError)
            else exc
        )
        report_failure_safely(
            self._telemetry,
            self._config,
            operation=operation,
            exc=observed,
            started_at=started,
        )

    def _call(self, operation: str, method, value):
        self._check_open()
        started = time.perf_counter()
        try:
            return method(value)
        except Exception as exc:
            self._failure(operation, exc, started)
            raise

    async def _acall(self, operation: str, method, value):
        self._check_open()
        started = time.perf_counter()
        try:
            return await method(value)
        except Exception as exc:
            self._failure(operation, exc, started)
            raise

    def submit(self, request: AudioTranscriptionRequest) -> AudioTask:
        return self._call("asr.submit", self._adapter.submit, request)

    async def asubmit(self, request: AudioTranscriptionRequest) -> AudioTask:
        return await self._acall("asr.submit", self._adapter.asubmit, request)

    def get_task(self, ref: AudioTaskRef) -> AudioTask:
        return self._call("asr.get_task", self._adapter.get_task, ref)

    async def aget_task(self, ref: AudioTaskRef) -> AudioTask:
        return await self._acall("asr.get_task", self._adapter.aget_task, ref)

    def fetch_result(self, ref: AudioTaskRef) -> AudioTranscriptionResult:
        return self._call("asr.fetch_result", self._adapter.fetch_result, ref)

    async def afetch_result(self, ref: AudioTaskRef) -> AudioTranscriptionResult:
        return await self._acall("asr.fetch_result", self._adapter.afetch_result, ref)

    def close(self) -> None:
        if not self._closed:
            if self._owns_pool:
                self._pool.close()
            self._closed = True

    async def aclose(self) -> None:
        if not self._closed:
            if self._owns_pool:
                await self._pool.aclose()
            self._closed = True
