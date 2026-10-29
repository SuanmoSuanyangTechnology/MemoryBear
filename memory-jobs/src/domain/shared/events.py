"""Domain event base.

Aggregates collect events while they change; the application layer publishes
them after the transaction commits. No dispatcher or transport knowledge lives
here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """Something that happened in the domain at a known time."""

    occurred_at: datetime
