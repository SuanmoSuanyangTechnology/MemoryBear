"""Transaction-scoped advisory locks for serializing cross-table writes."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


def acquire_advisory_xact_lock(db: Session, key: str) -> None:
    """Serialize one logical identity until the current transaction ends.

    Used where no single unique constraint can express the identity (for
    example a market MCP identity split across two tables), so concurrent
    writers cannot both pass the duplicate probe. Released at commit/rollback.
    """

    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": key},
    )
