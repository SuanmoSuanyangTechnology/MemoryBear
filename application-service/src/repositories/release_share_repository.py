"""Release-share data access (frozen port of the host ``ReleaseShareRepository``).

Covers the management face (create, read by release, update, delete,
token-uniqueness probing) and the public share-view reads (token lookup, view
counting) with their async twins.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..models.release_share_model import ReleaseShare
from ..utils.datetime_utils import utcnow_naive


class ReleaseShareRepository:
    """Release share repository."""

    def __init__(self, db: Session | AsyncSession):
        self.db = db

    def create(self, share: ReleaseShare) -> ReleaseShare:
        """Create a share configuration."""

        self.db.add(share)
        self.db.commit()
        self.db.refresh(share)
        return share

    def get_by_release_id(self, release_id: UUID) -> ReleaseShare | None:
        """Fetch the share configuration of one release."""

        return self.db.query(ReleaseShare).filter(
            ReleaseShare.release_id == release_id
        ).first()

    def get_by_share_token(self, share_token: str) -> ReleaseShare | None:
        """Fetch the share configuration behind one public token."""

        stmt = select(ReleaseShare).where(ReleaseShare.share_token == share_token)
        return self.db.scalars(stmt).first()

    async def get_by_share_token_async(self, share_token: str) -> ReleaseShare | None:
        """Async twin of ``get_by_share_token`` for the runtime plane."""

        result = await self.db.execute(
            select(ReleaseShare).where(ReleaseShare.share_token == share_token)
        )
        return result.scalars().first()

    def update(self, share: ReleaseShare) -> ReleaseShare:
        """Persist changes to a share configuration."""

        self.db.commit()
        self.db.refresh(share)
        return share

    def delete(self, share: ReleaseShare) -> None:
        """Delete a share configuration."""

        self.db.delete(share)
        self.db.commit()

    def token_exists(self, share_token: str) -> bool:
        """Whether a share token is already taken."""

        return self.db.query(ReleaseShare.id).filter(
            ReleaseShare.share_token == share_token
        ).first() is not None

    def increment_view_count(self, share_id: UUID) -> None:
        """Atomically bump the view counter (avoids a SELECT+UPDATE race that
        could sit on the row lock until timeout)."""

        stmt = (
            update(ReleaseShare)
            .where(ReleaseShare.id == share_id)
            .values(
                view_count=ReleaseShare.view_count + 1,
                last_accessed_at=utcnow_naive(),
                updated_at=utcnow_naive(),
            )
        )
        self.db.execute(stmt)
        self.db.commit()

    async def increment_view_count_async(self, share_id: UUID) -> None:
        """Async twin of ``increment_view_count`` for the runtime plane."""

        stmt = (
            update(ReleaseShare)
            .where(ReleaseShare.id == share_id)
            .values(
                view_count=ReleaseShare.view_count + 1,
                last_accessed_at=utcnow_naive(),
                updated_at=utcnow_naive(),
            )
        )
        await self.db.execute(stmt)
        await self.db.commit()


__all__ = ["ReleaseShareRepository"]
