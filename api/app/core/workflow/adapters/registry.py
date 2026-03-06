# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/25 14:19
from typing import Any

from app.core.workflow.adapters import DifyAdapter, MemoryBearAdapter
from app.core.workflow.adapters.base_adapter import BasePlatformAdapter, PlatformType


class PlatformAdapterRegistry:
    _adapters: dict[str, type[BasePlatformAdapter]] = {}

    @classmethod
    def register(cls, platform: str, adapter: type[BasePlatformAdapter]):
        cls._adapters[platform] = adapter

    @classmethod
    def get_adapter(cls, platform: str, config: dict[str, Any]) -> BasePlatformAdapter:
        if platform not in cls._adapters:
            raise ValueError(f"Unsupported platform: {platform}")
        return cls._adapters.get(platform)(config)

    @classmethod
    def list_platforms(cls) -> list[str]:
        return list(cls._adapters.keys())

    @classmethod
    def is_supported(cls, platform: str) -> bool:
        return platform in cls._adapters


PlatformAdapterRegistry.register(PlatformType.MEMORY_BEAR, MemoryBearAdapter)
PlatformAdapterRegistry.register(PlatformType.DIFY, DifyAdapter)
