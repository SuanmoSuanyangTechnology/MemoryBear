"""Read-only repository for Platform-owned reference projections."""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.references import (
    ModelBase,
    ModelConfig,
    ModelProvider,
    ModelType,
    User,
    Workspace,
)


class ReferenceRepository:
    @staticmethod
    async def get_workspace(
        db: AsyncSession,
        workspace_id: uuid.UUID,
    ) -> Workspace | None:
        result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
        return result.scalars().first()

    @staticmethod
    async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalars().first()

    @staticmethod
    async def get_users(
        db: AsyncSession,
        user_ids: list[uuid.UUID],
    ) -> list[User]:
        if not user_ids:
            return []
        result = await db.execute(select(User).where(User.id.in_(user_ids)))
        return list(result.scalars().all())

    @staticmethod
    async def get_model_config(
        db: AsyncSession,
        model_config_id: uuid.UUID,
    ) -> ModelConfig | None:
        result = await db.execute(
            select(ModelConfig).where(ModelConfig.id == model_config_id)
        )
        return result.scalars().first()

    @staticmethod
    async def get_model_configs(
        db: AsyncSession,
        model_config_ids: list[uuid.UUID],
    ) -> list[ModelConfig]:
        if not model_config_ids:
            return []
        result = await db.execute(
            select(ModelConfig).where(ModelConfig.id.in_(model_config_ids))
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_model_base(
        db: AsyncSession,
        model_base_id: uuid.UUID,
    ) -> ModelBase | None:
        result = await db.execute(select(ModelBase).where(ModelBase.id == model_base_id))
        return result.scalars().first()

    @staticmethod
    async def get_model_bases(
        db: AsyncSession,
        model_base_ids: list[uuid.UUID],
    ) -> list[ModelBase]:
        if not model_base_ids:
            return []
        result = await db.execute(
            select(ModelBase).where(ModelBase.id.in_(model_base_ids))
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_latest_vision_model(
        db: AsyncSession,
        tenant_id: uuid.UUID,
    ) -> ModelConfig | None:
        result = await db.execute(
            select(ModelConfig)
            .where(
                or_(
                    ModelConfig.tenant_id == tenant_id,
                    (
                        (ModelConfig.provider == ModelProvider.SPEEDBEAR.value)
                        & ModelConfig.is_public.is_(True)
                    ),
                ),
                ModelConfig.type.in_([ModelType.CHAT.value, ModelType.LLM.value]),
                ModelConfig.capability.contains(["vision"]),
                ModelConfig.is_active.is_(True),
            )
            .order_by(ModelConfig.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()
