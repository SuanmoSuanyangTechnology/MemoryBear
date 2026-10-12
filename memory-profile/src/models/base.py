"""Independent SQLAlchemy metadata roots for profile-owned and host-owned tables."""

from sqlalchemy.orm import DeclarativeBase


class ProfileBase(DeclarativeBase):
    """Migration metadata for tables whose DDL is owned by memory-profile."""

    pass


class ReadOnlyBase(DeclarativeBase):
    """Separate metadata for tables whose DDL is owned by another service."""

    pass
