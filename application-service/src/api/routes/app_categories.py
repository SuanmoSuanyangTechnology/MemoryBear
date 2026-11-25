"""功能分类字典的运维入口（方案 §4.5）。

分类的唯一存储是 core 自己的 `app_categories`，资源中心与发布弹窗读它。写入
只有这里一处，所以「加一个分类」从「改代码 + 发版」变成「调一次接口」。

**社区市场是另一套存储**：`market_categories` 归 market-service（企业端才有），
本接口不写它。两边靠 `code` 对齐，各自 seed 一份，靠测试比对防漂移。

为什么挂在 `internal/v1` 而不是 `/apps/assets` 下：`resource_type` 是为后续
不同来源的分类预留的维度，而资源中心那几条 `/apps/assets/*` 是租户面、只认
`app` 组；把写接口混进去会让「资源中心只管租户浏览」这个边界变糊。
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from ...services.category_service import CategoryService
from ..dependencies import get_sync_db
from ..schemas.common import success
from ..schemas.response_schema import ApiResponse

router = APIRouter(prefix="/app-categories", tags=["App Categories"])

DbSession = Annotated[Session, Depends(get_sync_db)]


@router.get("", summary="分类字典：运维面全量", response_model=ApiResponse)
def list_categories(
        db: DbSession,
        resource_type: str | None = Query(
            None, description="app | tool | skill_package，不传返回全部"
        ),
        enabled: bool | None = Query(None, description="启停过滤，不传返回全部"),
):
    """含 `id` / `resource_type` / `enabled`，供运营维护页用。

    租户面要的是精简版（只有 `code` / `name` / `name_en` / `sort`），走
    `GET /apps/assets/categories`；市场浏览面走 `GET /api/market/categories`
    （带分 tab 计数）。三处读的是同一份行，字段多寡按场景裁剪。
    """

    items = CategoryService(db).list_categories(
        resource_type=resource_type, enabled=enabled
    )
    return success(data={"items": items, "total": len(items)}, msg="获取分类列表成功")


@router.post("", summary="分类字典：新建", response_model=ApiResponse)
def create_category(
        db: DbSession,
        payload: dict = Body(
            ...,
            description=(
                "code（英文短码，必填）、name（中文名，必填）、name_en、sort、"
                "enabled、resource_type（不传则三个类型各写一行）"
            ),
        ),
):
    """新建分类。不传 `resource_type` 时 `app` / `tool` / `skill_package` 各写一行。

    三个 tab 同时出现是默认行为：只写一个类型会让另一个 tab 的分类栏少一项，
    而它们本来就是同一套词汇。
    """

    items = CategoryService(db).create(
        code=payload.get("code", ""),
        name=payload.get("name", ""),
        name_en=payload.get("name_en", ""),
        sort=payload.get("sort", 0),
        enabled=payload.get("enabled", True),
        resource_type=payload.get("resource_type"),
    )
    return success(data={"items": items, "total": len(items)}, msg="分类创建成功")


@router.patch("/{category_id}", summary="分类字典：修改", response_model=ApiResponse)
def update_category(
        category_id: UUID,
        db: DbSession,
        payload: dict = Body(
            ..., description="name / name_en / sort / enabled，字段均可选"
        ),
):
    """改名、英文名、排序、启停。`code` 与 `resource_type` 创建后不可改。

    `code` 已冻结进历史 `app_releases.categories` 与市场 `display.categories`，
    改动会让已发布版本 join 不上；`resource_type` 决定它出现在哪个 tab。
    """

    item = CategoryService(db).update(category_id=category_id, **payload)
    return success(data=item, msg="分类更新成功")


@router.delete("/{category_id}", summary="分类字典：删除", response_model=ApiResponse)
def delete_category(category_id: UUID, db: DbSession):
    """物理删除。被任一应用版本引用时返回 `RESOURCE_IN_USE`（5005）。

    被引用时不该删：已发布版本的 `categories` 里存着它的 code，删掉会让那些
    版本在筛选里消失、市场侧 join 成空。要下线就改成 `enabled=false`。
    """

    CategoryService(db).delete(category_id=category_id)
    return success(msg="分类已删除")