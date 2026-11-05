"""Provider/channel failure DTOs and the exhaustion aggregation policy."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from .codes import BizCode


@dataclass(frozen=True)
class ProviderFailure:
    provider: str
    http_status: int | None
    upstream_code: str | None
    message: str
    biz_code: int
    retryable: bool
    channel_id: UUID | None = None


@dataclass(frozen=True)
class ChannelFailure:
    channel_id: UUID | None
    provider: str
    error_type: str
    biz_code: int | None
    http_status: int | None
    upstream_code: str | None
    message: str
    retryable: bool


@dataclass(frozen=True)
class AggregatedFailure:
    biz_code: int
    retryable: bool


_TRANSIENT_CODES = {
    int(BizCode.RATE_LIMITED),
    int(BizCode.SERVICE_UNAVAILABLE),
    int(BizCode.UPSTREAM_UNKNOWN),
}


def aggregate_channel_failures(
    failures: list[ChannelFailure],
) -> AggregatedFailure | None:
    """Choose the public code for a multi-channel exhaustion.

    1. one failure -> its code;
    2. all codes identical -> that code;
    3. all transient -> 429 if any rate-limited, else 503;
    4. otherwise -> ``NO_AVAILABLE_CHANNEL``.
    """

    if not failures:
        return None
    codes = [failure.biz_code for failure in failures if failure.biz_code is not None]
    if len(failures) == 1:
        failure = failures[0]
        if failure.biz_code is None:
            return AggregatedFailure(biz_code=int(BizCode.NO_AVAILABLE_CHANNEL), retryable=False)
        return AggregatedFailure(biz_code=failure.biz_code, retryable=failure.retryable)
    if codes and len(codes) == len(failures) and len(set(codes)) == 1:
        return AggregatedFailure(biz_code=codes[0], retryable=all(f.retryable for f in failures))
    if all(failure.retryable and failure.biz_code in _TRANSIENT_CODES for failure in failures):
        code = (
            BizCode.RATE_LIMITED
            if any(failure.biz_code == int(BizCode.RATE_LIMITED) for failure in failures)
            else BizCode.SERVICE_UNAVAILABLE
        )
        return AggregatedFailure(biz_code=int(code), retryable=True)
    return AggregatedFailure(biz_code=int(BizCode.NO_AVAILABLE_CHANNEL), retryable=False)


__all__ = [
    "AggregatedFailure",
    "ChannelFailure",
    "ProviderFailure",
    "aggregate_channel_failures",
]
