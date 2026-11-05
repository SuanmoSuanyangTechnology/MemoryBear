"""The community free plan, owned by memory-profile.

Only the limits this service actually asks for are listed. The values keep the
API's historical names (``QUOTA_*``) so one deployment variable drives every
service, and they are read through ``ProfileSettings`` so the bootstrap env
files apply.
"""

from __future__ import annotations

from src.config import ProfileSettings

#: The three limits the quota endpoints resolve. Other keys belong to the API.
MEMORY_LIMIT_KEYS: tuple[str, ...] = (
    "memory_engine_quota",
    "end_user_memory_limit",
    "pre_user_memory_write_qps_limit",
)


def free_plan_limits(settings: ProfileSettings) -> dict[str, int]:
    """The community free plan in the enterprise endpoint's flat shape."""

    return {
        "memory_engine_quota": settings.quota_memory_engine_quota,
        "end_user_memory_limit": settings.quota_end_user_memory_limit,
        "pre_user_memory_write_qps_limit": settings.quota_pre_user_memory_write_qps_limit,
    }
