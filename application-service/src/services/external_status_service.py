"""External（市场来源）状态回填（设计文档 §11.2 / P1-7）.

主库只记录「这个本地副本来自市场的哪个资源、哪个版本」——`origin` /
`external_resource_id` / `external_version`。**市场状态不落库**：它是市场的
真相，落一份到主库立刻就有两个可能不同的值。

所以列表/详情默认返回 `external.listing_status = null`，只有请求带
`include_external_status=true` 时才批量去问一次。三条约束：

1. **一次批量，不 N+1**：按 `resource_type` 分组，每组一次调用（他的上限 100 个）。
2. **缓存 30 秒**：状态不是强一致数据，运营刚点通过就刷新列表不该打出一次 HTTP。
3. **失败整批为 `null`**：市场挂了不能让「我的应用列表」跟着 500，这是列表页
   最重要的可用性约束（接口文档 v0.10 §1.1「查询失败整批为 `null`，列表正常
   返回」）。`external.status` 摘要字段已在 v0.10 删除，`listing_status` /
   `delist_actor` 是唯一状态口径。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Callable, Iterable

from ..external_catalog.errors import ExternalCatalogError
from ..protocols.market_gateway import MarketGateway

logger = logging.getLogger(__name__)

STATUS_CACHE_TTL_SECONDS = 30
# v2 起缓存的是状态三元组 JSON，与 v1 的裸字符串不同格式，换命名空间避免读串。
STATUS_CACHE_NAMESPACE = "cache:external-status:v2"
STATUS_BATCH_LIMIT = 100

LISTING_STATUSES = frozenset(
    {"not_listed", "listed", "pending_listing", "delisted"}
)
DELIST_ACTORS = frozenset({"publisher", "operator"})


def build_external_ref(
        *,
        resource_id: Any,
        version: Any,
) -> dict[str, Any]:
    """`ExternalRef`（契约 v0.10 §1.1）。`source` 本期固定 `community_market`。

    `listing_status` / `delist_actor` 只在 `include_external_status=true` 时回填，
    否则都是 `null`。这里把键写全，让前端不必区分「字段不存在」与「还没回填」。
    """

    return {
        "source": "community_market",
        "resource_id": str(resource_id) if resource_id else None,
        "version": version,
        "listing_status": None,
        "delist_actor": None,
    }


def origin_of(*, origin: str | None) -> str:
    """主库只存 `local` / `external`，空值一律按 `local` 处理。"""

    return "external" if str(origin or "").lower() == "external" else "local"


class ExternalStatusResolver:
    """批量把 `status` 填进已构造好的 `external` 对象里。"""

    def __init__(self, market: MarketGateway, cache: Any | None = None):
        self.market = market
        self.cache = cache

    def fill(
            self,
            refs: Iterable[dict[str, Any] | None],
            *,
            resource_type: str,
            tenant_id: uuid.UUID,
    ) -> None:
        """就地回填 `refs` 的 `listing_status` / `delist_actor`。

        不可用时整批为 `null`（市场挂了不代表资源被下架，也不能凭空给一个
        `unknown` 之外的枚举——v0.10 已没有摘要状态字段）。
        """

        live = [ref for ref in refs if ref and _needs_fill(ref)]
        if not live:
            return
        records = self._resolve(
            [str(ref["resource_id"]) for ref in live],
            resource_type=resource_type,
            tenant_id=tenant_id,
        )
        for ref in live:
            record = records.get(str(ref["resource_id"])) or {}
            ref["listing_status"] = record.get("listing_status")
            ref["delist_actor"] = record.get("delist_actor")

    # ==================== 内部 ====================

    def _resolve(
            self,
            resource_ids: list[str],
            *,
            resource_type: str,
            tenant_id: uuid.UUID,
    ) -> dict[str, dict[str, Any]]:
        resolved: dict[str, dict[str, Any]] = {}
        missing: list[str] = []

        for resource_id in dict.fromkeys(resource_ids):
            cached = self._cache_get(tenant_id, resource_type, resource_id)
            if cached is None:
                missing.append(resource_id)
            else:
                resolved[resource_id] = cached

        if not missing:
            return resolved

        # 社区版的 `DisabledMarketGateway` 对每次调用抛 6020，下面统一降级为
        # `null`——所以这里不需要先探测 `enabled`，也不必多一个分支。
        for start in range(0, len(missing), STATUS_BATCH_LIMIT):
            chunk = missing[start:start + STATUS_BATCH_LIMIT]
            try:
                items = asyncio.run(
                    self.market.publication_status(
                        resource_type, chunk, str(tenant_id)
                    )
                )
            except Exception as error:  # noqa: BLE001 - 列表页可用性优先于状态准确性
                logger.warning(
                    "市场状态查询失败，整批为 null",
                    extra={"resource_type": resource_type, "error": str(error)[:200]},
                )
                for resource_id in chunk:
                    resolved[resource_id] = {}
                continue
            resolved.update(_map_statuses(items, chunk))
            for resource_id, record in resolved.items():
                if record:
                    self._cache_set(tenant_id, resource_type, resource_id, record)

        for resource_id in missing:
            resolved.setdefault(resource_id, {})
        return resolved

    def _cache_key(self, tenant_id: uuid.UUID, resource_type: str, resource_id: str) -> str:
        return f"{STATUS_CACHE_NAMESPACE}:{tenant_id}:{resource_type}:{resource_id}"

    def _cache_get(
            self, tenant_id: uuid.UUID, resource_type: str, resource_id: str
    ) -> dict[str, Any] | None:
        if self.cache is None:
            return None
        try:
            raw = self.cache.get(self._cache_key(tenant_id, resource_type, resource_id))
        except Exception:  # noqa: BLE001 - 缓存挂了不该影响列表
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        if not isinstance(raw, str):
            return None
        try:
            record = json.loads(raw)
        except ValueError:
            return None
        return record if isinstance(record, dict) else None

    def _cache_set(
            self,
            tenant_id: uuid.UUID,
            resource_type: str,
            resource_id: str,
            record: dict[str, Any],
    ) -> None:
        if self.cache is None:
            return
        try:
            self.cache.setex(
                self._cache_key(tenant_id, resource_type, resource_id),
                STATUS_CACHE_TTL_SECONDS,
                json.dumps(record, ensure_ascii=False),
            )
        except Exception:  # noqa: BLE001
            logger.debug("外部状态缓存写入失败", exc_info=True)


def _needs_fill(ref: dict[str, Any]) -> bool:
    """`listing_status` 缺失就要回填。

    v0.10 起 `external` 只有 `listing_status` / `delist_actor` 两个状态字段；
    `delist_actor` 在 `listed` / `not_listed` 下本来就该是 `null`，所以不能拿它
    做判断依据。
    """

    if not ref.get("resource_id"):
        return False
    return ref.get("listing_status") is None


def _map_statuses(items: Any, requested: list[str]) -> dict[str, dict[str, Any]]:
    """把网关返回的 `MarketPublicationStatus` 批量结果折成本地 `id -> 状态二元组`。

    契约里 `local_resource_id` 就是我们传进去的资源 ID 位置；dataclass 与
    dict 两种形态都认。没查到的留空 dict（整批 `null`），列表页不能因此报错。

    `listing_status` / `delist_actor` 是 v0.10 之后唯一的状态口径：市场侧返回
    了就取，没返回（旧版本市场服务）就留 `None`。
    """

    looked_up: dict[str, dict[str, Any]] = {}
    records = items.get("items") if isinstance(items, dict) else items
    for record in records or []:
        if isinstance(record, dict):
            key = record.get("local_resource_id") or record.get("resource_id")
            listing_status = record.get("listing_status")
            delist_actor = record.get("delist_actor")
        else:
            key = getattr(record, "local_resource_id", None) or getattr(
                record, "resource_id", None
            )
            listing_status = getattr(record, "listing_status", None)
            delist_actor = getattr(record, "delist_actor", None)
        if not key:
            continue
        looked_up[str(key)] = {
            "listing_status": str(listing_status) if listing_status else None,
            "delist_actor": str(delist_actor) if delist_actor else None,
        }
    return {
        resource_id: looked_up.get(resource_id) or {}
        for resource_id in requested
    }


def market_source_ref(source_type: Any, source_ref: Any) -> dict[str, Any] | None:
    """技能包没有来源列（§11.2 备注），靠既有的两个字段还原来源。

    `source_type=market` 且 `source_ref="<resource_id>@<version>"` → ExternalRef；
    其余（含导入的 URL / zip 名）一律 `None`，调用方据此写 `origin=local`。
    格式不符时宁可当作本地，也不猜一个错误的资源 ID——那会让前端去查一个
    根本不存在的市场条目。
    """

    if str(source_type or "") != "market":
        return None
    raw = str(source_ref or "").strip()
    resource_id, _, version = raw.partition("@")
    if not resource_id:
        return None
    return build_external_ref(resource_id=resource_id, version=version or None)


def collect_external_refs(
        rows: Iterable[Any],
        *,
        get_origin: Callable[[Any], str | None],
        get_resource_id: Callable[[Any], Any],
        get_version: Callable[[Any], Any],
) -> list[dict[str, Any] | None]:
    """把一批本地行折成 `ExternalRef`（非 external 的给 `None`）。"""

    refs: list[dict[str, Any] | None] = []
    for row in rows:
        if origin_of(origin=get_origin(row)) != "external":
            refs.append(None)
            continue
        refs.append(
            build_external_ref(
                resource_id=get_resource_id(row), version=get_version(row)
            )
        )
    return refs


def apply_external_status(
        items: Iterable[Any],
        *,
        resource_type: str,
        tenant_id: uuid.UUID,
        market: MarketGateway,
        cache: Any | None = None,
) -> None:
    """就地回填一批已序列化条目的 `external.listing_status`。

    同时接受 pydantic 模型（`App` / `ToolInfo`）与裸 dict（技能卡片直接是
    `model_dump()` 的结果）：前者改属性，后者改键。Pydantic v2 校验时会复制
    `dict` 字段，所以回填后必须**写回**而不能只改引用。
    """

    rows = [item for item in items if item is not None]
    if not rows:
        return
    refs = [_get_external(item) for item in rows]
    ExternalStatusResolver(market, cache).fill(
        refs, resource_type=resource_type, tenant_id=tenant_id
    )
    for item, ref in zip(rows, refs):
        if ref:
            _set_external(item, ref)


def _get_external(item: Any) -> dict[str, Any] | None:
    value = item.get("external") if isinstance(item, dict) else getattr(item, "external", None)
    return value if isinstance(value, dict) else None


def _set_external(item: Any, ref: dict[str, Any]) -> None:
    if isinstance(item, dict):
        item["external"] = ref
    else:
        setattr(item, "external", ref)


__all__ = [
    "DELIST_ACTORS",
    "ExternalStatusResolver",
    "LISTING_STATUSES",
    "STATUS_CACHE_TTL_SECONDS",
    "build_external_ref",
    "collect_external_refs",
    "origin_of",
]