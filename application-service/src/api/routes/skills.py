"""Skill family routes — management plane (design doc §9).

Six endpoints mirroring the prototype's收敛: the import pipeline is the only write
entry point (ADR-9), so ``POST /skills`` and ``PUT /skills/{id}`` are gone.

| method | path                        | role                                            |
| GET    | ``/skills``                 | list: plugin rows + standalone skills, ``kind``  |
| GET    | ``/skills/statistics``      | tab counts (plugin / skill / total), same filters |
| GET    | ``/skills/{id}``            | the single detail entry (skill or plugin)        |
| DELETE | ``/skills/{id}``            | soft-delete the row, purge the pack (ADR-13 A)   |
| POST   | ``/skills/packages/import`` | import from zip / URL, ``dry_run`` preview       |
| GET    | ``/skills/{id}/export``     | export a pack zip (standard replay / legacy)     |

Quota: ``skill_quota`` is charged on the import **commit** branch only — a
``dry_run=true`` preview writes nothing and must not consume quota (§12), so the
check runs inside the handler instead of as a route dependency.
"""

from __future__ import annotations

import io
import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ...errors import BizCode, BusinessException
from ...quota.usage import ensure_skill_quota
from ...runtime import ProcessRuntime
from ...schemas import skill_schema
from ...services.external_status_service import apply_external_status
from ...services.skill_package_service import SkillPackageService
from ..dependencies import (
    Principal,
    SyncRedis,
    get_principal,
    get_runtime,
    get_sync_db,
    get_sync_redis,
)
from ..schemas.common import success
from ..schemas.response_schema import ApiResponse, PageData, PageMeta

router = APIRouter(prefix="/skills", tags=["Skills"])

DbSession = Annotated[Session, Depends(get_sync_db)]
Caller = Annotated[Principal, Depends(get_principal)]
CacheClient = Annotated[SyncRedis, Depends(get_sync_redis)]
RuntimeDep = Annotated[ProcessRuntime, Depends(get_runtime)]


@router.get("", summary="技能列表", response_model=ApiResponse)
def list_skills(
        db: DbSession,
        principal: Caller,
        search: str | None = Query(
            None,
            description=(
                "搜索关键词（命中 id / name / description / call_name，"
                "插件额外命中插件 id 与成员）"
            ),
        ),
        ids: str | None = Query(
            None,
            description=(
                "按卡片 id 精确过滤，逗号分隔多个：skill id / package_id / 插件 id，"
                "命中任一即返回（并集），与其他过滤 AND"
            ),
        ),
        is_active: bool | None = Query(None, description="是否激活"),
        is_public: bool | None = Query(None, description="是否公开"),
        kind: str = Query("all", description="粒度过滤：all | plugin | skill"),
        page: int = Query(1, ge=1, description="页码"),
        pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
        include_external_status: bool = Query(
            False, description="是否批量回填 external.listing_status / delist_actor；失败为 null"
        ),
        cache: CacheClient = None,
        runtime: RuntimeDep = None,
):
    """List entries: plugin rows and standalone skill rows (plugin members are not cards)."""
    tenant_id = principal.tenant_id
    cards, total = SkillPackageService.list_entries(
        db, tenant_id,
        search=search, ids=ids, is_active=is_active, is_public=is_public,
        kind=kind, page=page, pagesize=pagesize,
    )
    # List cards stay lean: ``files`` / ``skill_md`` are only ever filled by the
    # detail path (``get_entry_detail``), so on list cards they were nothing but
    # Pydantic defaults (always ``null``). Drop them here instead of splitting the
    # schema, so the detail contract stays byte-identical.
    items = [
        skill_schema.Skill.model_validate(card).model_dump(
            exclude={"files", "skill_md"}
        )
        for card in cards
    ]
    if include_external_status:
        apply_external_status(
            items,
            resource_type="skill_package",
            tenant_id=tenant_id,
            market=runtime.market_gateway,
            cache=cache,
        )
    meta = PageMeta(page=page, pagesize=pagesize, total=total, hasnext=(page * pagesize) < total)
    return success(data=PageData(page=meta, items=items), msg="技能市场列表获取成功")


@router.get("/statistics", summary="技能 Tab 数量统计", response_model=ApiResponse)
def get_skill_statistics(
        db: DbSession,
        principal: Caller,
        search: str | None = Query(None, description="搜索关键词（与列表接口一致，各 Tab 共用）"),
        is_active: bool | None = Query(None, description="是否激活"),
        is_public: bool | None = Query(None, description="是否公开"),
):
    """Plugin / skill / total counts under the list filters.

    The search box is shared by every tab, so statistics accept the same query
    conditions as ``GET /skills`` (minus ``kind`` / ``ids`` / pagination). Declared
    before ``/{skill_id}`` so "statistics" is not captured by the UUID path.
    """
    stats = SkillPackageService.get_entry_statistics(
        db, principal.tenant_id,
        search=search, is_active=is_active, is_public=is_public,
    )
    return success(data=stats, msg="技能统计信息获取成功")


@router.get("/{skill_id}", summary="获取技能详情", response_model=ApiResponse)
def get_skill(
        skill_id: UUID,
        db: DbSession,
        principal: Caller,
        cache: CacheClient,
        runtime: RuntimeDep,
        include_external_status: bool = Query(
            False, description="是否回填 external.listing_status / delist_actor；仅市场来源有意义"
        ),
):
    """The single detail entry point.

    ``standard`` skills inline ``skill_md`` / ``files`` / ``has_scripts``; a plugin id
    returns its member list for drill-down; ``legacy`` rows return row fields only.
    """
    tenant_id = principal.tenant_id
    detail = SkillPackageService.get_entry_detail(db, tenant_id, skill_id)
    payload = skill_schema.Skill.model_validate(detail).model_dump()
    if include_external_status:
        apply_external_status(
            [payload],
            resource_type="skill_package",
            tenant_id=tenant_id,
            market=runtime.market_gateway,
            cache=cache,
        )
    return success(data=payload, msg="获取技能详情成功")


@router.post("/packages/import", summary="导入标准技能包（zip / URL）", response_model=ApiResponse)
def import_skill_package(
        db: DbSession,
        principal: Caller,
        file: Annotated[
            UploadFile | None,
            File(description="标准技能包 zip（含 SKILL.md）；与 url 二选一"),
        ] = None,
        url: Annotated[
            str | None,
            Form(description="GitHub 仓库 / 技能目录地址；与 file 二选一"),
        ] = None,
        dry_run: Annotated[bool, Form(description="true=只解析预览，不落库、不计配额")] = False,
        members: Annotated[
            str | None,
            Form(description="JSON 数组：插件保留的成员 identifier；缺省全部保留"),
        ] = None,
        meta: Annotated[
            str | None,
            Form(
                description="JSON 数组/对象：成员级元数据覆盖 "
                "[{identifier, name, description, call_name}]"
            ),
        ] = None,
        plugin_name: Annotated[str | None, Form(description="kind=plugin 时的插件展示名")] = None,
        plugin_description: Annotated[
            str | None,
            Form(description="kind=plugin 时的插件描述"),
        ] = None,
        overwrite: Annotated[bool, Form(description="同名但内容不同时是否覆盖更新")] = False,
        conflict_strategy: Annotated[
            str,
            Form(description="与同名 legacy 冲突策略：rename | overwrite"),
        ] = "rename",
):
    """Import a standard pack: fetch -> unpack -> validate -> persist (content_hash idempotent)."""
    tenant_id = principal.tenant_id

    # Exactly one source, checked before the upload is read or the URL is fetched:
    # taking the file and silently ignoring the URL would import something other than
    # what the caller asked for, and leaves the audit trail ambiguous.
    has_file = file is not None
    has_url = bool((url or "").strip())
    if has_file and has_url:
        raise BusinessException(
            "file 和 url 只能提供一个，不能同时提交", BizCode.BAD_REQUEST
        )
    if not has_file and not has_url:
        raise BusinessException("缺少技能包来源（file 或 url 二选一）", BizCode.BAD_REQUEST)

    if not dry_run:
        # Charged on the commit branch only: previews write nothing (§12).
        ensure_skill_quota(db, principal)

    zip_bytes = None
    source_ref = None
    if has_file:
        zip_bytes = file.file.read()
        source_ref = file.filename
        if not zip_bytes:
            raise BusinessException("上传文件为空", BizCode.BAD_REQUEST)

    member_list = _parse_json_form(members, "members", expect="array")
    meta_list = _parse_json_form(meta, "meta", expect="object")
    if conflict_strategy not in ("rename", "overwrite"):
        raise BusinessException("conflict_strategy 只接受 rename | overwrite", BizCode.BAD_REQUEST)

    result = SkillPackageService.import_package(
        db, tenant_id,
        zip_bytes=zip_bytes,
        url=url,
        dry_run=dry_run,
        members=member_list,
        meta=meta_list,
        plugin_name=plugin_name,
        plugin_description=plugin_description,
        source_ref=source_ref,
        overwrite=overwrite,
        conflict_strategy=conflict_strategy,
    )
    msg = "技能包解析完成（未落库）" if dry_run else "技能包导入成功"
    return success(data=result, msg=msg)


@router.get("/{skill_id}/export", summary="导出技能或整个插件为标准包 zip")
def export_skill_package(
        skill_id: UUID,
        db: DbSession,
        principal: Caller,
):
    """Export a zip stream. ``skill_id`` accepts a skill id or a plugin id
    (the latter aggregates every member). Standard packs replay byte-faithfully,
    legacy members convert on the fly."""
    tenant_id = principal.tenant_id
    zip_bytes, filename = SkillPackageService.export_zip(db, tenant_id, skill_id)
    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/{skill_id}", summary="删除技能", response_model=ApiResponse)
def delete_skill(
        skill_id: UUID,
        db: DbSession,
        principal: Caller,
):
    """Delete a skill or a whole plugin.

    ``{id}`` accepts a ``skills`` row id (one member) or a ``skill_plugins`` row id
    (every member plus the plugin row). Rows are soft-deleted (``is_active=False``,
    unchanged behaviour) while the pack is purged.
    """
    tenant_id = principal.tenant_id
    result = SkillPackageService.delete_entry(db, tenant_id, skill_id)
    return success(data=result, msg="技能删除成功")


def _parse_json_form(raw: str | None, field: str, *, expect: str) -> list | None:
    """Parse a JSON-bearing form field. ``expect`` is ``array`` or ``object``.

    ``members`` must be a JSON array of member identifiers. ``meta`` accepts both
    shapes: a list of ``SkillImportMember`` objects (§9.1) or an object keyed by
    member identifier (§7.2).
    """

    if raw is None or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BusinessException(f"{field} 不是合法 JSON：{exc}", BizCode.BAD_REQUEST) from exc

    if expect == "array":
        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list):
            raise BusinessException(f"{field} 需为 JSON 数组", BizCode.BAD_REQUEST)
        for item in parsed:
            if not isinstance(item, str):
                raise BusinessException(
                    f"{field} 的元素需为成员 identifier 字符串",
                    BizCode.BAD_REQUEST,
                )
        return parsed

    if isinstance(parsed, list):
        members = []
        for item in parsed:
            if not isinstance(item, dict):
                raise BusinessException(f"{field} 的元素需为对象", BizCode.BAD_REQUEST)
            members.append(item)
        return members
    if isinstance(parsed, dict):
        # {identifier: {name, description, call_name}} -> list with identifier merged in.
        members = []
        for identifier, value in parsed.items():
            entry = dict(value) if isinstance(value, dict) else {}
            entry.setdefault("identifier", identifier)
            members.append(entry)
        return members
    raise BusinessException(f"{field} 需为 JSON 数组或对象", BizCode.BAD_REQUEST)