"""Skill Controller - 技能市场管理"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional
import uuid

from app.db import get_db
from app.dependencies import get_current_user
from app.models import User
from app.schemas import skill_schema
from app.schemas.response_schema import PageData, PageMeta
from app.services.skill_service import SkillService
from app.core.response_utils import success

router = APIRouter(prefix="/skills", tags=["Skills"])


@router.post("", summary="创建技能")
def create_skill(
    data: skill_schema.SkillCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """创建技能 - 可以关联现有工具（内置、MCP、自定义）"""
    tenant_id = current_user.tenant_id
    skill = SkillService.create_skill(db, data, tenant_id)
    return success(data=skill_schema.Skill.model_validate(skill), msg="技能创建成功")


@router.get("", summary="技能列表")
def list_skills(
    search: Optional[str] = Query(None, description="搜索关键词"),
    is_active: Optional[bool] = Query(None, description="是否激活"),
    is_public: Optional[bool] = Query(None, description="是否公开"),
    page: int = Query(1, ge=1, description="页码"),
    pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """技能市场列表 - 包含本工作空间和公开的技能"""
    tenant_id = current_user.tenant_id
    skills, total = SkillService.list_skills(
        db, tenant_id, search, is_active, is_public, page, pagesize
    )
    
    items = [skill_schema.Skill.model_validate(s) for s in skills]
    meta = PageMeta(page=page, pagesize=pagesize, total=total, hasnext=(page * pagesize) < total)
    return success(data=PageData(page=meta, items=items), msg="技能市场列表获取成功")


@router.get("/{skill_id}", summary="获取技能详情")
def get_skill(
    skill_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取技能详情"""
    tenant_id = current_user.tenant_id
    skill = SkillService.get_skill(db, skill_id, tenant_id)
    return success(data=skill_schema.Skill.model_validate(skill), msg="获取技能详情成功")


@router.put("/{skill_id}", summary="更新技能")
def update_skill(
    skill_id: uuid.UUID,
    data: skill_schema.SkillUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """更新技能"""
    tenant_id = current_user.tenant_id
    skill = SkillService.update_skill(db, skill_id, data, tenant_id)
    return success(data=skill_schema.Skill.model_validate(skill), msg="技能更新成功")


@router.delete("/{skill_id}", summary="删除技能")
def delete_skill(
    skill_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """删除技能"""
    tenant_id = current_user.tenant_id
    SkillService.delete_skill(db, skill_id, tenant_id)
    return success(msg="技能删除成功")
