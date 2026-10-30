"""Core 与企业版共享的只读策略契约，不依赖数据库模型。"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Protocol


DEFAULT_EMOTION_POLICY_PLUGIN = "memory.default_emotion_policy"


@dataclass(frozen=True)
class EmotionPolicy:
    tone: str
    do_list: tuple[str, ...] = ()
    avoid_list: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "do_list", tuple(self.do_list))
        object.__setattr__(self, "avoid_list", tuple(self.avoid_list))


@dataclass(frozen=True)
class DefaultPolicySnapshot:
    policies: Mapping[str, EmotionPolicy]

    def __post_init__(self) -> None:
        # 脱离调用方字典，避免一个请求意外修改整个进程的缓存。
        object.__setattr__(self, "policies", MappingProxyType(dict(self.policies)))


class DefaultPolicyLoadError(Exception):
    """策略读取失败；只携带可安全记录的故障类别。"""

    def __init__(self, fault_type: str):
        self.fault_type = fault_type
        super().__init__(fault_type)


class DefaultEmotionPolicyProvider(Protocol):
    async def get_default_policy_snapshot(self) -> DefaultPolicySnapshot:
        """返回完整快照；失败时抛 DefaultPolicyLoadError，不缓存失败。"""
        ...
