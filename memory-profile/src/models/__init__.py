"""SQLAlchemy metadata roots used by the profile service."""

from src.models.base import ProfileBase, ReadOnlyBase

__all__ = ["ProfileBase", "ReadOnlyBase"]
