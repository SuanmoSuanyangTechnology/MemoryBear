"""Skill Service"""
import uuid
from typing import List

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.repositories.skill_repository import SkillRepository
from app.schemas.skill_schema import SkillCreate, SkillUpdate
from app.models.skill_model import Skill
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode
from app.services.tool_service import ToolService


class SkillService:
    """Skill 业务逻辑层"""

    @staticmethod
    def create_skill(db: Session, data: SkillCreate, tenant_id: uuid.UUID) -> Skill:
        """创建技能"""
        # 检查同名技能
        existing = db.query(Skill).filter(
            Skill.tenant_id == tenant_id,
            Skill.name == data.name
        ).first()
        if existing:
            raise BusinessException(f"技能名称'{data.name}'已存在", BizCode.DUPLICATE_NAME)
        
        skill = SkillRepository.create(db, data, tenant_id)
        db.commit()
        db.refresh(skill)
        return skill

    @staticmethod
    def get_skill(db: Session, skill_id: uuid.UUID, tenant_id: uuid.UUID) -> Skill:
        """获取技能"""
        try:
            skill = SkillRepository.get_by_id(db, skill_id, tenant_id)
            if not skill:
                raise BusinessException(f"技能{skill_id}不存在", BizCode.NOT_FOUND)
            
            # 填充工具详情
            tool_service = ToolService(db)
            enriched_tools = []
            for tool_config in skill.tools:
                tool_id = tool_config.get("tool_id")
                if tool_id:
                    tool_info = tool_service.get_tool_info(tool_id, tenant_id)
                    if tool_info:
                        enriched_tools.append({
                            "tool_id": tool_id,
                            "operation": tool_config.get("operation"),
                            "tool_info": tool_info
                        })
            skill.tools = enriched_tools
            
            return skill
        except (BusinessException, SQLAlchemyError) as e:
            db.rollback()
            raise e

    @staticmethod
    def list_skills(
        db: Session,
        tenant_id: uuid.UUID,
        search: str = None,
        is_active: bool = None,
        is_public: bool = None,
        page: int = 1,
        pagesize: int = 10
    ) -> tuple[list[type[Skill]], int]:
        """列出技能"""
        return SkillRepository.list_skills(
            db, tenant_id, search, is_active, is_public, page, pagesize
        )

    @staticmethod
    def update_skill(db: Session, skill_id: uuid.UUID, data: SkillUpdate, tenant_id: uuid.UUID) -> Skill:
        """更新技能"""
        try:
            skill = SkillRepository.update(db, skill_id, data, tenant_id)
            if not skill:
                raise BusinessException(f"技能{skill_id}不存在或无权限", BizCode.NOT_FOUND)
            db.commit()
            db.refresh(skill)
            return skill
        except (BusinessException, SQLAlchemyError) as e:
            db.rollback()
            raise e

    @staticmethod
    def delete_skill(db: Session, skill_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
        """删除技能"""
        try:
            success = SkillRepository.delete(db, skill_id, tenant_id)
            if not success:
                raise BusinessException("技能不存在或无权限", BizCode.NOT_FOUND)
            db.commit()
            return True
        except (BusinessException, SQLAlchemyError) as e:
            db.rollback()
            raise e

    @staticmethod
    def load_skill_tools(db: Session, skill_ids: List[str], tenant_id: uuid.UUID) -> tuple[List, dict[str, str]]:
        """加载技能关联的工具
        
        Returns:
            (tools, tool_to_skill_map) - 工具列表和工具到技能的映射
        """
        tools = []
        tool_to_skill_map = {}  # {tool_name: skill_id}
        tool_service = ToolService(db)
        
        for skill_id in skill_ids:
            try:
                skill = SkillRepository.get_by_id(db, uuid.UUID(skill_id), tenant_id)
                if skill and skill.is_active:
                    # 加载技能关联的工具
                    for tool_config in skill.tools:
                        tool = tool_service._get_tool_instance(tool_config.get("tool_id", ""), tenant_id)
                        if tool:
                            langchain_tool = tool.to_langchain_tool(tool_config.get("operation", None))
                            tools.append(langchain_tool)
                            # 建立工具到技能的映射
                            tool_name = getattr(langchain_tool, 'name', str(id(langchain_tool)))
                            tool_to_skill_map[tool_name] = skill_id
            except Exception as e:
                print(f"加载技能 {skill_id} 的工具时出错: {e}")
                continue

        return tools, tool_to_skill_map
