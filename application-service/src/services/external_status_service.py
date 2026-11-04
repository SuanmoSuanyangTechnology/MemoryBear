"""External（市场来源）状态回填（设计文档 §11.2 / P1-7）.

主库只记录「这个本地副本来自市场的哪个资源、哪个版本」——`origin` /
`external_resource_id` / `external_version`。**市场状态不落库**：它是市场的
真相，落一份到主库立刻就有两个可能不同的值。

所以列表/详情默认返回 `external.status = null`，只有请求带
`include_external_status=true` 时才批量去问一次。三条约束：

1. **一次批量，不 N+1**：按 `resource_type` 分组，每组一次调用（他的上限 100 个）。
2. **缓存 30 秒**：状态不是强一致数据，运营刚点通过就刷新列表不该打出一次 HTTP。
3. **失败整批降级 `unknown`**：市场挂了不能让「我的应用列表」跟着 500，这是
   列表页最重要的可用性约束（他的 §1.1「查询失败时整批降级为 unknown，列表
   正常返回」）。
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Callable, Iterable

from ..protocols.market_gateway import MarketGateway, MarketUnavailableError

logger = logging.getLogger(__name__)

STATUS_CACHE_TTL_SECONDS = 30
STATUS_CACHE_NAMESPACE = "cache:external-status:v1"
STATUS_BATCH_LIMIT = 100

UNKNOWN = "unknown"

# 已安装副本只可能是这三种（他的 §1.1 状态口径）
INSTALLED_STATUSES = frozenset({"published", "unlisted", UNKNOWN})


def build_external_ref(
        *,
        resource_id: Any,
        version: Any,
        status: str | None = None,
) -> dict[str, Any]:
    """`ExternalRef`（他的 §1.1）。`source` 本期固定 `community_market`。"""

    return {
        "source": "community_market",
        "resource_id": str(resource_id) if resource_id else None,
        "version": version,
        "status": status,
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
        """就地回填 `refs` 里每个 `ExternalRef.status`；不可用时全部 `unknown`。"""

        live = [ref for ref in refs if ref and ref.get("resource_id") and not ref.get("status")]
        if not live:
            return
        statuses = self._resolve(
            [str(ref["resource_id"]) for ref in live],
            resource_type=resource_type,
            tenant_id=tenant_id,
        )
        for ref in live:
            ref["status"] = statuses.get(str(ref["resource_id"]), UNKNOWN)

    # ==================== 内部 ====================

    def _resolve(
            self,
            resource_ids: list[str],
            *,
            resource_type: str,
            tenant_id: uuid.UUID,
    ) -> dict[str, str]:
        resolved: dict[str, str] = {}
        missing: list[str] = []

        for resource_id in dict.fromkeys(resource_ids):
            cached = self._cache_get(tenant_id, resource_type, resource_id)
            if cached is None:
                missing.append(resource_id)
            else:
                resolved[resource_id] = cached

        if not missing:
            return resolved

        if not self.market.enabled:
            # 社区版没有市场：来源信息仍然要能展示，状态就是 unknown
            for resource_id in missing:
                resolved[resource_id] = UNKNOWN
            return resolved

        for start in range(0, len(missing), STATUS_BATCH_LIMIT):
            chunk = missing[start:start + STATUS_BATCH_LIMIT]
            try:
                items = self.market.get_publication_status(
                    tenant_id=str(tenant_id),
                    resource_type=resource_type,
                    local_resource_ids=chunk,
                )
            except MarketUnavailableError as error:
                logger.warning(
                    "市场状态查询失败，整批降级 unknown",
                    extra={"resource_type": resource_type, "error": str(error)[:200]},
                )
                for resource_id in chunk:
                    resolved[resource_id] = UNKNOWN
                continue
            resolved.update(_map_statuses(items, chunk, resolved))
            for resource_id, status in resolved.items():
                if status != UNKNOWN:
                    self._cache_set(tenant_id, resource_type, resource_id, status)

        for resource_id in missing:
            resolved.setdefault(resource_id, UNKNOWN)
        return resolved

    def _cache_key(self, tenant_id: uuid.UUID, resource_type: str, resource_id: str) -> str:
        return f"{STATUS_CACHE_NAMESPACE}:{tenant_id}:{resource_type}:{resource_id}"

    def _cache_get(
            self, tenant_id: uuid.UUID, resource_type: str, resource_id: str
    ) -> str | None:
        if self.cache is None:
            return None
        try:
            raw = self.cache.get(self._cache_key(tenant_id, resource_type, resource_id))
        except Exception:  # noqa: BLE001 - 缓存挂了不该影响列表
            return None
        return raw.decode() if isinstance(raw, bytes) else raw

    def _cache_set(
            self, tenant_id: uuid.UUID, resource_type: str, resource_id: str, status: str
    ) -> None:
        if self.cache is None:
            return
        try:
            self.cache.setex(
                self._cache_key(tenant_id, resource_type, resource_id),
                STATUS_CACHE_TTL_SECONDS,
                status,
            )
        except Exception:  # noqa: BLE001
            logger.debug("外部状态缓存写入失败", exc_info=True)


def _map_statuses(
        items: Any, requested: list[str], already: dict[str, str]
) -> dict[str, str]:
    """他的批量响应是 `{items: [{local_resource_id, status, ...}]}`。

    `local_resource_id` 在这里就是我们传进去的市场资源 ID 列表位置——为兼容他
    两种写法（回传 `local_resource_id` 或 `resource_id`），两个键都认。
    """

    looked_up: dict[str, str] = {}
    records = items.get("items") if isinstance(items, dict) else items
    for record in records or []:
        if not isinstance(record, dict):
            continue
        key = record.get("local_resource_id") or record.get("resource_id")
        if not key:
            continue
        looked_up[str(key)] = str(record.get("status") or UNKNOWN)
    return {resource_id: looked_up.get(resource_id, UNKNOWN) for resource_id in requested}


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
    """就地回填一批已序列化条目的 `external.status`。

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
    "ExternalStatusResolver",
    "INSTALLED_STATUSES",
    "STATUS_CACHE_TTL_SECONDS",
    "UNKNOWN",
    "build_external_ref",
    "collect_external_refs",
    "origin_of",
]