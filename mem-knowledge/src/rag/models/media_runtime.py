"""Adapters from RedBear media runtimes to the existing chunk-pipeline protocols."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from redbear_model import (
    AudioTaskStatus,
    AudioTranscriptionRequest,
    VideoUnderstandingRequest,
)

from ..chunk.prompts import video_transcription_prompt

if TYPE_CHECKING:
    from redbear_model.runtime import RedBearAudioTranscriber, RedBearVideoUnderstanding

ASR_POLL_INTERVAL_SECONDS = 1.0
ASR_POLL_TIMEOUT_SECONDS = 600.0


class AudioTranscriptionChunkModel:
    def __init__(
        self,
        transcriber: RedBearAudioTranscriber,
        file_url: str,
        *,
        poll_interval_seconds: float = ASR_POLL_INTERVAL_SECONDS,
        poll_timeout_seconds: float = ASR_POLL_TIMEOUT_SECONDS,
    ) -> None:
        self.transcriber = transcriber
        self.file_url = file_url
        self.poll_interval_seconds = poll_interval_seconds
        self.poll_timeout_seconds = poll_timeout_seconds

    def transcription(self, _audio_path: str) -> tuple[str, int]:
        task = self.transcriber.submit(AudioTranscriptionRequest(file_url=self.file_url))
        deadline = time.monotonic() + self.poll_timeout_seconds
        while task.status in {AudioTaskStatus.PENDING, AudioTaskStatus.RUNNING}:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("ASR task polling timed out")
            time.sleep(min(self.poll_interval_seconds, remaining))
            task = self.transcriber.get_task(task.ref)
        result = self.transcriber.fetch_result(task.ref)
        return result.text, result.usage.total_tokens or 0


class VideoUnderstandingChunkModel:
    def __init__(
        self,
        runtime: RedBearVideoUnderstanding,
        video_url: str,
        *,
        lang: str = "Chinese",
    ) -> None:
        self.runtime = runtime
        self.video_url = video_url
        self.lang = lang

    def chat(
        self,
        system: str,
        history: list,
        gen_conf: dict,
        *,
        video_bytes: bytes | None = None,
        filename: str = "",
        **kwargs: Any,
    ) -> tuple[str, int]:
        del system, history, gen_conf, video_bytes, filename, kwargs
        result = self.runtime.invoke(
            VideoUnderstandingRequest(
                video_url=self.video_url,
                prompt=video_transcription_prompt(self.lang),
            )
        )
        return result.text, result.usage.total_tokens or 0


__all__ = ["AudioTranscriptionChunkModel", "VideoUnderstandingChunkModel"]
