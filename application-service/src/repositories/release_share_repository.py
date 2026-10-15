"""Release-share data access (frozen port of the host ``ReleaseShareRepository``).

Only the paths consumed by the management face are ported: create, read by
release, update, delete and token-uniqueness probing. The view counter and the
async twins belong to the public share-view face (runtime plane).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from ..models.release_share_model import ReleaseShare


class ReleaseShareRepository:
    """Release share repository."""

    def __init__(self, db: Session):
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


__all__ = ["ReleaseShareRepository"]
