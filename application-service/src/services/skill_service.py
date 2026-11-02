"""Skill definition-plane service (port of the monolith ``SkillService`` read paths).

Split adaptation: the host's static methods become instance methods over the
request session, and tool enrichment goes through the port ``ToolService``.

The write paths (``create_skill`` / ``update_skill``) are gone with their
endpoints: the standard-pack import pipeline is the only write entry point
(design doc ADR-9). ``delete_skill`` was also removed — deletion now lives in
``SkillPackageService.delete_entry`` because it must also purge the pack and
handle the plugin cascade in one transaction.

``load_skill_tools(_async)`` lives here too: the runtime plane consumes it and it
must skip platform-tool loading for ``skill_format='standard'`` rows (§8.4).
"""

from __future__ import annotations

import logging
import uuid
from typing import List

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..errors import BizCode, BusinessException
from ..models.skill_model import Skill
from ..repositories.skill_repository import SkillRepository
from .tool_service import ToolService

logger = logging.getLogger(__name__)


class SkillService:
    """Skill business logic layer."""

    def __init__(self, db: Session):
        self.db = db
        self.repository = SkillRepository(db)

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

    @staticmethod
    def load_skill_tools(
        db: Session,
        skill_ids: List[str],
        tenant_id: uuid.UUID,
        runtime_context: dict | None = None,
    ) -> tuple[List, dict[str, str]]:
        """Load the platform tools bound by legacy skills.

        Returns ``(tools, tool-to-skill map)``. Standard skills are skipped outright
        (design doc §8.4): the protocol has no platform-tool binding, so a dirty row
        still carrying ``tools`` must not leak into keyword gating.
        """

        tools: List = []
        tool_to_skill_map: dict[str, str] = {}
        tool_service = ToolService(db)
        repo = SkillRepository(db)

        for skill_id in skill_ids:
            try:
                skill = repo.get_by_id(uuid.UUID(skill_id), tenant_id)
                if skill and skill.is_active:
                    if (skill.skill_format or "legacy") == "standard":
                        continue
                    for tool_config in skill.tools:
                        tool = tool_service.get_tool_instance(tool_config.get("tool_id", ""), tenant_id)
                        if tool:
                            if runtime_context:
                                tool.set_runtime_context(**runtime_context)
                            langchain_tool = tool.to_langchain_tool(tool_config.get("operation", None))
                            tools.append(langchain_tool)
                            tool_name = getattr(langchain_tool, "name", str(id(langchain_tool)))
                            tool_to_skill_map[tool_name] = skill_id
            except Exception as e:
                logger.error("Failed to load tools for skill %s: %s", skill_id, e)
                continue

        return tools, tool_to_skill_map

    @staticmethod
    async def load_skill_tools_async(
        db: AsyncSession,
        skill_ids: List[str],
        tenant_id: uuid.UUID,
        runtime_context: dict | None = None,
    ) -> tuple[List, dict[str, str]]:
        """Async variant of :meth:`load_skill_tools` (runtime plane)."""

        tools: List = []
        tool_to_skill_map: dict[str, str] = {}
        tool_service = ToolService(db)

        for skill_id in skill_ids:
            try:
                skill = await SkillRepository.get_by_id_async(db, uuid.UUID(skill_id), tenant_id)
                if skill and skill.is_active:
                    if (skill.skill_format or "legacy") == "standard":
                        continue
                    for tool_config in skill.tools:
                        tool = await tool_service.get_tool_instance_async(
                            tool_config.get("tool_id", ""),
                            tenant_id,
                        )
                        if tool:
                            if runtime_context:
                                tool.set_runtime_context(**runtime_context)
                            langchain_tool = tool.to_langchain_tool(tool_config.get("operation", None))
                            tools.append(langchain_tool)
                            tool_name = getattr(langchain_tool, "name", str(id(langchain_tool)))
                            tool_to_skill_map[tool_name] = skill_id
            except Exception as e:
                logger.error("Failed to load tools for skill %s: %s", skill_id, e)
                continue

        return tools, tool_to_skill_map
