import uuid

from sqlalchemy import exists
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import EndUser


class EndUserRepository:
    @staticmethod
    async def enduser_in_workspace(
            db: AsyncSession,
            end_user_id: uuid.UUID,
            workspace_id: uuid.UUID
    ) -> bool:
        stmt = exists().where(
            EndUser.id == end_user_id,
            EndUser.workspace_id == workspace_id,
            EndUser.is_active.is_(True),
        ).select()
        return bool(await db.scalar(stmt))
