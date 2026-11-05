"""功能分类字典的读写入口（方案 §4.5）。

分类此前是 `app_schema.APP_CATEGORY_OPTIONS` 里的代码常量，加一项要改代码 +
发版。现在唯一存储是 core 自己的 `app_categories` 表：

- 资源中心 `GET /apps/assets/categories` 读它；
- 写入只有 `/internal/v1/app-categories` 这一处入口。

**社区市场是另一套存储**：`market_categories` 归 market-service（企业端才有），
这里不读不写。两边靠 `code` 对齐——投稿时 `app_releases.categories` 原样当
`display.categories` 传，不做 name→code 映射；同一个分类在两边各有一行，靠
`tests/test_app_category_options.py` 比对两份 seed 防漂移。

`resource_type` 一期只有 `app` 有消费方，工具/技能的分类以后可能不同，先留着
维度。

`code` 创建后不可修改：它已经冻结进历史 `app_releases.categories`，改了就对不上
已发布版本。
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Iterable

from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..errors import BizCode, BusinessException, ResourceNotFoundException
from ..models.app_asset_model import AppCategory
from ..models.app_release_model import AppRelease

logger = logging.getLogger(__name__)

# 四元组 = (code, 中文展示名, 英文展示名, sort)。
#
# `code` 是稳定英文短码，`app_releases.categories` 存的就是它；中文名只放 `name`。
# 与市场侧 `market_categories` 是同一套词汇，靠
# `tests/test_app_category_options.py` 与市场启动常量逐字比对防漂移。
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

# 与市场侧 `MarketCategory.code` 的校验同一套：`^[a-z0-9][a-z0-9-]{0,31}$`
CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")

# 一期只有 `app` 有消费方；新建时不传 `resource_type` 就三组一起写，
# 保证以后开工具/技能 tab 时不会只有应用有分类、别的缺项。
RESOURCE_TYPES: tuple[str, ...] = ("app", "tool", "skill_package")

MAX_NAME_LENGTH = 128
MAX_SORT = 1_000_000


class CategoryService:
    def __init__(self, db: Session):
        self.db = db

    # ---------- 读 ----------

    def list_options(self, *, resource_type: str = "app") -> list[dict[str, Any]]:
        """字典项，只给 `code` / `name` / `name_en` / `sort`（不统计数量）。

        字段名与市场侧 `/api/market/categories` 的 `items[]` 同名同义，少
        `id` / `resource_type` / `enabled` / `count` —— 那四个是市场页面的需要
        （按行筛选、下架、分 tab 计数），发布弹窗与资源中心分类栏用不上。

        两张表互不引用（市场用 `market_categories`），所以这里只读 core 自己的
        `app_categories`；词表靠测试与市场 seed 逐字对齐，不靠外键。
        """

        rows = self.db.scalars(
            select(AppCategory)
            .where(
                AppCategory.resource_type == resource_type,
                AppCategory.enabled.is_(True),
            )
            .order_by(AppCategory.sort, AppCategory.code)
        ).all()
        return [
            {
                "code": row.code,
                "name": row.name,
                "name_en": row.name_en,
                "sort": row.sort,
            }
            for row in rows
        ]

    def list_categories(
            self, *, resource_type: str | None = None, enabled: bool | None = None
    ) -> list[dict[str, Any]]:
        """运营面用：含 `id` / `resource_type` / `enabled`，可按类型与启停过滤。"""

        stmt = select(AppCategory)
        if resource_type:
            stmt = stmt.where(AppCategory.resource_type == resource_type)
        if enabled is not None:
            stmt = stmt.where(AppCategory.enabled.is_(enabled))
        rows = self.db.scalars(
            stmt.order_by(AppCategory.resource_type, AppCategory.sort, AppCategory.code)
        ).all()
        return [self._to_dict(row) for row in rows]

    # ---------- 写 ----------

    def create(
            self,
            *,
            code: str,
            name: str,
            name_en: str = "",
            sort: int = 0,
            enabled: bool = True,
            resource_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """新建分类。`resource_type` 不传则三个类型各写一行。

        写入是一次事务：三个类型要么都成，要么都不成，避免出现只有 app tab
        有、tool tab 没有的半截状态。
        """

        normalized_code = self._validate_code(code)
        normalized_name = self._require_text(name, "中文展示名")
        normalized_name_en = (name_en or "").strip()
        if len(normalized_name_en) > MAX_NAME_LENGTH:
            raise BusinessException(
                f"英文展示名最多 {MAX_NAME_LENGTH} 字符", BizCode.VALIDATION_FAILED
            )
        target_types = self._validate_types(resource_type)

        existing = self.db.scalars(
            select(AppCategory).where(
                AppCategory.resource_type.in_(target_types),
                AppCategory.code == normalized_code,
            )
        ).all()
        if existing:
            raise BusinessException(
                f"分类 code「{normalized_code}」在 {existing[0].resource_type} 下已存在",
                BizCode.RESOURCE_ALREADY_EXISTS,
                context={"code": normalized_code,
                         "resource_type": existing[0].resource_type},
            )

        created: list[AppCategory] = []
        for target in target_types:
            row = AppCategory(
                id=uuid.uuid4(),
                resource_type=target,
                code=normalized_code,
                name=normalized_name,
                name_en=normalized_name_en,
                sort=self._validate_sort(sort),
                enabled=enabled,
            )
            self.db.add(row)
            created.append(row)

        self.db.commit()
        for row in created:
            self.db.refresh(row)
        return [self._to_dict(row) for row in created]

    def update(
            self, *, category_id: uuid.UUID, **fields: Any
    ) -> dict[str, Any]:
        """改名 / 英文名 / 排序 / 启停。`code` 与 `resource_type` 不可改。"""

        row = self._get_or_404(category_id)

        if "name" in fields and fields["name"] is not None:
            row.name = self._require_text(fields["name"], "中文展示名")
        if "name_en" in fields and fields["name_en"] is not None:
            name_en = str(fields["name_en"]).strip()
            if len(name_en) > MAX_NAME_LENGTH:
                raise BusinessException(
                    f"英文展示名最多 {MAX_NAME_LENGTH} 字符", BizCode.VALIDATION_FAILED
                )
            row.name_en = name_en
        if "sort" in fields and fields["sort"] is not None:
            row.sort = self._validate_sort(fields["sort"])
        if "enabled" in fields and fields["enabled"] is not None:
            row.enabled = bool(fields["enabled"])

        self.db.commit()
        self.db.refresh(row)
        return self._to_dict(row)

    def delete(self, *, category_id: uuid.UUID) -> None:
        """物理删除。被引用时报 `RESOURCE_IN_USE`，不静默改数据。"""

        row = self._get_or_404(category_id)

        referenced = self.db.scalar(
            select(func.count())
            .select_from(AppRelease)
            .where(
                cast(AppRelease.categories, JSONB).contains([row.code]),
            )
        )
        if referenced:
            raise BusinessException(
                f"分类「{row.name}」已被 {referenced} 个应用版本引用，不能删除，"
                f"可改为停用（enabled=false）",
                BizCode.RESOURCE_IN_USE,
                context={"code": row.code, "references": int(referenced)},
            )

        self.db.delete(row)
        self.db.commit()

    # ---------- 供发布校验调用 ----------

    def existing_codes(
            self, codes: Iterable[str], *, resource_type: str = "app"
    ) -> list[str]:
        """从给定 code 里挑出「存在且启用」的那些，保持原顺序。

        投稿前用它做白名单：release 快照是历史数据，可能带着分类调整前的旧
        code，塞进 `display.categories` 会让整包投稿被市场拒掉。
        """

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
        """发布时校验：传入的 code 必须都存在且启用。

        这条以前是 `app_schema._validate_categories` 里的静态枚举比较，现在挪到
        服务层——枚举已不存在，只能查表。放在 publish 里而不是 pydantic
        validator 里，是因为 validator 拿不到 session。
        """

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

    # ---------- 内部 ----------

    def _get_or_404(self, category_id: uuid.UUID) -> AppCategory:
        row = self.db.get(AppCategory, category_id)
        if row is None:
            raise ResourceNotFoundException("分类", str(category_id))
        return row

    @staticmethod
    def _to_dict(row: AppCategory) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "resource_type": row.resource_type,
            "code": row.code,
            "name": row.name,
            "name_en": row.name_en,
            "sort": row.sort,
            "enabled": bool(row.enabled),
        }

    @staticmethod
    def _validate_code(code: str) -> str:
        normalized = (code or "").strip()
        if not CODE_PATTERN.fullmatch(normalized):
            raise BusinessException(
                f"分类 code 必须是 1~32 位英文小写短码（如 office），收到「{code}」",
                BizCode.VALIDATION_FAILED,
                context={"code": code},
            )
        return normalized

    @staticmethod
    def _require_text(value: str, label: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise BusinessException(f"{label}不能为空", BizCode.VALIDATION_FAILED)
        if len(normalized) > MAX_NAME_LENGTH:
            raise BusinessException(
                f"{label}最多 {MAX_NAME_LENGTH} 字符", BizCode.VALIDATION_FAILED
            )
        return normalized

    @staticmethod
    def _validate_sort(value: int) -> int:
        try:
            sort = int(value)
        except (TypeError, ValueError):
            raise BusinessException("排序必须是整数", BizCode.VALIDATION_FAILED)
        if sort < 0 or sort > MAX_SORT:
            raise BusinessException(
                f"排序需在 0~{MAX_SORT} 之间", BizCode.VALIDATION_FAILED
            )
        return sort

    @staticmethod
    def _validate_types(resource_type: str | None) -> list[str]:
        if resource_type is None:
            return list(RESOURCE_TYPES)
        normalized = str(resource_type).strip()
        if normalized not in RESOURCE_TYPES:
            raise BusinessException(
                f"resource_type 只能是 {'/'.join(RESOURCE_TYPES)}，收到「{resource_type}」",
                BizCode.VALIDATION_FAILED,
                context={"resource_type": resource_type},
            )
        return [normalized]


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

    存在的必要性：`app_categories` 一旦为空，`assert_codes_exist` 会把所有分类判成
    不存在，发布接口直接失败；而迁移可能因为 `DB_AUTO_UPGRADE=false` 从未执行，
    或者库是从别处恢复的。所以不能只依赖迁移里的 seed。

    **幂等靠 `ON CONFLICT (resource_type, code) DO NOTHING`**（落在
    `uq_app_categories_code` 上），不是「`count(*) > 0` 就整体跳过」：

    - 已存在的行一律不动 —— 运营改过的 `name` / `sort` / `enabled` 不会被启动
      覆盖回出厂值，也不会把运营停用的分类重新打开；
    - 只补缺失的行，所以「被删了一行」也能自愈，后者永远补不回来；
    - 多副本同时启动、反复重启都不打架。

    失败只记日志、返回 0：分类缺失影响的是发布校验，不该让服务起不来。
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
    "CODE_PATTERN",
    "RESOURCE_TYPES",
    "build_category_rows",
    "seed_categories",
]