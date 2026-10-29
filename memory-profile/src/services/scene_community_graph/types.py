from dataclasses import dataclass
from uuid import UUID

from src.common.errors import BizCode, LocalizedProfileError


@dataclass(frozen=True)
class AuthorizedUser:
    workspace_id: UUID
    end_user_id: UUID


@dataclass(frozen=True)
class Options:
    enabled: bool = False
    cache_enabled: bool = True
    cache_ttl: int = 60
    max_communities: int = 1000
    max_scenes: int = 5000
    max_bytes: int = 10 * 1024 * 1024
    read_timeout: float = 5.0
    db_timeout: float = 3.0
    cache_timeout: float = 0.2

    def __post_init__(self):
        for name in (
            "cache_ttl",
            "max_communities",
            "max_scenes",
            "max_bytes",
            "read_timeout",
            "db_timeout",
            "cache_timeout",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")


def not_found() -> LocalizedProfileError:
    return LocalizedProfileError(
        BizCode.USER_NOT_FOUND,
        "analytics.scene_graph.user_not_found",
        detail_key="analytics.scene_graph.user_not_found",
        status_code=200,
    )


def bad_data() -> LocalizedProfileError:
    return LocalizedProfileError(
        BizCode.DB_ERROR,
        "analytics.scene_graph.bad_data",
        detail_key="analytics.scene_graph.bad_data",
        status_code=500,
    )


def capacity_error() -> LocalizedProfileError:
    return LocalizedProfileError(
        BizCode.INVALID_PARAMETER,
        "analytics.scene_graph.capacity_exceeded",
        detail_key="analytics.scene_graph.capacity_exceeded",
        status_code=400,
    )


def unavailable() -> LocalizedProfileError:
    return LocalizedProfileError(
        BizCode.SERVICE_UNAVAILABLE,
        "analytics.scene_graph.unavailable",
        detail_key="analytics.scene_graph.unavailable",
        status_code=503,
    )
