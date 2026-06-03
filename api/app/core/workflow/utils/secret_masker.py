from typing import Any, Iterable

MASKED_SECRET_VALUE = "__SECRET__"


def normalize_secret_values(secret_values: Iterable[str] | None) -> list[str]:
    """Normalize runtime secret values for deterministic masking."""
    if not secret_values:
        return []

    normalized = {
        str(value)
        for value in secret_values
        if value not in (None, "", MASKED_SECRET_VALUE)
    }
    return sorted(normalized, key=len, reverse=True)


def mask_secret_text(text: str, secret_values: Iterable[str] | None) -> str:
    """Replace secret substrings in text with a placeholder."""
    if not text:
        return text

    masked = text
    for secret in normalize_secret_values(secret_values):
        if secret in masked:
            masked = masked.replace(secret, MASKED_SECRET_VALUE)
    return masked


def mask_secrets(value: Any, secret_values: Iterable[str] | None) -> Any:
    """Recursively mask runtime secrets in common JSON-like structures."""
    secrets = normalize_secret_values(secret_values)
    if not secrets:
        return value

    return _mask_secrets(value, secrets)


def _mask_secrets(value: Any, secret_values: list[str]) -> Any:
    if isinstance(value, str):
        return mask_secret_text(value, secret_values)
    if isinstance(value, dict):
        return {key: _mask_secrets(item, secret_values) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask_secrets(item, secret_values) for item in value]
    if isinstance(value, tuple):
        return tuple(_mask_secrets(item, secret_values) for item in value)
    return value
