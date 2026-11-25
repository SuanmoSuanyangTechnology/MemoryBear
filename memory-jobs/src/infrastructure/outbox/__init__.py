"""Infrastructure adapters of the storage-outbox projection worker."""

from src.infrastructure.outbox.model import OUTBOX_EVENTS, OUTBOX_TABLE_NAME
from src.infrastructure.outbox.repository import PostgresOutboxStore

__all__ = ["OUTBOX_EVENTS", "OUTBOX_TABLE_NAME", "PostgresOutboxStore"]
