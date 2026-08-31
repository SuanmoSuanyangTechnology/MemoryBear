"""Separate metadata for read-only Platform table projections."""

from sqlalchemy.orm import DeclarativeBase


class ReferenceBase(DeclarativeBase):
    """Base for Platform tables that Knowledge may only read."""
