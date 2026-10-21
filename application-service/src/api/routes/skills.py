"""Skill family management routes.

Mirrors the monolith ``skill_controller`` (definition plane). The account-level
skill quota decorator on create becomes a dependency that runs before the
handler body, matching the host decorator evaluation order.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...quota.usage import require_skill_quota
from ...schemas import skill_schema
from ...services.skill_service import SkillService
from ..dependencies import Principal, get_principal, get_sync_db
from ..schemas.common import success
from ..schemas.response_schema import ApiResponse, PageData, PageMeta

router = APIRouter(prefix="/skills", tags=["Skills"])

DbSession = Annotated[Session, Depends(get_sync_db)]
Caller = Annotated[Principal, Depends(get_principal)]
SkillQuota = Annotated[None, Depends(require_skill_quota)]


@router.post("", summary="创建技能", response_model=ApiResponse)
def create_skill(
        data: skill_schema.SkillCreate,
        _quota: SkillQuota,
        db: DbSession,
        principal: Caller,
):
    """Create a skill - can link existing tools (builtin, MCP, custom)"""
    tenant_id = principal.tenant_id
    skill = SkillService(db).create_skill(data, tenant_id)
    return success(data=skill_schema.Skill.model_validate(skill), msg="技能创建成功")


@router.get("", summary="技能列表", response_model=ApiResponse)
def list_skills(
        db: DbSession,
        principal: Caller,
        search: str | None = Query(None, description="搜索关键词"),
        is_active: bool | None = Query(None, description="是否激活"),
        is_public: bool | None = Query(None, description="是否公开"),
        page: int = Query(1, ge=1, description="页码"),
        pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
):
    """Skill marketplace list - includes the workspace's own and public skills"""
    tenant_id = principal.tenant_id
    skills, total = SkillService(db).list_skills(
        tenant_id, search, is_active, is_public, page, pagesize
    )

    items = [skill_schema.Skill.model_validate(s) for s in skills]
    meta = PageMeta(page=page, pagesize=pagesize, total=total, hasnext=(page * pagesize) < total)
    return success(data=PageData(page=meta, items=items), msg="技能市场列表获取成功")


@router.get("/{skill_id}", summary="获取技能详情", response_model=ApiResponse)
def get_skill(
        skill_id: UUID,
        db: DbSession,
        principal: Caller,
):
    """Get skill details"""
    tenant_id = principal.tenant_id
    skill = SkillService(db).get_skill(skill_id, tenant_id)
    return success(data=skill_schema.Skill.model_validate(skill), msg="获取技能详情成功")


@router.put("/{skill_id}", summary="更新技能", response_model=ApiResponse)
def update_skill(
        skill_id: UUID,
        data: skill_schema.SkillUpdate,
        db: DbSession,
        principal: Caller,
):
    """Update a skill"""
    tenant_id = principal.tenant_id
    skill = SkillService(db).update_skill(skill_id, data, tenant_id)
    return success(data=skill_schema.Skill.model_validate(skill), msg="技能更新成功")


@router.delete("/{skill_id}", summary="删除技能", response_model=ApiResponse)
def delete_skill(
        skill_id: UUID,
        db: DbSession,
        principal: Caller,
):
    """Delete a skill"""
    tenant_id = principal.tenant_id
    SkillService(db).delete_skill(skill_id, tenant_id)
    return success(msg="技能删除成功")
