"""Community fallback quotas: wire-compatible port of the monolith's
``app/config/default_free_plan.py`` quotas block (values and QUOTA_<KEY>
environment overrides). Used when a tenant has no active subscription, or when
the premium quota tables are absent (community deployment).
"""

from __future__ import annotations

import os

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


def free_quota_config() -> dict[str, int | float]:
    return DEFAULT_FREE_QUOTAS


__all__ = ["DEFAULT_FREE_QUOTAS", "free_quota_config"]
