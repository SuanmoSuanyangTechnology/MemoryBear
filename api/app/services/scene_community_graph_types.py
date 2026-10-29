from dataclasses import dataclass
from uuid import UUID

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException


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
    max_merge_hops: int = 8

    def __post_init__(self):
        for name in (
            "cache_ttl", "max_communities", "max_scenes", "max_bytes",
            "read_timeout", "db_timeout", "cache_timeout", "max_merge_hops",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")


def not_found() -> BusinessException:
    return BusinessException("用户不存在", BizCode.USER_NOT_FOUND)


def bad_data() -> BusinessException:
    return BusinessException("场景社区数据异常", BizCode.DB_ERROR)


def capacity_error() -> BusinessException:
    return BusinessException("场景社区图谱数据量超过完整响应上限", BizCode.INVALID_PARAMETER)
