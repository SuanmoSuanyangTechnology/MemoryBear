"""功能分类字典：单体树侧的只读校验（方案 §4.5）。

存储是 core 自己的 `app_categories` 表。资源中心的字典接口
（`GET /apps/assets/categories`）与运维写入口（`/app-categories` 增删改查）都实现在
`application-service` 里，单体树这里只保留发布时需要的存在性校验——单体树的
`AppService.publish` 也要接受 `categories` 入参，不校验就能写进不存在的 code。

为什么不在 pydantic validator 里校验：validator 拿不到 session，无法查表；枚举
已删除，也无从静态比较。
"""
from __future__ import annotations

from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.models import AppCategory

# 与市场侧同一套：`^[a-z0-9][a-z0-9-]{0,31}$`
RESOURCE_TYPES: tuple[str, ...] = ("app", "tool", "skill_package")


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


__all__ = ["CategoryService", "RESOURCE_TYPES"]