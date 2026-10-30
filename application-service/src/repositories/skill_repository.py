"""Skill table access (frozen port of the monolith ``skill_repository``;
the host static methods become instance repositories, matching the port's
repository convention)."""

from __future__ import annotations

import uuid

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..models.skill_model import Skill
from ..schemas.skill_schema import SkillCreate, SkillUpdate


class SkillRepository:
    """Skill data access layer."""

    def __init__(self, db: Session | AsyncSession):
        self.db = db

    def create(self, data: SkillCreate, tenant_id: uuid.UUID) -> Skill:
        """Create a skill."""
        skill = Skill(
            **data.model_dump(),
            tenant_id=tenant_id
        )
        self.db.add(skill)
        self.db.flush()
        return skill

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

    async def get_by_id_async(
        self, skill_id: uuid.UUID, tenant_id: uuid.UUID | None = None
    ) -> Skill | None:
        """Get a skill by ID."""
        stmt = select(Skill).where(Skill.id == skill_id)
        if tenant_id:
            stmt = stmt.where(
                or_(
                    Skill.tenant_id == tenant_id,
                    Skill.is_public.is_(True)
                )
            )
        return (await self.db.execute(stmt.limit(1))).scalar_one_or_none()

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

    async def list_skills_async(
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
            filters.append(Skill.name.ilike(f"%{search}%"))

        if is_active is not None:
            filters.append(Skill.is_active == is_active)

        if is_public is not None:
            filters.append(Skill.is_public == is_public)

        where_clause = and_(*filters)
        total = (
            await self.db.execute(
                select(func.count()).select_from(Skill).where(where_clause)
            )
        ).scalar_one()

        result = await self.db.execute(
            select(Skill)
            .where(where_clause)
            .order_by(Skill.created_at.desc())
            .offset((page - 1) * pagesize)
            .limit(pagesize)
        )

        return list(result.scalars().all()), total

    def update(self, skill_id: uuid.UUID, data: SkillUpdate, tenant_id: uuid.UUID) -> Skill | None:
        """Update a skill."""
        skill = self.db.query(Skill).filter(
            Skill.id == skill_id,
            Skill.tenant_id == tenant_id
        ).first()

        if not skill:
            return None

        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(skill, key, value)

        self.db.flush()
        return skill

    def delete(self, skill_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
        """Delete a skill."""
        skill = self.db.query(Skill).filter(
            Skill.id == skill_id,
            Skill.tenant_id == tenant_id
        ).first()

        if not skill:
            return False

        # db.delete(skill)
        skill.is_active = False
        self.db.flush()
        return True
