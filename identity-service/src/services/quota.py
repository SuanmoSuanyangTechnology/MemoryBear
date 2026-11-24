"""Workspace-quota admission and usage snapshot for the management plane.

Wire-compatible port of the monolith ``quota_manager`` resolution chain, using
application-service's read-only mirror approach: the five premium quota tables
are read through a dedicated ``MetaData`` (no DDL, no ORM mapping); the ops
platform stays their write owner. When the tables are absent (community
deployment) the pgcode 42P01 error rolls back only a SAVEPOINT and the free
plan applies, leaving the caller's transaction and ORM state untouched.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    func,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from src.core.datetime_utils import utcnow_naive
from src.core.exceptions import QuotaExceededError
from src.models.workspace_model import Workspace

logger = logging.getLogger(__name__)

quota_metadata = MetaData()

tenant_subscriptions = Table(
    "tenant_subscriptions",
    quota_metadata,
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("tenant_id", PgUUID(as_uuid=True)),
    Column("package_plan_id", PgUUID(as_uuid=True)),
    Column("package_version", String),
    Column("status", String),
)

package_plans = Table(
    "package_plans",
    quota_metadata,
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("tier_level", Integer),
    Column("quotas", JSONB),
)

package_plan_versions = Table(
    "package_plan_versions",
    quota_metadata,
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("package_plan_id", PgUUID(as_uuid=True)),
    Column("version", String),
    Column("version_snapshot", JSONB),
)

resource_pack_versions = Table(
    "resource_pack_versions",
    quota_metadata,
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("version_snapshot", JSONB),
)

tenant_resource_packs = Table(
    "tenant_resource_packs",
    quota_metadata,
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("tenant_id", PgUUID(as_uuid=True)),
    Column("resource_pack_version_id", PgUUID(as_uuid=True)),
    Column("tier_id", PgUUID(as_uuid=True)),
    Column("quantity", Integer),
    Column("status", String),
    Column("expired_at", DateTime),
)

# Per-workspace metering: plan and pack limits apply per active workspace, so
# tenant-wide limits are multiplied by the active workspace count. Identity
# meters only workspace_quota today; the set keeps the host snapshot shape.
PER_WORKSPACE_QUOTA_KEYS = frozenset(
    {"app_quota", "knowledge_capacity_quota", "memory_engine_quota", "end_user_quota"}
)

_QUOTA_ENV_KEYS = [
    "workspace_quota",
    "skill_quota",
    "app_quota",
    "knowledge_capacity_quota",
    "memory_engine_quota",
    "end_user_quota",
    "ontology_project_quota",
    "model_quota",
    "api_ops_rate_limit",
    "end_user_memory_limit",
    "pre_user_memory_write_qps_limit",
]


def _quota_from_env() -> dict[str, int | float]:
    quotas: dict[str, int | float] = {}
    for key in _QUOTA_ENV_KEYS:
        env_value = os.getenv(f"QUOTA_{key.upper()}")
        if env_value is not None:
            try:
                quotas[key] = float(env_value) if "." in env_value else int(env_value)
            except ValueError:
                pass
    return quotas


def _build_default_free_quotas() -> dict[str, int | float]:
    quotas: dict[str, int | float] = {
        "workspace_quota": 1,
        "skill_quota": 5,
        "app_quota": 2,
        "knowledge_capacity_quota": 0.3,
        "memory_engine_quota": 1,
        "end_user_quota": 10,
        "ontology_project_quota": 3,
        "model_quota": 1,
        "api_ops_rate_limit": 50,
        "end_user_memory_limit": 300,
        "pre_user_memory_write_qps_limit": 50,
    }
    quotas.update(_quota_from_env())
    return quotas


DEFAULT_FREE_QUOTAS = _build_default_free_quotas()

# Flat-snapshot compatibility keys (older plan-version snapshots carry quota
# fields at the top level instead of nested under "quotas").
_QUOTA_KEYS = [
    "workspace_quota",
    "skill_quota",
    "app_quota",
    "knowledge_capacity_quota",
    "memory_engine_quota",
    "end_user_quota",
    "ontology_project_quota",
    "model_quota",
    "api_ops_rate_limit",
]


def free_quota_config() -> dict[str, int | float]:
    return DEFAULT_FREE_QUOTAS


def merge_quota_overlay(
    base: dict[str, Any] | None, overlay: dict[str, Any]
) -> dict[str, Any]:
    """Return a new quota mapping with every numeric resource-pack grant added."""

    merged: dict[str, Any] = dict(base or {})
    for key, value in overlay.items():
        merged[key] = (merged.get(key) or 0) + (value or 0)
    return merged


def get_active_subscription(db: Session, tenant_id: UUID):
    """Current subscription: status = active, highest tier_level first."""

    return db.execute(
        select(tenant_subscriptions)
        .join(
            package_plans,
            tenant_subscriptions.c.package_plan_id == package_plans.c.id,
        )
        .where(
            tenant_subscriptions.c.tenant_id == tenant_id,
            tenant_subscriptions.c.status == "active",
        )
        .order_by(package_plans.c.tier_level.desc())
        .limit(1)
    ).first()


def get_plan_version_snapshot(db: Session, package_plan_id: UUID, version: str):
    return (
        db.execute(
            select(package_plan_versions.c.version_snapshot)
            .where(
                package_plan_versions.c.package_plan_id == package_plan_id,
                package_plan_versions.c.version == version,
            )
            .limit(1)
        )
        .scalars()
        .first()
    )


def get_plan_quotas(db: Session, package_plan_id: UUID) -> dict[str, Any] | None:
    return (
        db.execute(
            select(package_plans.c.quotas)
            .where(package_plans.c.id == package_plan_id)
            .limit(1)
        )
        .scalars()
        .first()
    )


def get_resource_pack_rows(db: Session, tenant_id: UUID, now: datetime):
    """Active resource-pack grants with their unexpired quantities."""

    return db.execute(
        select(
            resource_pack_versions.c.version_snapshot,
            tenant_resource_packs.c.tier_id,
            tenant_resource_packs.c.quantity,
        )
        .join(
            tenant_resource_packs,
            tenant_resource_packs.c.resource_pack_version_id
            == resource_pack_versions.c.id,
        )
        .where(
            tenant_resource_packs.c.tenant_id == tenant_id,
            tenant_resource_packs.c.status == "active",
            or_(
                tenant_resource_packs.c.expired_at.is_(None),
                tenant_resource_packs.c.expired_at > now,
            ),
        )
    ).all()


def _get_quota_from_snapshot(db: Session, subscription) -> dict[str, Any]:
    snapshot = get_plan_version_snapshot(
        db, subscription.package_plan_id, subscription.package_version
    )
    if snapshot:
        if "quotas" in snapshot:
            quotas = snapshot["quotas"]
        else:
            quotas = {key: snapshot.get(key) for key in _QUOTA_KEYS}
    else:
        quotas = get_plan_quotas(db, subscription.package_plan_id) or {}
        knowledge_capacity = quotas.get("knowledge_capacity_quota")
        if knowledge_capacity is not None:
            quotas = {**quotas, "knowledge_capacity_quota": float(knowledge_capacity)}
    quotas.setdefault(
        "pre_user_memory_write_qps_limit",
        int(os.getenv("QUOTA_PRE_USER_MEMORY_WRITE_QPS_LIMIT", "100")),
    )
    quotas.setdefault(
        "end_user_memory_limit",
        int(os.getenv("QUOTA_END_USER_MEMORY_LIMIT", "600")),
    )
    return quotas


def _effective_quota(db: Session, tenant_id: UUID) -> dict[str, Any]:
    subscription = get_active_subscription(db, tenant_id)
    if not subscription:
        return {}
    return _get_quota_from_snapshot(db, subscription)


def _resource_pack_overlay(db: Session, tenant_id: UUID) -> dict[str, Any]:
    rows = get_resource_pack_rows(db, tenant_id, utcnow_naive())
    overlay: dict[str, Any] = {}
    for snapshot, tier_id, quantity in rows:
        selected = next(
            (
                tier
                for tier in ((snapshot or {}).get("tiers") or [])
                if str(tier.get("tier_id")) == str(tier_id)
            ),
            None,
        )
        if not selected:
            continue
        for key, value in (selected.get("quota_grants") or {}).items():
            overlay[key] = overlay.get(key, 0) + (value or 0) * quantity
    return overlay


def get_quota_breakdown(
    db: Session, tenant_id: UUID
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Return (plan quota, resource-pack quota) so callers can report each part."""

    try:
        # A missing premium schema rolls back only this SAVEPOINT; the caller's
        # transaction and its loaded ORM instances stay valid.
        with db.begin_nested():
            base = _effective_quota(db, tenant_id)
            overlay = _resource_pack_overlay(db, tenant_id)
        if not base:
            logger.debug(
                "Tenant %s has no subscription; using free-plan quotas", tenant_id
            )
            base = free_quota_config()
        return base, overlay
    except ProgrammingError as error:
        if getattr(error.orig, "pgcode", None) != "42P01":
            raise
        logger.warning(
            "Quota tables absent (community deployment); using free-plan quotas"
        )
        return free_quota_config(), {}


def get_quota_config(db: Session, tenant_id: UUID) -> dict[str, Any] | None:
    """Final quota config: effective plan (or free fallback) + active packs."""

    base, overlay = get_quota_breakdown(db, tenant_id)
    if not overlay:
        return dict(base) if base else base
    return merge_quota_overlay(base, overlay)


def count_workspaces(db: Session, tenant_id: UUID) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(Workspace)
            .where(
                Workspace.tenant_id == tenant_id,
                Workspace.is_active.is_(True),
            )
        ).scalar_one()
    )


def get_usage(
    db: Session, tenant_id: UUID, quota_type: str, workspace_id: UUID | None = None
) -> int | float:
    """Current usage for one quota type (identity meters workspace_quota only)."""

    if quota_type == "workspace_quota":
        return count_workspaces(db, tenant_id)
    return 0


def check_quota(
    db: Session,
    tenant_id: UUID,
    quota_type: str,
    resource_name: str,
    usage_func: Callable[..., int | float] | None = None,
    workspace_id: UUID | None = None,
) -> None:
    """Compare current usage against the limit and raise 402 when exhausted."""

    try:
        quota_config = get_quota_config(db, tenant_id)
        if not quota_config:
            logger.warning("Tenant %s has no quota config; skipping check", tenant_id)
            return

        quota_limit = quota_config.get(quota_type)
        if quota_limit is None:
            logger.warning("Quota config lacks %s; skipping check", quota_type)
            return

        if usage_func:
            current_usage = (
                usage_func(db, tenant_id, workspace_id)
                if workspace_id
                else usage_func(db, tenant_id)
            )
        else:
            current_usage = get_usage(db, tenant_id, quota_type, workspace_id)

        if current_usage >= quota_limit:
            logger.warning(
                "Quota exceeded: tenant=%s, workspace=%s, type=%s, usage=%s, limit=%s",
                tenant_id,
                workspace_id,
                quota_type,
                current_usage,
                quota_limit,
            )
            raise QuotaExceededError(
                resource=resource_name,
                current_usage=current_usage,
                quota_limit=quota_limit,
            )
    except QuotaExceededError:
        raise
    except Exception:
        logger.error(
            "Quota check failed: tenant=%s, workspace=%s, type=%s",
            tenant_id,
            workspace_id,
            quota_type,
            exc_info=True,
        )
        raise


def check_workspace_quota(db: Session, tenant_id: UUID) -> None:
    """Admission check before creating a workspace (host decorator body)."""

    check_quota(db, tenant_id, "workspace_quota", "workspace")


def get_quota_snapshot(
    db: Session,
    tenant_id: UUID,
    quota_type: str,
    workspace_id: UUID | None = None,
) -> dict[str, Any] | None:
    """Usage snapshot in the host shape, consumed by the alert task payload."""

    plan_quota, pack_quota = get_quota_breakdown(db, tenant_id)
    if not plan_quota and not pack_quota:
        return None

    per_workspace = quota_type in PER_WORKSPACE_QUOTA_KEYS
    scoped = per_workspace and workspace_id is not None

    plan_limit = (plan_quota or {}).get(quota_type)
    pack_limit = (pack_quota or {}).get(quota_type) or 0
    if plan_limit is None and not pack_limit:
        return None
    limit = (plan_limit or 0) + pack_limit

    used = get_usage(db, tenant_id, quota_type, workspace_id if scoped else None)
    if per_workspace and not scoped:
        # Tenant-wide fallback: limits scale with the active workspace count,
        # matching the host's aggregated usage view.
        workspace_count = count_workspaces(db, tenant_id)
        if workspace_count > 0:
            limit = limit * workspace_count

    percentage = round(used / limit * 100, 1) if limit else None
    return {
        "used": round(used, 2) if isinstance(used, float) else used,
        "limit": round(limit, 4) if isinstance(limit, float) else limit,
        "percentage": percentage,
        "unit": "GB" if quota_type == "knowledge_capacity_quota" else None,
        "scope": "workspace" if scoped else "tenant",
        "workspace_id": str(workspace_id) if scoped else None,
        "limit_source": {"plan": plan_limit, "resource_pack": pack_limit},
    }
