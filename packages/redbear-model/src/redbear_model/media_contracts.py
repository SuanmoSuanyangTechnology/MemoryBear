"""Typed media calls, without storage, scheduling, or provider dependencies."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import ConfigDict, Field, StrictInt, field_validator, model_validator

from .contracts import ContractModel

NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
PositiveInt = Annotated[StrictInt, Field(gt=0)]


def validate_media_url(value: str) -> str:
    """Validate syntax without fetching the media or rewriting signed URLs."""
    try:
        url = urlsplit(value)
        valid = (
            url.scheme in {"https", "http"}
            and url.hostname
            and url.username is None
            and url.password is None
            and not url.fragment
            and not any(char.isspace() or ord(char) < 32 for char in value)
        )
        _ = url.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("A valid HTTP(S) URL without credentials is required")
    return value


class _MediaContract(ContractModel):
    model_config = ConfigDict(hide_input_in_errors=True)


class MediaCallOptions(_MediaContract):
    """Local response limits, not provider limits on the source media."""

    call_timeout_ms: PositiveInt = 600_000
    max_text_chars: PositiveInt = 1_000_000
    max_result_bytes: PositiveInt = 67_108_864


class MediaUsage(_MediaContract):
    """Provider-reported cumulative usage. None means unavailable, not zero."""

    input_tokens: NonNegativeInt | None = None
    output_tokens: NonNegativeInt | None = None
    total_tokens: NonNegativeInt | None = None
    audio_duration_ms: NonNegativeInt | None = None
    token_details: dict[str, NonNegativeInt] = Field(default_factory=dict)


class AudioTranscriptionRequest(_MediaContract):
    file_url: str = Field(repr=False)
    language: str | None = Field(default=None, min_length=1)
    channel_ids: tuple[NonNegativeInt, ...] = (0,)
    enable_itn: bool = False
    enable_words: bool = False

    _url = field_validator("file_url")(validate_media_url)

    @field_validator("channel_ids")
    @classmethod
    def normalize_channels(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value or len(set(value)) != len(value):
            raise ValueError("At least one distinct audio channel is required")
        return tuple(sorted(value))


class AudioTaskRef(_MediaContract):
    """Credential-free context binding; authorization remains with the host."""

    task_id: str = Field(
        min_length=1, max_length=512, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]*$"
    )
    tenant_id: UUID
    model_config_id: UUID
    channel_id: UUID | None = None
    key_id: UUID | None = None
    model_name: str = Field(min_length=1)
    api_base: str

    _url = field_validator("api_base")(validate_media_url)


class AudioTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class AudioTask(_MediaContract):
    ref: AudioTaskRef
    status: AudioTaskStatus
    provider_status: str
    provider_request_id: str | None = None
    provider_code: str | None = None
    error_summary: str | None = None
    usage: MediaUsage | None = None
    result_available: bool = False


class _TimedText(_MediaContract):
    """Times are relative milliseconds from the submitted media's start."""

    text: str = Field(strict=True, repr=False)
    start_ms: NonNegativeInt | None = None
    end_ms: NonNegativeInt | None = None

    @model_validator(mode="after")
    def validate_interval(self):
        if (self.start_ms is None) != (self.end_ms is None):
            raise ValueError("Media times must be provided together")
        if self.start_ms is not None and self.start_ms > self.end_ms:
            raise ValueError("Media start time must not exceed end time")
        return self


class TranscriptWord(_TimedText):
    punctuation: str | None = None


class TranscriptSentence(_TimedText):
    sentence_id: NonNegativeInt | None = None
    language: str | None = None
    words: tuple[TranscriptWord, ...] = ()

    @model_validator(mode="after")
    def validate_words(self):
        if self.start_ms is not None:
            for word in self.words:
                if word.start_ms is not None and (
                    word.start_ms < self.start_ms or word.end_ms > self.end_ms
                ):
                    raise ValueError("Word times must fall within the sentence")
        return self


class TranscriptTrack(_MediaContract):
    channel_id: NonNegativeInt
    text: str = Field(strict=True, repr=False)
    sentences: tuple[TranscriptSentence, ...] = ()


class AudioTranscriptionResult(_MediaContract):
    ref: AudioTaskRef
    tracks: tuple[TranscriptTrack, ...]
    text: str = Field(strict=True, repr=False)
    usage: MediaUsage = Field(default_factory=MediaUsage)
    provider_request_id: str | None = None


class VideoUnderstandingRequest(_MediaContract):
    video_url: str = Field(repr=False)
    prompt: str = Field(min_length=1, repr=False)
    max_output_tokens: PositiveInt | None = None

    _url = field_validator("video_url")(validate_media_url)

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A non-empty video prompt is required")
        return value


class VideoUnderstandingResult(_MediaContract):
    text: str = Field(min_length=1, repr=False)
    finish_reason: str
    provider_request_id: str | None = None
    usage: MediaUsage = Field(default_factory=MediaUsage)
    elapsed_ms: NonNegativeInt

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Video output must contain text")
        return value
