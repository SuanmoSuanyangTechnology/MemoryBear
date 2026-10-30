"""Skill table access (port of the monolith ``skill_repository`` management-plane reads).

The write paths (``create`` / ``update`` / ``delete``) are gone: the import pipeline owns
skill-row creation, and deletion (soft-delete the row + purge its pack) lives in
``SkillPackageService`` where the plugin cascade can share one transaction (ADR-9).
"""

from __future__ import annotations

import uuid

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..models.skill_model import Skill


class SkillRepository:
    """Skill data access layer."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, skill_id: uuid.UUID, tenant_id: uuid.UUID | None = None) -> Skill | None:
        """Get a skill by ID."""
        query = self.db.query(Skill).filter(Skill.id == skill_id)
        if tenant_id:
            query = query.filter(
                or_(
                    Skill.tenant_id == tenant_id,
                    Skill.is_public.is_(True)
                )
            )
        return query.first()

    def list_skills(
        self,
        tenant_id: uuid.UUID,
        search: str | None = None,
        is_active: bool | None = None,
        is_public: bool | None = None,
        page: int = 1,
        pagesize: int = 10
    ) -> tuple[list[type[Skill]], int]:
        """List skills."""
        filters = [
            or_(
                Skill.tenant_id == tenant_id,
                Skill.is_public.is_(True)
            )
        ]

        if search:
            filters.append(
                or_(
                    Skill.name.ilike(f"%{search}%"),
                    # Skill.description.ilike(f"%{search}%")
                )
            )

        if is_active is not None:
            filters.append(Skill.is_active == is_active)

        if is_public is not None:
            filters.append(Skill.is_public == is_public)

        query = self.db.query(Skill).filter(and_(*filters))
        total = query.count()

        skills = query.order_by(Skill.created_at.desc()).offset(
            (page - 1) * pagesize
        ).limit(pagesize).all()

        return skills, total

    @staticmethod
    async def list_skills_async(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        search: str | None = None,
        is_active: bool | None = None,
        is_public: bool | None = None,
        page: int = 1,
        pagesize: int = 10,
    ) -> tuple[list[Skill], int]:
        """Async list (runtime plane: the middleware loads skills inside the event loop)."""

        filters = [or_(Skill.tenant_id == tenant_id, Skill.is_public.is_(True))]
        if search:
            filters.append(Skill.name.ilike(f"%{search}%"))
        if is_active is not None:
            filters.append(Skill.is_active == is_active)
        if is_public is not None:
            filters.append(Skill.is_public == is_public)

        where_clause = and_(*filters)
        total = (
            await db.execute(select(func.count()).select_from(Skill).where(where_clause))
        ).scalar_one()
        result = await db.execute(
            select(Skill)
            .where(where_clause)
            .order_by(Skill.created_at.desc())
            .offset((page - 1) * pagesize)
            .limit(pagesize)
        )
        return list(result.scalars().all()), total

    @staticmethod
    async def get_by_id_async(
        db: AsyncSession, skill_id: uuid.UUID, tenant_id: uuid.UUID | None = None
    ) -> Skill | None:
        """Async single-row read (runtime plane)."""

        stmt = select(Skill).where(Skill.id == skill_id).limit(1)
        if tenant_id:
            stmt = stmt.where(or_(Skill.tenant_id == tenant_id, Skill.is_public.is_(True)))
        return (await db.execute(stmt)).scalar_one_or_none()