"""Effective quota configuration: subscription snapshot + resource-pack overlay.

Wire-compatible port of the monolith's ``quota_manager`` resolution chain:
active subscription (highest tier) -> version snapshot (tier-format compatibility
included) -> plan-row fallback -> free plan -> active resource-pack grants summed
on top. The premium tables are read through the cross-domain mirror; when they
are absent (community deployment) the pgcode 42P01 error rolls back only a
SAVEPOINT and falls back to the free plan, leaving the caller's transaction and
loaded ORM instances untouched.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from uuid import UUID

from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from ..repositories import cross_domain_read
from ..utils.datetime_utils import utcnow_naive
from .free_plan import free_quota_config

logger = logging.getLogger(__name__)

# Flat-snapshot compatibility keys (older version snapshots carry quota fields
# at the top level instead of nested under "quotas").
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


def merge_quota_overlay(
    base: dict[str, Any] | None, overlay: dict[str, Any]
) -> dict[str, Any]:
    """Return a new quota mapping with every numeric resource-pack grant added."""

    merged: dict[str, Any] = dict(base or {})
    for key, value in overlay.items():
        merged[key] = (merged.get(key) or 0) + (value or 0)
    return merged


def _get_quota_from_snapshot(db: Session, subscription: Any) -> dict[str, Any]:
    snapshot = cross_domain_read.get_plan_version_snapshot(
        db, subscription.package_plan_id, subscription.package_version
    )
    if snapshot:
        if "quotas" in snapshot:
            quotas = snapshot["quotas"]
        else:
            quotas = {key: snapshot.get(key) for key in _QUOTA_KEYS}
    else:
        quotas = cross_domain_read.get_plan_quotas(db, subscription.package_plan_id) or {}
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
    subscription = cross_domain_read.get_active_subscription(db, tenant_id)
    if not subscription:
        return {}
    return _get_quota_from_snapshot(db, subscription)


def _resource_pack_overlay(db: Session, tenant_id: UUID) -> dict[str, Any]:
    rows = cross_domain_read.get_resource_pack_rows(db, tenant_id, utcnow_naive())
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
            logger.debug("Tenant %s has no subscription; using free-plan quotas", tenant_id)
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


__all__ = ["get_quota_breakdown", "get_quota_config", "merge_quota_overlay"]
