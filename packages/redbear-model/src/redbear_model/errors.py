"""Stable errors exposed by the RedBear model package."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID


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
