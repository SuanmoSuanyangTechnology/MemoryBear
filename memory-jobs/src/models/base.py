"""Metadata root for the tables this service reads.

``memory-jobs`` owns no database: every table it touches belongs to the API
migration chain (and later to the storage service), so there is no "owned" side
to keep apart and no migration target. This mirrors ``memory-profile``'s
``src/models`` layout, with the read-only/host-owned split collapsed into one
base because nothing here is owned.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Host-owned tables only: no DDL, no Alembic environment in this service."""
