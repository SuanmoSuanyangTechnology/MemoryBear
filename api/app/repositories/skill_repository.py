"""Skill Repository"""
from typing import List, Optional, Tuple, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, select, func
from sqlalchemy.ext.asyncio import AsyncSession
import uuid

from app.models.skill_model import Skill
from app.schemas.skill_schema import SkillCreate, SkillUpdate


class SkillRepository:
    """Skill 数据访问层"""

    @staticmethod
    def create(db: Session, data: SkillCreate, tenant_id: uuid.UUID) -> Skill:
        """创建技能"""
        skill = Skill(
            **data.model_dump(),
            tenant_id=tenant_id
        )
        db.add(skill)
        db.flush()
        return skill

    @staticmethod
    def get_by_id(db: Session, skill_id: uuid.UUID, tenant_id: Optional[uuid.UUID] = None) -> Optional[Skill]:
        """根据ID获取技能"""
        query = db.query(Skill).filter(Skill.id == skill_id)
        if tenant_id:
            query = query.filter(
                or_(
                    Skill.tenant_id == tenant_id,
                    Skill.is_public == True
                )
            )
        return query.first()

    @staticmethod
    async def get_by_id_async(
        db: AsyncSession,
        skill_id: uuid.UUID,
        tenant_id: Optional[uuid.UUID] = None,
    ) -> Optional[Skill]:
        """根据ID异步获取技能"""
        stmt = select(Skill).where(Skill.id == skill_id)
        if tenant_id:
            stmt = stmt.where(
                or_(
                    Skill.tenant_id == tenant_id,
                    Skill.is_public == True
                )
            )
        result = await db.execute(stmt.limit(1))
        return result.scalar_one_or_none()

    @staticmethod
    def list_skills(
        db: Session,
        tenant_id: uuid.UUID,
        search: Optional[str] = None,
        is_active: Optional[bool] = None,
        is_public: Optional[bool] = None,
        page: int = 1,
        pagesize: int = 10
    ) -> tuple[list[type[Skill]], int]:
        """列出技能"""
        filters = [
            or_(
                Skill.tenant_id == tenant_id,
                Skill.is_public == True
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

        query = db.query(Skill).filter(and_(*filters))
        total = query.count()

        skills = query.order_by(Skill.created_at.desc()).offset(
            (page - 1) * pagesize
        ).limit(pagesize).all()

        return skills, total

    @staticmethod
    async def list_skills_async(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        search: Optional[str] = None,
        is_active: Optional[bool] = None,
        is_public: Optional[bool] = None,
        page: int = 1,
        pagesize: int = 10
    ) -> tuple[list[type[Skill]], int]:
        """异步列出技能"""
        filters = [
            or_(
                Skill.tenant_id == tenant_id,
                Skill.is_public == True
            )
        ]

        if search:
            filters.append(
                or_(
                    Skill.name.ilike(f"%{search}%"),
                )
            )

        if is_active is not None:
            filters.append(Skill.is_active == is_active)

        if is_public is not None:
            filters.append(Skill.is_public == is_public)

        where_clause = and_(*filters)
        total_result = await db.execute(
            select(func.count()).select_from(Skill).where(where_clause)
        )
        total = total_result.scalar_one()

        result = await db.execute(
            select(Skill)
            .where(where_clause)
            .order_by(Skill.created_at.desc())
            .offset((page - 1) * pagesize)
            .limit(pagesize)
        )
        skills = list(result.scalars().all())

        return skills, total

    @staticmethod
    def update(db: Session, skill_id: uuid.UUID, data: SkillUpdate, tenant_id: uuid.UUID) -> Optional[Skill]:
        """更新技能"""
        skill = db.query(Skill).filter(
            Skill.id == skill_id,
            Skill.tenant_id == tenant_id
        ).first()

        if not skill:
            return None

        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(skill, key, value)

        db.flush()
        return skill

    @staticmethod
    def delete(db: Session, skill_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
        """删除技能"""
        skill = db.query(Skill).filter(
            Skill.id == skill_id,
            Skill.tenant_id == tenant_id
        ).first()

        if not skill:
            return False

        # db.delete(skill)
        skill.is_active = False
        db.flush()
        return True
