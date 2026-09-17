"""Stable errors exposed by the RedBear model package."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from .media_contracts import AudioTaskRef, MediaUsage


def _status_code(value: object) -> int | None:
    status = getattr(value, "status_code", None)
    if status is None and isinstance(value, Mapping):
        status = value.get("status_code")
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def is_provider_rate_limit_error(exc: BaseException) -> bool:
    """Return whether an exception chain contains a provider HTTP 429."""

    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if _status_code(current) == 429 or _status_code(
            getattr(current, "response", None)
        ) == 429:
            return True
        for wrapped in (current.__cause__, current.__context__):
            if isinstance(wrapped, BaseException):
                pending.append(wrapped)
    return False


class RedBearModelError(Exception):
    """Base class for public model errors."""


class MediaProviderError(RedBearModelError):
    """Safe media failure context, never raw provider messages or URLs."""

    summary = "Media provider request failed"

    def __init__(
        self,
        operation: str,
        *,
        status_code: int | None = None,
        provider_code: str | None = None,
        provider_request_id: str | None = None,
        usage: MediaUsage | None = None,
        task_ref: AudioTaskRef | None = None,
    ):
        self.operation = operation
        self.status_code = status_code
        self.provider_code = provider_code
        self.provider_request_id = provider_request_id
        self.usage = usage
        self.task_ref = task_ref
        super().__init__(f"{self.summary}: {operation}")


class ModelSubmissionUncertainError(MediaProviderError):
    summary = "Media submission may have been accepted; do not automatically resubmit"


class ModelTaskNotReadyError(MediaProviderError):
    summary = "Media task is not ready"


class ModelTaskFailedError(MediaProviderError):
    summary = "Media task failed or is unknown"


class IncompleteModelOutputError(MediaProviderError):
    summary = "Media output did not finish successfully"


class EmptyModelOutputError(MediaProviderError):
    summary = "Video model returned empty text"


class MediaOutputLimitError(MediaProviderError):
    summary = "Media output exceeds the local response limit"


class MediaCallTimeoutError(MediaProviderError):
    summary = "Media call exceeded its timeout"


class InvalidProviderResponseError(RedBearModelError):
    def __init__(self, operation: str, reason: str):
        super().__init__(f"Invalid {operation} provider response: {reason}")


class MultimodalInputLimitError(RedBearModelError):
    def __init__(self, operation: str):
        super().__init__(f"The {operation} input exceeds the provider limit")


class UnsupportedMultimodalModelError(RedBearModelError):
    def __init__(self, operation: str):
        super().__init__(f"The configured model does not support {operation}")


class ModelConfigNotFoundError(RedBearModelError):
    def __init__(self, model_config_id: UUID):
        super().__init__(f"Model config was not found: {model_config_id}")


class ModelConfigInactiveError(RedBearModelError):
    def __init__(self, model_config_id: UUID):
        super().__init__(f"Model config is inactive: {model_config_id}")


class ModelAccessDeniedError(RedBearModelError):
    def __init__(self, model_config_id: UUID, tenant_id: UUID):
        super().__init__(
            f"Tenant {tenant_id} cannot access model config {model_config_id}"
        )


class ModelCredentialNotFoundError(RedBearModelError):
    def __init__(self, model_config_id: UUID):
        super().__init__(f"No active credential exists for model config {model_config_id}")


class PublicCredentialUnavailableError(RedBearModelError):
    def __init__(self, model_config_id: UUID, tenant_id: UUID):
        super().__init__(
            f"Public model credential is unavailable for model config "
            f"{model_config_id} and tenant {tenant_id}"
        )


class NoAvailableChannelError(RedBearModelError):
    def __init__(
        self,
        model_config_id: UUID,
        provider: str,
        model_name: str | None = None,
        reason: str | None = None,
    ):
        suffix = "" if reason is None else f": {reason}"
        super().__init__(
            f"No available channel for model config {model_config_id} "
            f"(provider {provider}, model {model_name}){suffix}"
        )


class SpeedbearChannelMissingError(NoAvailableChannelError):
    """租户无 provider=speedbear && source=platform 渠道（替代旧 PublicCredentialUnavailableError 文案面）。"""

    def __init__(self, model_config_id: UUID, tenant_id: UUID):
        super().__init__(
            model_config_id,
            "speedbear",
            None,
            f"tenant {tenant_id} has no platform speedbear channel",
        )


class CredentialDecryptError(RedBearModelError):
    def __init__(self, channel_id: UUID, provider: str, cause: Exception):
        self.__cause__ = cause
        super().__init__(
            f"Failed to decrypt credential for channel {channel_id} "
            f"(provider {provider})"
        )


class ChannelSwitchExhaustedError(RedBearModelError):
    """候选渠道全部耗尽（spec §11.2 候选耗尽行）：聚合报错——尝试过的渠道链 + 原始错误链。

    failures = [(channel_id | None, error_type), ...]，仅渠道 id 与错误类型名，
    不含任何凭据明文/密文；原始最后错误经 __cause__ 保留堆栈。
    """

    def __init__(
        self,
        model_config_id: UUID,
        provider: str,
        model_name: str,
        failures: list[tuple[UUID | None, str]],
        cause: BaseException | None,
    ):
        self.failures = failures
        if cause is not None:
            self.__cause__ = cause
        chain = ", ".join(
            f"channel={channel_id}:{error_type}" for channel_id, error_type in failures
        )
        super().__init__(
            f"All candidate channels failed for model config {model_config_id} "
            f"(provider {provider}, model {model_name})"
            + ("" if not chain else f": {chain}")
        )


class UnsupportedModelProviderError(RedBearModelError):
    def __init__(self, provider: str):
        super().__init__(f"Unsupported model provider: {provider}")


class ModelUsageRecordError(RedBearModelError):
    def __init__(self, key_id: UUID, cause: Exception):
        self.__cause__ = cause
        super().__init__(f"Failed to record usage for model key {key_id}")


class ProviderDependencyMissingError(RedBearModelError):
    def __init__(self, provider: str, extra: str):
        super().__init__(
            f"Provider '{provider}' requires optional dependency extra "
            f"'redbear-model[{extra}]'"
        )
