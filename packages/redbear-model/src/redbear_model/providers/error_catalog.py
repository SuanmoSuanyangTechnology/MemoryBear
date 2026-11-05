"""Provider error catalogs: upstream status/code -> platform BizCode."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from error_core.codes import BizCode
from error_core.diagnostics import redact
from error_core.supplier import ChannelFailure, ProviderFailure


@dataclass(frozen=True)
class Rule:
    status: int | None
    code_prefix: str | None
    biz_code: int
    retryable: bool


def _rules(*items: Rule) -> tuple[Rule, ...]:
    return items


_SPEEDBEAR = _rules(
    Rule(401, "AUTH_", 3009, False),
    Rule(402, "BILLING_API_KEY_", 3018, False),
    Rule(402, "BILLING_PROJECT_", 3018, False),
    Rule(402, "BILLING_TENANT_", 3018, False),
    Rule(402, "BILLING_CHANNEL_", 10003, False),
    Rule(402, "ENDPOINT_NO_CHANNEL_QUOTA", 10003, False),
    Rule(403, "AUTH_MODEL_NOT_AVAILABLE", 4003, False),
    Rule(403, "AUTH_MODEL_FORBIDDEN", 3001, False),
    Rule(403, "AUTH_MODEL_DISABLED", 3001, False),
    Rule(404, "ROUTE_", 4000, False),
    Rule(404, "TASK_", 4000, False),
    Rule(404, "RESPONSE_", 4000, False),
    Rule(404, "CONVERSATION_", 4000, False),
    Rule(413, "SAFETY_INPUT_TOO_LONG", 1003, False),
    Rule(422, "SAFETY_INPUT_TOO_LONG", 1003, False),
    Rule(422, "OMNI_", 1003, False),
    Rule(429, "RATE_LIMITED", 10004, True),
    Rule(451, "SAFETY_BLOCKED", 1010, False),
    Rule(500, "INTERNAL_ERROR", 10001, True),
    Rule(500, "UNKNOWN", 10001, True),
    Rule(502, "UPSTREAM_AUTH_FAILED", 3009, False),
    Rule(502, "UPSTREAM_ROUTE_FAILED", 7001, False),
    Rule(502, "UPSTREAM_CONNECT_FAILED", 10003, True),
    Rule(502, "BAD_GATEWAY", 10003, True),
    Rule(503, "UPSTREAM_QUOTA_EXHAUSTED", 10003, False),
    Rule(503, "GATEWAY_POOL_EXHAUSTED", 10003, True),
    Rule(503, "SHUTTING_DOWN", 10003, True),
    Rule(503, "NO_ENDPOINT_", 10003, True),
    Rule(504, "GATEWAY_TIMEOUT", 10003, True),
    Rule(504, "UPSTREAM_TIMEOUT", 10003, True),
    Rule(529, "GATEWAY_OVERLOADED", 10003, True),
)

_OPENAI = _rules(
    Rule(401, "invalid_api_key", 3009, False),
    Rule(402, "insufficient_quota", 3018, False),
    Rule(403, "model_not_found", 4003, False),
    Rule(404, "model_not_found", 4003, False),
    Rule(429, "rate_limit_exceeded", 10004, True),
    Rule(451, "content_filter", 1010, False),
    Rule(500, None, 10001, True),
    Rule(502, None, 10003, True),
    Rule(503, None, 10003, True),
    Rule(504, None, 10003, True),
)

_DASHSCOPE = _rules(
    Rule(400, "DataInspectionFailed", 1010, False),
    Rule(400, "InvalidParameter", 1003, False),
    Rule(401, "InvalidApiKey", 3009, False),
    Rule(403, "AccessDenied", 3001, False),
    Rule(404, "ModelNotFound", 4003, False),
    Rule(429, "Throttling", 10004, True),
    Rule(500, None, 10001, True),
    Rule(502, None, 10003, True),
    Rule(503, None, 10003, True),
    Rule(504, None, 10003, True),
)

_OLLAMA = _rules(
    Rule(400, "invalid", 1003, False),
    Rule(404, "not found", 4003, False),
    Rule(500, None, 10001, True),
    Rule(503, None, 10003, True),
)

_BEDROCK = _rules(
    Rule(400, "ValidationException", 1003, False),
    Rule(403, "AccessDeniedException", 3009, False),
    Rule(404, "ResourceNotFoundException", 4003, False),
    Rule(429, "ThrottlingException", 10004, True),
    Rule(500, "ModelNotReadyException", 10003, True),
    Rule(502, None, 10003, True),
    Rule(503, "ServiceUnavailableException", 10003, True),
    Rule(504, None, 10003, True),
)

_VOLCENGINE = _rules(
    Rule(400, "InvalidParameter", 1003, False),
    Rule(401, "AuthenticationError", 3009, False),
    Rule(403, "AccessDenied", 3001, False),
    Rule(404, "ModelNotFound", 4003, False),
    Rule(429, "RateLimitExceeded", 10004, True),
    Rule(500, "InternalServiceError", 10001, True),
    Rule(502, None, 10003, True),
    Rule(503, None, 10003, True),
    Rule(504, None, 10003, True),
)

_CATALOGS: dict[str, tuple[Rule, ...]] = {
    "speedbear": _SPEEDBEAR,
    "openai": _OPENAI,
    "dashscope": _DASHSCOPE,
    "ollama": _OLLAMA,
    "bedrock": _BEDROCK,
    "volcano": _VOLCENGINE,
}

# Shared 4xx fallback for every provider: some SDKs surface only an HTTP status
# (no machine-readable code), and per-provider code rules cannot match those.
# Provider prefix/status-only rules run first and keep priority; 5xx statuses
# without a provider rule keep the UPSTREAM_UNKNOWN behavior.
_SHARED_4XX = _rules(
    Rule(401, None, 3009, False),
    Rule(403, None, 3001, False),
    Rule(404, None, 4003, False),
    Rule(429, None, 10004, True),
)


def _shared_status_rule(status: int) -> Rule | None:
    for rule in _SHARED_4XX:
        if status == rule.status:
            return rule
    if 400 <= status < 500:
        return Rule(status, None, 1003, False)
    return None


def classify_provider_failure(
    provider: str,
    status: int | None,
    upstream_code: str | None,
    message: str,
    channel_id: UUID | None = None,
) -> ProviderFailure:
    """Exact code prefix first, then provider status-only rules, shared 4xx, unknown."""

    rules = _CATALOGS.get(provider.lower(), ())
    normalized = upstream_code or ""
    for rule in rules:
        if rule.code_prefix and rule.status is not None:
            if status == rule.status and normalized.startswith(rule.code_prefix):
                return _failure(provider, status, upstream_code, message, rule, channel_id)
    for rule in rules:
        if rule.code_prefix is None and status == rule.status:
            return _failure(provider, status, upstream_code, message, rule, channel_id)
    if status is not None:
        shared = _shared_status_rule(status)
        if shared is not None:
            return _failure(provider, status, upstream_code, message, shared, channel_id)
    return ProviderFailure(
        provider=provider,
        http_status=status,
        upstream_code=upstream_code,
        message=redact(message)[:2048],
        biz_code=int(BizCode.UPSTREAM_UNKNOWN),
        retryable=status is not None and status >= 500,
        channel_id=channel_id,
    )


def _failure(
    provider: str,
    status: int | None,
    upstream_code: str | None,
    message: str,
    rule: Rule,
    channel_id: UUID | None,
) -> ProviderFailure:
    return ProviderFailure(
        provider=provider,
        http_status=status,
        upstream_code=upstream_code,
        message=redact(message)[:2048],
        biz_code=rule.biz_code,
        retryable=rule.retryable,
        channel_id=channel_id,
    )


def to_channel_failure(failure: ProviderFailure, error_type: str) -> ChannelFailure:
    """Project a classified provider failure onto the per-channel aggregation row."""

    return ChannelFailure(
        channel_id=failure.channel_id,
        provider=failure.provider,
        error_type=error_type,
        biz_code=failure.biz_code,
        http_status=failure.http_status,
        upstream_code=failure.upstream_code,
        message=failure.message,
        retryable=failure.retryable,
    )


__all__ = ["Rule", "classify_provider_failure", "to_channel_failure"]
