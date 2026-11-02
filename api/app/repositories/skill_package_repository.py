"""Skill Package Repository"""
import uuid
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill_package_model import SkillPackage, SkillFile
from app.models.skill_model import Skill


class SkillPackageRepository:
    """技能包数据访问层（包与文件同事务写入，由 service 层统一提交）"""

    # ---------------- package ----------------

    @staticmethod
    def create(db: Session, **kwargs) -> SkillPackage:
        pkg = SkillPackage(**kwargs)
        db.add(pkg)
        db.flush()
        return pkg

    @staticmethod
    def get_by_id(db: Session, package_id: uuid.UUID, tenant_id: Optional[uuid.UUID] = None) -> Optional[SkillPackage]:
        query = db.query(SkillPackage).filter(SkillPackage.id == package_id)
        if tenant_id:
            query = query.filter(SkillPackage.tenant_id == tenant_id)
        return query.first()

    @staticmethod
    async def get_by_id_async(db: AsyncSession, package_id: uuid.UUID, tenant_id: Optional[uuid.UUID] = None) -> Optional[SkillPackage]:
        stmt = select(SkillPackage).where(SkillPackage.id == package_id).limit(1)
        if tenant_id:
            stmt = stmt.where(SkillPackage.tenant_id == tenant_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    def get_by_name(db: Session, tenant_id: uuid.UUID, name: str) -> Optional[SkillPackage]:
        return db.query(SkillPackage).filter(
            SkillPackage.tenant_id == tenant_id,
            SkillPackage.name == name,
        ).first()

    @staticmethod
    def delete(db: Session, package_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
        """删除包（skill_files 级联删除），并清理对象存储中的大文件/二进制。

        ⚠️ skills.package_id 外键没有 ON DELETE SET NULL/ CASCADE，
        删包前必须先把引用该包的 skills 行指针断开，否则 ForeignKeyViolation。
        调用方随后会写入新 package_id（同事务），期间指针短暂为 NULL 是安全的。
        """
        from app.services.skill_package_service import SkillPackageService

        pkg = db.query(SkillPackage).filter(
            SkillPackage.id == package_id,
            SkillPackage.tenant_id == tenant_id,
        ).first()
        if not pkg:
            return False
        # 删对象存储（best-effort：清理失败不阻断 DB 删除，残留对象由运维兜底）
        SkillPackageService._delete_storage_objects(db, package_id)

        db.query(Skill).filter(Skill.package_id == package_id).update(
            {Skill.package_id: None}, synchronize_session=False
        )
        db.delete(pkg)
        db.flush()
        return True

    # ---------------- files ----------------

    @staticmethod
    def add_files(db: Session, package_id: uuid.UUID, files: Sequence[dict]) -> None:
        """批量写入包内文件行（不做 content_hash 判重，由 service 层保证幂等）"""
        for f in files:
            db.add(SkillFile(package_id=package_id, **f))
        db.flush()

    @staticmethod
    def get_files(db: Session, package_id: uuid.UUID) -> list[SkillFile]:
        return db.query(SkillFile).filter(SkillFile.package_id == package_id).order_by(SkillFile.rel_path).all()

    @staticmethod
    async def get_files_async(db: AsyncSession, package_id: uuid.UUID) -> list[SkillFile]:
        stmt = select(SkillFile).where(SkillFile.package_id == package_id).order_by(SkillFile.rel_path)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    def get_file(db: Session, package_id: uuid.UUID, rel_path: str) -> Optional[SkillFile]:
        return db.query(SkillFile).filter(
            SkillFile.package_id == package_id,
            SkillFile.rel_path == rel_path,
        ).first()

    @staticmethod
    async def get_file_async(db: AsyncSession, package_id: uuid.UUID, rel_path: str) -> Optional[SkillFile]:
        stmt = select(SkillFile).where(
            SkillFile.package_id == package_id,
            SkillFile.rel_path == rel_path,
        ).limit(1)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()
