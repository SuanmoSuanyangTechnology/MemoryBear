"""Skill Controller - 技能市场管理"""
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional
import io
import uuid

from app.db import get_db
from app.dependencies import get_current_user
from app.models import User
from app.schemas import skill_schema
from app.schemas.response_schema import PageData, PageMeta
from app.services.skill_service import SkillService
from app.services.skill_package_service import SkillPackageService
from app.core.response_utils import success
from app.core.quota_stub import check_skill_quota

router = APIRouter(prefix="/skills", tags=["Skills"])


def _skill_payload(db: Session, skill_id, tenant_id, package_result: Optional[dict] = None) -> dict:
    """组装「Skill 行 + 标准包结果」的合并响应。

    对外字段以 Skill 行为主（老前端读 id/name/description 等字段不受影响），
    另并入标准包链路产生的 package_id / warnings 等附加字段。
    """
    payload: dict = {}
    if skill_id:
        skill = SkillService.get_skill(db, uuid.UUID(str(skill_id)), tenant_id)
        payload = skill_schema.Skill.model_validate(skill).model_dump()
    if package_result:
        for key in ("package_id", "content_hash", "files_count",
                    "warnings", "idempotent", "conflict_resolved"):
            if package_result.get(key) is not None:
                payload[key] = package_result[key]
    return payload


@router.post("", summary="创建技能（一律创建标准格式）")
@check_skill_quota
def create_skill(
    data: skill_schema.SkillCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """创建技能（唯一入口，沿用老路径 `POST /api/skills`，直接改造为标准格式）。

    入参：`skill_md`（SKILL.md 全文，含 frontmatter）+ 可选 `files`（附加文件）。
    不再支持创建 legacy 形态技能；存量 legacy 技能保持可运行、可通过
    管理员批量迁移脚本升级为标准格式。
    """
    tenant_id = current_user.tenant_id
    result = SkillPackageService.create_from_editor(
        db, tenant_id, skill_md=data.skill_md, files=data.files,
    )
    return success(
        data=_skill_payload(db, result.get("skill_id"), tenant_id, result),
        msg="技能创建成功",
    )


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
    """获取技能详情（唯一详情接口）。

    standard 技能一并返回包内容（skill_md / files / has_scripts），
    供编辑器回显；legacy 技能返回行字段，行为与旧版一致。
    """
    tenant_id = current_user.tenant_id
    skill = SkillService.get_skill(db, skill_id, tenant_id)
    payload = skill_schema.Skill.model_validate(skill).model_dump()
    if (skill.skill_format or "legacy") == "standard" and skill.package_id:
        pkg = SkillPackageService.get_package_content(db, skill_id, tenant_id)
        # 只并入编辑器需要的字段，不覆盖 skills 行本身的 name/description/id
        payload["skill_md"] = pkg.get("skill_md")
        payload["files"] = pkg.get("files") or []
        payload["has_scripts"] = pkg.get("has_scripts", False)
    return success(data=payload, msg="获取技能详情成功")


@router.put("/{skill_id}", summary="更新技能")
def update_skill(
    skill_id: uuid.UUID,
    data: skill_schema.SkillUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """更新技能（唯一更新接口）。

    - standard 技能：传 `skill_md` / `files` 更新包内容（整体替换）
    - legacy 技能：更新 name / description / tools / prompt 等行字段（存量兼容）
    """
    tenant_id = current_user.tenant_id
    if data.skill_md is not None or data.files is not None:
        result = SkillPackageService.update_package_content(
            db, skill_id, tenant_id, skill_md=data.skill_md or "", files=data.files,
        )
        return success(
            data=_skill_payload(db, skill_id, tenant_id, result), msg="技能更新成功",
        )

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


# ==================== 标准包（Agent Skills 协议）批量动作端点 ====================
# 说明：创建 / 详情 / 更新已统一到 POST /skills、GET /skills/{id}、PUT /skills/{id}，
#       不再单独提供 /packages/create、/{id}/package、/{id}/migrate 端点。
#       不提供 legacy → standard 的迁移功能；legacy 技能保留原链路运行，
#       可通过 GET /skills/{id}/export 导出为标准结构。

@router.post("/packages/import", summary="导入标准技能包（zip）")
@check_skill_quota
def import_skill_package(
    file: UploadFile = File(..., description="标准技能包 zip（含 SKILL.md）"),
    overwrite: bool = Form(False, description="同版本内容不同时覆盖更新"),
    conflict_strategy: str = Form("rename", description="同名 legacy 技能冲突策略：overwrite | rename"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """导入 Agent Skills 标准包：解包 → frontmatter 校验 → 落库（content_hash 幂等）"""
    tenant_id = current_user.tenant_id
    zip_bytes = file.file.read()
    if not zip_bytes:
        from app.core.exceptions import BusinessException
        from app.core.error_codes import BizCode
        raise BusinessException("上传文件为空", BizCode.BAD_REQUEST)
    result = SkillPackageService.import_package(
        db, zip_bytes, tenant_id,
        source_ref=file.filename,
        overwrite=overwrite,
        conflict_strategy=conflict_strategy,
    )
    return success(data=result, msg="技能包导入成功")


@router.get("/{skill_id}/export", summary="导出技能为标准包 zip")
def export_skill_package(
    skill_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """导出技能为标准文件夹 zip（standard 原文还原；legacy 自动转换为标准结构）"""
    tenant_id = current_user.tenant_id
    zip_bytes, filename = SkillPackageService.export_package(db, skill_id, tenant_id)
    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
