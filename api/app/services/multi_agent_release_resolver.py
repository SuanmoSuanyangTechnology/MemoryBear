"""集群子 Agent 版本解析。

子 Agent 条目（multi_agent_configs.sub_agents[*]）的约定（新形态）：
- agent_id：子 Agent 的**应用 ID**（App.id），身份稳定，不随发布变化。
- release_policy：current（跟随最新）| pinned（固定版本）；缺失按 pinned 处理。
- release_id：仅 pinned 有值，固定的 AppRelease.id；current 时为空。
- 运行时内部（orchestrator.sub_agents 的键、路由、执行记录）仍以"有效 release ID"为键，
  由本模块解析：current → App.current_release_id；pinned → release_id。

旧形态兼容（无需数据迁移）：历史数据里 agent_id 存的是 AppRelease.id。
按值判别——先按 App.id 查，命中即新形态；否则按 AppRelease.id 查，命中即旧形态：
- 旧形态 + pinned（或无 release_policy）→ agent_id 就是固定 release；
- 旧形态 + current → agent_id 只是"锚点"，反查所属应用的 current_release_id，失败可回退锚点。
UUID 空间不会冲突，所以这种判别是安全的。
"""
from __future__ import annotations

import inspect
import uuid
from typing import Any, Dict, NamedTuple, Optional, Tuple

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.models import App, AppRelease

logger = get_business_logger()

POLICY_CURRENT = "current"
POLICY_PINNED = "pinned"


def get_policy(entry: Dict[str, Any]) -> str:
    """条目的版本策略；缺失/非法值按 pinned（兼容存量）。"""
    policy = (entry or {}).get("release_policy")
    return POLICY_CURRENT if policy == POLICY_CURRENT else POLICY_PINNED


def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _label(entry: Dict[str, Any]) -> str:
    return str((entry or {}).get("name") or (entry or {}).get("agent_id") or "未命名")


def _require_agent_id(entry: Dict[str, Any]) -> uuid.UUID:
    agent_id = _as_uuid((entry or {}).get("agent_id"))
    if agent_id is None:
        raise BusinessException(f"子 Agent「{_label(entry)}」缺少有效 agent_id", BizCode.INVALID_PARAMETER)
    return agent_id


def _check_release(app: Optional[App], release: Optional[AppRelease], label: str) -> None:
    """校验 release 可用：应用存在且启用、release 存在/启用/归属该应用。"""
    if not app or not app.is_active:
        raise BusinessException(f"子 Agent「{label}」的应用不存在或已停用", BizCode.APP_NOT_FOUND)
    if not release or not release.is_active or release.app_id != app.id:
        raise BusinessException(
            f"子 Agent「{label}」的发布版本不存在、已下线或归属错误", BizCode.RELEASE_NOT_FOUND
        )


def _release_usable(app: Optional[App], release: Optional[AppRelease]) -> bool:
    return bool(app and app.is_active and release and release.is_active and release.app_id == app.id)


# ───────────────────────── 保存时（同步 Session） ─────────────────────────


def resolve_release_for_save(
    db: Any,
    app_id: uuid.UUID,
    release_policy: str,
    release_id: Optional[uuid.UUID],
    label: str = "",
) -> AppRelease:
    """保存集群配置时校验子 Agent 并返回其目标 release。

    current → 应用当前发布版本（未发布则报错）；pinned → 校验 release_id 归属后返回。
    调用方据此决定落库的 release_id（pinned 才落库）；agent_id 始终保持应用 ID。
    """
    app = db.get(App, app_id)
    if release_policy == POLICY_PINNED:
        target_id = _as_uuid(release_id)
        if target_id is None:
            raise BusinessException(f"子 Agent「{label}」固定版本缺少 release_id", BizCode.INVALID_PARAMETER)
    else:
        if not app or not app.current_release_id:
            raise BusinessException(
                f"子 Agent「{label}」未发布或不存在", BizCode.APP_NOT_PUBLISHED
            )
        target_id = app.current_release_id
    release = db.get(AppRelease, target_id)
    _check_release(app, release, label)
    return release


# ───────────────────────── 形态判别 + 解析计划 ─────────────────────────


class _Plan(NamedTuple):
    """解析计划：fixed 直接可用；否则需要查 app 的最新发布版本。"""

    fixed: Optional[uuid.UUID] = None
    app: Optional[App] = None
    fallback: Optional[uuid.UUID] = None  # 仅旧形态 current 有"锚点"可回退


def _locate(db: Any, agent_id: uuid.UUID) -> Tuple[Optional[App], Optional[AppRelease]]:
    """同步：返回 (app, legacy_release)。legacy_release 非空表示旧形态（agent_id 是 release ID）。"""
    app = db.get(App, agent_id)
    if app is not None:
        return app, None
    release = db.get(AppRelease, agent_id)
    if release is not None:
        return db.get(App, release.app_id), release
    return None, None


async def _alocate(get, agent_id: uuid.UUID) -> Tuple[Optional[App], Optional[AppRelease]]:
    app = await get(App, agent_id)
    if app is not None:
        return app, None
    release = await get(AppRelease, agent_id)
    if release is not None:
        return await get(App, release.app_id), release
    return None, None


def _plan(
    entry: Dict[str, Any],
    agent_id: uuid.UUID,
    app: Optional[App],
    legacy: Optional[AppRelease],
) -> _Plan:
    pinned = get_policy(entry) == POLICY_PINNED
    release_id = _as_uuid(entry.get("release_id"))

    if legacy is not None:  # 旧形态：agent_id 是 release ID
        if pinned:
            return _Plan(fixed=agent_id)
        return _Plan(app=app, fallback=agent_id)

    if app is None:  # 两种 ID 都查不到
        if pinned:
            # 沿用旧行为：交给下游加载时报"release 不存在"
            return _Plan(fixed=release_id or agent_id)
        raise BusinessException(f"子 Agent「{_label(entry)}」的应用不存在", BizCode.APP_NOT_FOUND)

    if pinned and release_id is not None:
        return _Plan(fixed=release_id)
    return _Plan(app=app)  # current，或 pinned 却缺 release_id（脏数据）→ 取最新


def _latest_or_fail(
    plan: _Plan, latest: Optional[AppRelease], entry: Dict[str, Any], strict: bool
) -> uuid.UUID:
    if _release_usable(plan.app, latest):
        return latest.id  # type: ignore[union-attr]
    if strict or plan.fallback is None:
        raise BusinessException(
            f"子 Agent「{_label(entry)}」当前没有可用的发布版本", BizCode.APP_NOT_PUBLISHED
        )
    logger.warning(f"子 Agent 跟随最新解析失败，回退锚点版本: {_label(entry)} anchor={plan.fallback}")
    return plan.fallback


# ───────────────────────── 运行时 / 发布时 ─────────────────────────


def resolve_effective_release_id(db: Any, entry: Dict[str, Any], *, strict: bool = True) -> uuid.UUID:
    """同步：条目当前应使用的 release ID。

    pinned → 固定 release；current → 应用 current_release_id。
    strict=False（运行时）：旧形态 current 解析失败回退锚点，不让一次下线拖垮整个集群；
    新形态 current 没有锚点，应用未发布/下线时只能报错。
    strict=True（发布时）：失败直接报错。
    """
    agent_id = _require_agent_id(entry)
    app, legacy = _locate(db, agent_id)
    plan = _plan(entry, agent_id, app, legacy)
    if plan.fixed is not None:
        return plan.fixed
    latest = None
    if plan.app is not None and plan.app.is_active and plan.app.current_release_id:
        latest = db.get(AppRelease, plan.app.current_release_id)
    return _latest_or_fail(plan, latest, entry, strict)


async def aresolve_effective_release_id(db: Any, entry: Dict[str, Any], *, strict: bool = False) -> uuid.UUID:
    """异步版本，db 可为 Session 或 AsyncSession（db.get 返回 awaitable 即 await）。"""

    async def _get(model, ident):
        result = db.get(model, ident)
        return await result if inspect.isawaitable(result) else result

    agent_id = _require_agent_id(entry)
    app, legacy = await _alocate(_get, agent_id)
    plan = _plan(entry, agent_id, app, legacy)
    if plan.fixed is not None:
        return plan.fixed
    latest = None
    if plan.app is not None and plan.app.is_active and plan.app.current_release_id:
        latest = await _get(AppRelease, plan.app.current_release_id)
    return _latest_or_fail(plan, latest, entry, strict)


# ───────────────────────── 回读展示 ─────────────────────────


def describe_release_state(db: Any, entry: Dict[str, Any]) -> Dict[str, Any]:
    """回读用：返回应用 ID、策略、固定版本、当前版本及是否落后。

    解析不到（应用/release 被删等）时字段尽量留空，不抛错，避免读配置接口整体失败。
    """
    policy = get_policy(entry)
    agent_id = _as_uuid(entry.get("agent_id"))
    state: Dict[str, Any] = {
        "release_policy": policy,
        "release_id": None,
        "app_id": None,
        "current_release_id": None,
        "has_newer_release": False,
    }
    if agent_id is None:
        return state

    app, legacy = _locate(db, agent_id)
    if legacy is not None:
        state["app_id"] = str(legacy.app_id)
    if app is None:
        return state
    state["app_id"] = str(app.id)

    pinned_id: Optional[uuid.UUID] = None
    if policy == POLICY_PINNED:
        pinned_id = agent_id if legacy is not None else _as_uuid(entry.get("release_id"))
        state["release_id"] = str(pinned_id) if pinned_id else None

    if app.current_release_id:
        state["current_release_id"] = str(app.current_release_id)
        if pinned_id is not None and app.current_release_id != pinned_id:
            state["has_newer_release"] = True
    return state
