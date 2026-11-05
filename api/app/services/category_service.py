"""功能分类字典：单体树侧的只读校验 + 启动灌数（方案 §4.5）。

存储是 core 自己的 `app_categories` 表。资源中心的字典接口
（`GET /apps/assets/categories`）与运维写入口（`/app-categories` 增删改查）都实现在
`application-service` 里，单体树这里保留两件事：

1. 发布时需要的存在性校验——单体树的 `AppService.publish` 也要接受 `categories`
   入参，不校验就能写进不存在的 code；
2. 启动时补齐分类行（`seed_categories`）——单体树 `DB_AUTO_UPGRADE` 默认 false
   （`app/core/config.py:124`），迁移可能压根没跑；表为空时所有分类都会被判不存在，
   发布接口直接失败。

为什么不在 pydantic validator 里校验：validator 拿不到 session，无法查表；枚举
已删除，也无从静态比较。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.models import AppCategory

logger = logging.getLogger(__name__)

# 与市场侧同一套：`^[a-z0-9][a-z0-9-]{0,31}$`
RESOURCE_TYPES: tuple[str, ...] = ("app", "tool", "skill_package")

# 四元组 = (code, 中文展示名, 英文展示名, sort)。
#
# `code` 是稳定英文短码，`app_releases.categories` 存的就是它；中文名只放 `name`。
# 与拆分服务 `src/services/category_service.py`、市场侧 `src/categories.py` 是同一套
# 词汇，靠 `tests/test_app_category_options.py` 逐字比对防漂移。
CATEGORY_OPTIONS: tuple[tuple[str, str, str, int], ...] = (
    ("office", "办公提效", "Office Productivity", 10),
    ("content", "内容创作", "Content Creation", 20),
    ("knowledge", "知识检索", "Knowledge Retrieval", 30),
    ("sales", "销售支持", "Sales Support", 40),
    ("analytics", "数据分析", "Data Analysis", 50),
    ("meeting", "会议协作", "Meeting Collaboration", 60),
    ("research", "调研分析", "Research & Analysis", 70),
    ("engineering", "研发编程", "Engineering & Coding", 80),
)


class CategoryService:
    def __init__(self, db: Session):
        self.db = db

    def existing_codes(
            self, codes: Iterable[str], *, resource_type: str = "app"
    ) -> list[str]:
        """从给定 code 里挑出「存在且启用」的那些，保持原顺序。"""

        wanted = [str(code).strip() for code in codes if str(code).strip()]
        if not wanted:
            return []
        rows = self.db.execute(
            select(AppCategory.code).where(
                AppCategory.resource_type == resource_type,
                AppCategory.code.in_(wanted),
                AppCategory.enabled.is_(True),
            )
        ).scalars().all()
        known = set(rows)
        return [code for code in wanted if code in known]

    def assert_codes_exist(
            self, codes: Iterable[str], *, resource_type: str = "app"
    ) -> None:
        """发布校验：传入的 code 必须都存在且启用。"""

        wanted = [str(code).strip() for code in codes if str(code).strip()]
        if not wanted:
            return

        rows = self.db.execute(
            select(AppCategory.code, AppCategory.enabled).where(
                AppCategory.resource_type == resource_type,
                AppCategory.code.in_(wanted),
            )
        ).all()
        known = {code for code, enabled in rows if enabled}

        unknown = [code for code in wanted if code not in known]
        if unknown:
            raise BusinessException(
                f"功能分类不存在或已停用：{unknown[0]}",
                BizCode.VALIDATION_FAILED,
                context={"invalid_categories": unknown},
            )


def build_category_rows() -> list[dict[str, Any]]:
    """三套 `resource_type` × 8 项，每行自带 id。

    `AppCategory.id` 是主键且没有 server_default（模型侧 `default=uuid.uuid4` 只在
    ORM flush 时生效），走 Core insert 必须显式给值。
    """

    return [
        {
            "id": uuid.uuid4(),
            "resource_type": resource_type,
            "code": code,
            "name": name,
            "name_en": name_en,
            "sort": sort,
            "enabled": True,
        }
        for resource_type in RESOURCE_TYPES
        for code, name, name_en, sort in CATEGORY_OPTIONS
    ]


def seed_categories(db: Session) -> int:
    """启动时补齐分类字典，返回本次真正插入的行数。

    **幂等靠 `ON CONFLICT (resource_type, code) DO NOTHING`**（落在
    `uq_app_categories_code` 上），不是「`count(*) > 0` 就整体跳过」：已存在的行
    一律不动（运营改过的 `name` / `sort` / `enabled` 不被覆盖、停用的分类不会被
    重新打开），只补缺失的行——所以删掉一行也能自愈。

    失败只记日志、返回 0：分类缺失影响发布校验，不该让服务起不来。
    """

    rows = build_category_rows()
    statement = (
        pg_insert(AppCategory)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["resource_type", "code"])
    )
    try:
        result = db.execute(statement)
        db.commit()
        inserted = result.rowcount if result.rowcount is not None else 0
    except SQLAlchemyError:
        db.rollback()
        logger.exception(
            "分类字典灌数失败（app_categories 未创建或数据库不可用）；"
            "发布接口的分类校验会全部失败"
        )
        return 0

    if inserted:
        logger.info(
            "分类字典已补 %d 行（已存在的 %d 行未改动）", inserted, len(rows) - inserted
        )
    return inserted


__all__ = [
    "CATEGORY_OPTIONS",
    "CategoryService",
    "RESOURCE_TYPES",
    "build_category_rows",
    "seed_categories",
]