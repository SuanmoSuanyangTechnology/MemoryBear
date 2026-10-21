"""End-user info table read paths (frozen port of the monolith
``end_user_info_repository`` subset used by the migrated endpoints)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.end_user_info_model import EndUserInfo


class EndUserInfoRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_end_user_info(self, end_user_id: UUID) -> EndUserInfo | None:
        return self.db.execute(
            select(EndUserInfo).where(EndUserInfo.end_user_id == end_user_id).limit(1)
        ).scalars().first()


__all__ = ["EndUserInfoRepository"]
