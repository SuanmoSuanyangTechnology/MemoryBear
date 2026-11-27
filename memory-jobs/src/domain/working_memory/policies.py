"""Legacy one-day detail freshness rule."""

from datetime import UTC, datetime, timedelta

from src.domain.shared.clock import as_utc_aware


def stable(updated: datetime | None) -> bool:
    updated = as_utc_aware(updated)
    if updated is None:
        return False
    return datetime.now(UTC) - updated > timedelta(days=1)
