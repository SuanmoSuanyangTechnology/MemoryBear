"""Skill definition-plane service (frozen port of the monolith
``SkillService`` create/get/list/update/delete paths).

Split adaptation: the host's static methods become instance methods over the
request session, and tool enrichment goes through the port ``ToolService``.
The execution-plane helpers (``load_skill_tools*``) stay in the host — the
monolith remains the only runtime consumer.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..errors import BizCode, BusinessException
from ..models.skill_model import Skill
from ..repositories.skill_repository import SkillRepository
from ..schemas.skill_schema import SkillCreate, SkillUpdate
from .tool_service import ToolService


class SkillService:
    """Skill business logic layer."""

    def __init__(self, db: Session):
        self.db = db
        self.repository = SkillRepository(db)

    def create_skill(self, data: SkillCreate, tenant_id: uuid.UUID) -> Skill:
        """Create a skill."""
        # Check for an existing skill with the same name
        existing = self.db.query(Skill).filter(
            Skill.tenant_id == tenant_id,
            Skill.name == data.name
        ).first()
        if existing:
            raise BusinessException(f"技能名称'{data.name}'已存在", BizCode.DUPLICATE_NAME)

        skill = self.repository.create(data, tenant_id)
        self.db.commit()
        self.db.refresh(skill)
        return skill

    def get_skill(self, skill_id: uuid.UUID, tenant_id: uuid.UUID) -> Skill:
        """Get a skill."""
        try:
            skill = self.repository.get_by_id(skill_id, tenant_id)
            if not skill:
                raise BusinessException(f"技能{skill_id}不存在", BizCode.NOT_FOUND)

            # Enrich with tool details
            tool_service = ToolService(self.db)
            enriched_tools = []
            for tool_config in skill.tools:
                tool_id = tool_config.get("tool_id")
                if tool_id:
                    tool_info = tool_service.get_tool_summary(tool_id, tenant_id)
                    if tool_info:
                        enriched_tool = {
                            "tool_id": tool_id,
                            "tool_info": tool_info
                        }
                        if "operation" in tool_config:
                            enriched_tool["operation"] = tool_config["operation"]
                        enriched_tools.append(enriched_tool)
            skill.tools = enriched_tools

            return skill
        except (BusinessException, SQLAlchemyError) as e:
            self.db.rollback()
            raise e

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
        return self.repository.list_skills(
            tenant_id, search, is_active, is_public, page, pagesize
        )

    def update_skill(self, skill_id: uuid.UUID, data: SkillUpdate, tenant_id: uuid.UUID) -> Skill:
        """Update a skill."""
        try:
            skill = self.repository.update(skill_id, data, tenant_id)
            if not skill:
                raise BusinessException(f"技能{skill_id}不存在或无权限", BizCode.NOT_FOUND)
            self.db.commit()
            self.db.refresh(skill)
            return skill
        except (BusinessException, SQLAlchemyError) as e:
            self.db.rollback()
            raise e

    def delete_skill(self, skill_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
        """Delete a skill."""
        try:
            success = self.repository.delete(skill_id, tenant_id)
            if not success:
                raise BusinessException("技能不存在或无权限", BizCode.NOT_FOUND)
            self.db.commit()
            return True
        except (BusinessException, SQLAlchemyError) as e:
            self.db.rollback()
            raise e
