"""Skill package / file / plugin data access.

Ported from the monolith ``app/repositories/skill_package_repository.py``
(ADR-13 option A). Style follows this service's repository convention (instance
methods over the request session, like ``skill_repository.py``) instead of the
host's ``@staticmethod`` + explicit ``db`` argument.

Delete semantics are load-bearing: ``skills.package_id`` has no ON DELETE
handler, so the referencing rows must be detached *before* the package row is
removed, or Postgres raises a ForeignKeyViolation. Plugin deletion cascades over
its member packages through this same entry point.
"""

from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..models.skill_model import Skill
from ..models.skill_package_model import SkillFile, SkillPackage, SkillPlugin


class SkillPackageRepository:
    """Package, package-file and plugin access (one transaction, committed by the service)."""

    def __init__(self, db: Session):
        self.db = db

    # ---------------- package ----------------

    def create(self, **kwargs) -> SkillPackage:
        pkg = SkillPackage(**kwargs)
        self.db.add(pkg)
        self.db.flush()
        return pkg

    def get_by_id(self, package_id: uuid.UUID, tenant_id: Optional[uuid.UUID] = None) -> Optional[SkillPackage]:
        query = self.db.query(SkillPackage).filter(SkillPackage.id == package_id)
        if tenant_id:
            query = query.filter(SkillPackage.tenant_id == tenant_id)
        return query.first()

    def get_by_name(self, tenant_id: uuid.UUID, name: str) -> Optional[SkillPackage]:
        return self.db.query(SkillPackage).filter(
            SkillPackage.tenant_id == tenant_id,
            SkillPackage.name == name,
        ).first()

    def list_by_plugin(self, plugin_id: uuid.UUID, tenant_id: uuid.UUID) -> list[SkillPackage]:
        return self.db.query(SkillPackage).filter(
            SkillPackage.plugin_id == plugin_id,
            SkillPackage.tenant_id == tenant_id,
        ).order_by(SkillPackage.created_at).all()

    def delete(self, package_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
        """Delete a package (files cascade) after detaching ``skills.package_id``.

        Object storage cleanup is best-effort and handled by the service layer.
        """
        pkg = self.db.query(SkillPackage).filter(
            SkillPackage.id == package_id,
            SkillPackage.tenant_id == tenant_id,
        ).first()
        if not pkg:
            return False

        self.db.query(Skill).filter(Skill.package_id == package_id).update(
            {Skill.package_id: None}, synchronize_session=False
        )
        self.db.delete(pkg)
        self.db.flush()
        return True

    # ---------------- files ----------------

    def add_files(self, package_id: uuid.UUID, files: Sequence[dict]) -> None:
        """Bulk insert package files (dedup/idempotency is the service layer's job)."""
        for f in files:
            self.db.add(SkillFile(package_id=package_id, **f))
        self.db.flush()

    def get_files(self, package_id: uuid.UUID) -> list[SkillFile]:
        return self.db.query(SkillFile).filter(
            SkillFile.package_id == package_id
        ).order_by(SkillFile.rel_path).all()

    def get_file(self, package_id: uuid.UUID, rel_path: str) -> Optional[SkillFile]:
        return self.db.query(SkillFile).filter(
            SkillFile.package_id == package_id,
            SkillFile.rel_path == rel_path,
        ).first()

    def storage_refs(self, package_id: uuid.UUID) -> list[str]:
        """Object-storage keys owned by one package (for best-effort cleanup)."""
        rows = self.db.query(SkillFile.storage_ref).filter(
            SkillFile.package_id == package_id,
            SkillFile.storage_ref.isnot(None),
        ).all()
        return [r[0] for r in rows]

    # ---------------- plugin ----------------

    def create_plugin(self, **kwargs) -> SkillPlugin:
        plugin = SkillPlugin(**kwargs)
        self.db.add(plugin)
        self.db.flush()
        return plugin

    def get_plugin_by_id(
        self, plugin_id: uuid.UUID, tenant_id: Optional[uuid.UUID] = None
    ) -> Optional[SkillPlugin]:
        query = self.db.query(SkillPlugin).filter(SkillPlugin.id == plugin_id)
        if tenant_id:
            query = query.filter(SkillPlugin.tenant_id == tenant_id)
        return query.first()

    def get_plugin_by_name(self, tenant_id: uuid.UUID, name: str) -> Optional[SkillPlugin]:
        return self.db.query(SkillPlugin).filter(
            SkillPlugin.tenant_id == tenant_id,
            SkillPlugin.name == name,
        ).first()

    def list_plugins(self, tenant_id: uuid.UUID) -> list[SkillPlugin]:
        return self.db.query(SkillPlugin).filter(
            SkillPlugin.tenant_id == tenant_id
        ).order_by(SkillPlugin.created_at.desc()).all()

    def delete_plugin(self, plugin_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
        """Delete one plugin row (member packages must be removed first by the service)."""
        plugin = self.get_plugin_by_id(plugin_id, tenant_id)
        if not plugin:
            return False
        self.db.delete(plugin)
        self.db.flush()
        return True

    # ---------------- async reads (runtime paths) ----------------

    @staticmethod
    async def get_by_id_async(
        db: AsyncSession, package_id: uuid.UUID, tenant_id: Optional[uuid.UUID] = None
    ) -> Optional[SkillPackage]:
        stmt = select(SkillPackage).where(SkillPackage.id == package_id).limit(1)
        if tenant_id:
            stmt = stmt.where(SkillPackage.tenant_id == tenant_id)
        return (await db.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def get_files_async(db: AsyncSession, package_id: uuid.UUID) -> list[SkillFile]:
        stmt = select(SkillFile).where(
            SkillFile.package_id == package_id
        ).order_by(SkillFile.rel_path)
        return list((await db.execute(stmt)).scalars().all())

    @staticmethod
    async def get_file_async(
        db: AsyncSession, package_id: uuid.UUID, rel_path: str
    ) -> Optional[SkillFile]:
        stmt = select(SkillFile).where(
            SkillFile.package_id == package_id,
            SkillFile.rel_path == rel_path,
        ).limit(1)
        return (await db.execute(stmt)).scalar_one_or_none()