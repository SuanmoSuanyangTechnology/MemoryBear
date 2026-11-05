"""Quota entitlement and admission owned by memory-profile.

The community build keeps its free-plan defaults here; an enterprise deployment
reads the effective limits from ``memory-enterprise`` over HTTP. Either way this
package is the single place the three memory limits are resolved.
"""

from src.services.quota.admission import (
    QUOTA_TYPE,
    RESOURCE_KEY,
    RESOURCE_NAME,
    AdmissionOutcome,
    admit_memory_engine,
    check_memory_engine_quota,
    count_memory_engines,
)
from src.services.quota.defaults import MEMORY_LIMIT_KEYS, free_plan_limits
from src.services.quota.entitlements import QuotaEntitlements
from src.services.quota.errors import QuotaDenied

__all__ = [
    "MEMORY_LIMIT_KEYS",
    "QUOTA_TYPE",
    "RESOURCE_KEY",
    "RESOURCE_NAME",
    "AdmissionOutcome",
    "QuotaDenied",
    "QuotaEntitlements",
    "admit_memory_engine",
    "check_memory_engine_quota",
    "count_memory_engines",
    "free_plan_limits",
]
