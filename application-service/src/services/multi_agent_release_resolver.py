"""Cluster sub-agent release resolution (subset of the host
``multi_agent_release_resolver``: save-time validation, runtime resolution —
sync and async, read-back display).

Sub-agent entry conventions (multi_agent_configs.sub_agents[*], current form):
- agent_id: the sub-agent's **app ID** (App.id); the identity is stable and
  does not change across releases.
- release_policy: current (follow the latest) | pinned (fixed release); a
  missing value is treated as pinned.
- release_id: set only for pinned, holding the fixed AppRelease.id; empty for
  current.

Legacy-form compatibility (no data migration needed): in historical data,
agent_id stores an AppRelease.id. Discriminate by lookup — try App.id first; a
hit means the current form; otherwise look up AppRelease.id, and a hit means
the legacy form:
- Legacy + pinned (or no release_policy) -> agent_id is the fixed release;
- Legacy + current -> agent_id is only an "anchor"; resolve it back to the
  owning app's current_release_id, falling back to the anchor on failure.
UUID spaces cannot collide, so this discrimination is safe.
"""

from __future__ import annotations

import inspect
import logging
import uuid
from typing import Any, NamedTuple

from ..errors import BizCode, BusinessException
from ..models import App, AppRelease

logger = logging.getLogger(__name__)

POLICY_CURRENT = "current"
POLICY_PINNED = "pinned"


def get_policy(entry: dict[str, Any]) -> str:
    """Release policy of an entry; missing / invalid values fall back to pinned (legacy data)."""
    policy = (entry or {}).get("release_policy")
    return POLICY_CURRENT if policy == POLICY_CURRENT else POLICY_PINNED


def _as_uuid(value: Any) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _label(entry: dict[str, Any]) -> str:
    return str((entry or {}).get("name") or (entry or {}).get("agent_id") or "未命名")


def _require_agent_id(entry: dict[str, Any]) -> uuid.UUID:
    agent_id = _as_uuid((entry or {}).get("agent_id"))
    if agent_id is None:
        raise BusinessException(
            f"子 Agent「{_label(entry)}」缺少有效 agent_id", BizCode.INVALID_PARAMETER
        )
    return agent_id


def _release_usable(app: App | None, release: AppRelease | None) -> bool:
    return bool(
        app and app.is_active and release and release.is_active and release.app_id == app.id
    )


def _check_release(app: App | None, release: AppRelease | None, label: str) -> None:
    """Validate that the release is usable: the app exists and is active, and
    the release exists, is active, and belongs to the app.
    """
    if not app or not app.is_active:
        raise BusinessException(f"子 Agent「{label}」的应用不存在或已停用", BizCode.APP_NOT_FOUND)
    if not release or not release.is_active or release.app_id != app.id:
        raise BusinessException(
            f"子 Agent「{label}」的发布版本不存在、已下线或归属错误", BizCode.RELEASE_NOT_FOUND
        )


class _Plan(NamedTuple):
    """Resolution plan: ``fixed`` is directly usable; otherwise the app's
    latest release must be looked up.
    """

    fixed: uuid.UUID | None = None
    app: App | None = None
    fallback: uuid.UUID | None = None  # Only the legacy current form has an "anchor" for fallback


def _locate(db: Any, agent_id: uuid.UUID) -> tuple[App | None, AppRelease | None]:
    """Synchronous lookup: returns (app, legacy_release).

    A non-null legacy_release means the legacy form (agent_id is a release ID).
    """
    app = db.get(App, agent_id)
    if app is not None:
        return app, None
    release = db.get(AppRelease, agent_id)
    if release is not None:
        return db.get(App, release.app_id), release
    return None, None


async def _alocate(get, agent_id: uuid.UUID) -> tuple[App | None, AppRelease | None]:
    """Async lookup through an awaited getter (``get`` returns an awaitable)."""
    app = await get(App, agent_id)
    if app is not None:
        return app, None
    release = await get(AppRelease, agent_id)
    if release is not None:
        return await get(App, release.app_id), release
    return None, None


def _plan(
    entry: dict[str, Any],
    agent_id: uuid.UUID,
    app: App | None,
    legacy: AppRelease | None,
) -> _Plan:
    pinned = get_policy(entry) == POLICY_PINNED
    release_id = _as_uuid(entry.get("release_id"))

    if legacy is not None:  # Legacy form: agent_id is a release ID
        if pinned:
            return _Plan(fixed=agent_id)
        return _Plan(app=app, fallback=agent_id)

    if app is None:  # Neither ID resolves
        if pinned:
            # Keep the old behavior: let downstream loading report "release not found"
            return _Plan(fixed=release_id or agent_id)
        raise BusinessException(f"子 Agent「{_label(entry)}」的应用不存在", BizCode.APP_NOT_FOUND)

    if pinned and release_id is not None:
        return _Plan(fixed=release_id)
    return _Plan(app=app)  # current, or pinned without release_id (dirty data) -> use the latest


def _latest_or_fail(
    plan: _Plan, latest: AppRelease | None, entry: dict[str, Any], strict: bool
) -> uuid.UUID:
    if _release_usable(plan.app, latest):
        return latest.id  # type: ignore[union-attr]
    if strict or plan.fallback is None:
        raise BusinessException(
            f"子 Agent「{_label(entry)}」当前没有可用的发布版本", BizCode.APP_NOT_PUBLISHED
        )
    logger.warning(
        f"子 Agent 跟随最新解析失败，回退锚点版本: {_label(entry)} anchor={plan.fallback}"
    )
    return plan.fallback


def resolve_release_for_save(
    db: Any,
    app_id: uuid.UUID,
    release_policy: str,
    release_id: uuid.UUID | None,
    label: str = "",
) -> AppRelease:
    """Validate a sub-agent on cluster-config save and return its target release.

    current -> the app's current release (error if unpublished); pinned ->
    validate release_id ownership and return it. The caller uses this to decide
    the stored release_id (only pinned is stored); agent_id always remains the
    app ID.
    """
    app = db.get(App, app_id)
    if release_policy == POLICY_PINNED:
        target_id = _as_uuid(release_id)
        if target_id is None:
            raise BusinessException(
                f"子 Agent「{label}」固定版本缺少 release_id", BizCode.INVALID_PARAMETER
            )
    else:
        if not app or not app.current_release_id:
            raise BusinessException(
                f"子 Agent「{label}」未发布或不存在", BizCode.APP_NOT_PUBLISHED
            )
        target_id = app.current_release_id
    release = db.get(AppRelease, target_id)
    _check_release(app, release, label)
    return release


def resolve_effective_release_id(
    db: Any, entry: dict[str, Any], *, strict: bool = True
) -> uuid.UUID:
    """Synchronous: the release ID the entry should currently use.

    pinned -> fixed release; current -> the app's current_release_id.
    strict=False (check path): a failed legacy current resolution falls back to
    the anchor, so a single offline release does not break the whole cluster;
    the current form has no anchor, so an unpublished / offline app still
    raises.
    strict=True (publish time): any failure raises directly.
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


async def aresolve_effective_release_id(
    db: Any, entry: dict[str, Any], *, strict: bool = False
) -> uuid.UUID:
    """Async variant; db may be a Session or AsyncSession (awaited when ``db.get``
    returns an awaitable).
    """

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


# ───────────────────────── Read-back display ─────────────────────────


def describe_release_state(db: Any, entry: dict[str, Any]) -> dict[str, Any]:
    """For read-back: returns the app ID, policy, pinned release, current
    release, and whether a newer release exists.

    When resolution fails (app/release deleted, etc.) fields are left empty
    instead of raising, so the config-read endpoint does not fail as a whole.
    """
    policy = get_policy(entry)
    agent_id = _as_uuid(entry.get("agent_id"))
    state: dict[str, Any] = {
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

    pinned_id: uuid.UUID | None = None
    if policy == POLICY_PINNED:
        pinned_id = agent_id if legacy is not None else _as_uuid(entry.get("release_id"))
        state["release_id"] = str(pinned_id) if pinned_id else None

    if app.current_release_id:
        state["current_release_id"] = str(app.current_release_id)
        if pinned_id is not None and app.current_release_id != pinned_id:
            state["has_newer_release"] = True
    return state


__all__ = [
    "POLICY_CURRENT",
    "POLICY_PINNED",
    "aresolve_effective_release_id",
    "describe_release_state",
    "get_policy",
    "resolve_effective_release_id",
    "resolve_release_for_save",
]
